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

import logging

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


def clean_up(api, node, neutron_ports):
    try:
        api.update_node(node.uuid, instance_uuid=os_api.REMOVE)
    except Exception:
        LOG.warning('Failed to remove instance_uuid, assuming already removed')

    for port in neutron_ports:
        try:
            api.detach_port_from_node(node.uuid, port.id)
        except Exception:
            LOG.warning('Failed to remove VIF %(vif)s from node %(node)s, '
                        'assuming already removed',
                        {'vif': port.id, 'node': node.uuid})
        try:
            api.delete_port(port.id)
        except Exception:
            LOG.warning('Failed to delete neutron port %s', port.id)


def provision(api, node, network, image, netboot=False):
    target_caps = {'boot_option': 'netboot' if netboot else 'local'}
    updates = {'/instance_info/ramdisk': image.ramdisk_id,
               '/instance_info/kernel': image.kernel_id,
               '/instance_info/image_source': image.id,
               '/instance_info/root_gb': node.properties['local_gb'],
               '/instance_info/capabilities': target_caps}
    node = api.update_node(node.uuid, updates)
    neutron_ports = []

    try:
        node_ports = api.list_node_ports(node.uuid)
        for node_port in node_ports:
            port = api.create_port(mac_address=node_port.address,
                                   network_id=network.id)
            neutron_ports.append(port)
            LOG.debug('Created Neutron port %s', port)

            api.attach_port_to_node(node.uuid, port.id)
            LOG.info('Ironic port %(node_port)s (%(mac)s) associated with '
                     'Neutron port %(port)s',
                     {'node_port': node_port.uuid,
                      'mac': node_port.address,
                      'port': port.id})

        api.validate_node(node.uuid, validate_deploy=True)
        api.node_action(node.uuid, 'active')
    except Exception as exc:
        with excutils.save_and_reraise_exception():
            LOG.error('Deploy attempt failed: %s', exc)
            try:
                clean_up(node, neutron_ports)
            except Exception:
                LOG.exception('Clean up failed, system needs manual clean up')


def deploy(api, resource_class, image_id, network_id, capabilities,
           netboot=False, dry_run=False):
    """Deploy an image on a given profile."""
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
    LOG.info('Got list of %d available nodes from Ironic', len(nodes))

    node = reserve(api, nodes, capabilities, dry_run=dry_run)
    LOG.info('Reserved node %s', _log_node(node))

    if dry_run:
        LOG.warning('Dry run, not provisioning node %s', node.uuid)
        return

    provision(api, node, network, image, netboot=netboot)
    LOG.info('Provisioning started on node %s', _log_node(node))
