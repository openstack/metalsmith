# Copyright 2015-2017 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import json
import logging
import os
import shutil
import tempfile

from ironicclient import exc as ir_exc
from oslo_utils import excutils

from metalsmith import os_api


LOG = logging.getLogger(__name__)


def _log_node(node):
    if node.name:
        return '%s (UUID %s)' % (node.name, node.uuid)
    else:
        return node.uuid


def _get_capabilities(node):
    return dict(x.split(':', 1) for x in
                node.properties.get('capabilities', '').split(',') if x)


@contextlib.contextmanager
def _config_drive_dir(node, ssh_keys):
    d = tempfile.mkdtemp()
    try:
        metadata = {'public_keys': ssh_keys,
                    'uuid': node.uuid,
                    'name': node.name,
                    'hostname': node.name or node.uuid,
                    'launch_index': 0,
                    'availability_zone': '',
                    'files': [],
                    'meta': {}}
        for version in ('2012-08-10', 'latest'):
            subdir = os.path.join(d, 'openstack', version)
            if not os.path.exists(subdir):
                os.makedirs(subdir)

            with open(os.path.join(subdir, 'meta_data.json'), 'w') as fp:
                json.dump(metadata, fp)

        yield d
    finally:
        shutil.rmtree(d)


def reserve(api, nodes, capabilities, dry_run=False):
    suitable_nodes = []
    for node in nodes:
        caps = _get_capabilities(node)
        LOG.debug('Capabilities for node %(node)s: %(cap)s',
                  {'node': _log_node(node), 'cap': caps})
        for key, value in capabilities.items():
            if caps.get(key) != value:
                break
        else:
            suitable_nodes.append(node)

    if not suitable_nodes:
        raise RuntimeError('No nodes found with capabilities %s' %
                           capabilities)

    for node in suitable_nodes:
        try:
            api.validate_node(node.uuid)
        except RuntimeError as exc:
            LOG.warning('Node %(node)s failed validation: %(err)s',
                        {'node': _log_node(node), 'err': exc})
            continue

        if not node.properties.get('local_gb'):
            LOG.warning('No local_gb for node %s', _log_node(node))
            continue

        if dry_run:
            LOG.debug('Dry run, assuming node %s reserved', _log_node(node))
            return node
        else:
            try:
                return api.update_node(node.uuid, instance_uuid=node.uuid)
            except ir_exc.Conflict:
                LOG.info('Node %s was occupied, proceeding with the next',
                         _log_node(node))

    raise RuntimeError('Unable to reserve any node')


def clean_up(api, node_uuid, neutron_ports):
    try:
        api.update_node(node_uuid, instance_uuid=os_api.REMOVE)
    except Exception:
        LOG.warning('Failed to remove instance_uuid, assuming already removed')

    for port_id in neutron_ports:
        LOG.debug('Detaching port %(port)s from node %(node)s',
                  {'port': port_id, 'node': node_uuid})
        try:
            api.detach_port_from_node(node_uuid, port_id)
        except Exception:
            LOG.warning('Failed to remove VIF %(vif)s from node %(node)s, '
                        'assuming already removed',
                        {'vif': port_id, 'node': node_uuid})

        LOG.debug('Deleting port %s', port_id)
        try:
            api.delete_port(port_id)
        except Exception:
            LOG.warning('Failed to delete neutron port %s', port_id)


def provision(api, node, network, image, root_disk_size=None,
              ssh_keys=None, netboot=False, wait=None):
    neutron_ports = []
    target_caps = {'boot_option': 'netboot' if netboot else 'local'}

    try:
        if root_disk_size is None:
            root_disk_size = node.properties.get('local_gb')
            if not root_disk_size:
                raise RuntimeError('No root disk size requested and local_gb '
                                   'is empty')
            # allow for partitioning and config drive
            root_disk_size = int(root_disk_size) - 2

        updates = {'/instance_info/ramdisk': image.ramdisk_id,
                   '/instance_info/kernel': image.kernel_id,
                   '/instance_info/image_source': image.id,
                   '/instance_info/root_gb': root_disk_size,
                   '/instance_info/capabilities': target_caps}
        node = api.update_node(node.uuid, updates)

        node_ports = api.list_node_ports(node.uuid)
        for node_port in node_ports:
            port = api.create_port(mac_address=node_port.address,
                                   network_id=network.id)
            neutron_ports.append(port.id)
            LOG.debug('Created Neutron port %s', port)

            api.attach_port_to_node(node.uuid, port.id)
            LOG.info('Ironic port %(node_port)s (%(mac)s) associated with '
                     'Neutron port %(port)s',
                     {'node_port': node_port.uuid,
                      'mac': node_port.address,
                      'port': port.id})

        api.validate_node(node.uuid, validate_deploy=True)
        with _config_drive_dir(node, ssh_keys) as cd:
            api.node_action(node.uuid, 'active', configdrive=cd)
        LOG.info('Provisioning started on node %s', _log_node(node))

        if wait is not None:
            api.ironic.node.wait_for_provision_state(node.uuid, 'active',
                                                     timeout=max(0, wait))
    except Exception:
        with excutils.save_and_reraise_exception():
            LOG.error('Deploy attempt failed, cleaning up')
            try:
                clean_up(api, node, neutron_ports)
            except Exception:
                LOG.exception('Clean up failed, system needs manual clean up')

    if wait is not None:
        LOG.info('Deploy succeeded on node %s', _log_node(node))


def deploy(api, resource_class, image_id, network_id, root_disk_size,
           ssh_keys, capabilities=None, netboot=False,
           wait=None, dry_run=False):
    """Deploy an image on a given profile."""
    capabilities = capabilities or {}
    LOG.debug('Deploying image %(image)s on node with class %(class)s '
              'and capabilities %(caps)s on network %(net)s',
              {'image': image_id, 'class': resource_class,
               'net': network_id, 'capabilities': capabilities})

    image = api.get_image_info(image_id)
    if image is None:
        raise RuntimeError('Image %s does not exist' % image_id)
    for im_prop in ('kernel_id', 'ramdisk_id'):
        if not getattr(image, im_prop, None):
            raise RuntimeError('%s property is required on image' % im_prop)
    LOG.debug('Image: %s', image)

    network = api.get_network(network_id)
    if network is None:
        raise RuntimeError('Network %s does not exist' % network_id)
    LOG.debug('Network: %s', network)

    nodes = api.list_nodes(resource_class=resource_class)
    LOG.debug('Ironic nodes: %s', nodes)
    if not nodes:
        raise RuntimeError('No available nodes found with resource class %s' %
                           resource_class)

    node = reserve(api, nodes, capabilities, dry_run=dry_run)
    LOG.info('Reserved node %s', _log_node(node))

    if dry_run:
        LOG.warning('Dry run, not provisioning node %s', node.uuid)
        return

    provision(api, node, network, image, root_disk_size, ssh_keys,
              netboot=netboot, wait=wait)


def undeploy(api, node_uuid, wait=None):
    neutron_ports = [port.id
                     for port in api.list_node_attached_ports(node_uuid)]

    api.node_action(node_uuid, 'deleted')
    LOG.info('Deleting started for node %s', node_uuid)
    if wait is not None:
        api.ironic.node.wait_for_provision_state(node_uuid, 'available',
                                                 timeout=max(0, wait))

    clean_up(api, node_uuid, neutron_ports)
    LOG.info('Node %s undeployed successfully', node_uuid)
