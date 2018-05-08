# Copyright 2018 Red Hat, Inc.
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
import random

from oslo_utils import excutils

from metalsmith import _exceptions
from metalsmith import _os_api
from metalsmith import _scheduler
from metalsmith import _utils


LOG = logging.getLogger(__name__)

_CREATED_PORTS = 'metalsmith_created_ports'


class Provisioner(object):
    """API to deploy/undeploy nodes with OpenStack."""

    def __init__(self, session, dry_run=False):
        self._api = _os_api.API(session)
        self._dry_run = dry_run

    def reserve_node(self, resource_class, capabilities=None):
        """Find and reserve a suitable node.

        :param resource_class: Requested resource class.
        :param capabilities: Requested capabilities as a dict.
        :return: reserved Node object
        :raises: ReservationFailed
        """
        capabilities = capabilities or {}

        nodes = self._api.list_nodes(resource_class=resource_class)
        if not nodes:
            raise _exceptions.ResourceClassNotFound(resource_class,
                                                    capabilities)

        # Make sure parallel executions don't try nodes in the same sequence
        random.shuffle(nodes)
        LOG.debug('Ironic nodes: %s', nodes)

        filters = [_scheduler.CapabilitiesFilter(resource_class, capabilities),
                   _scheduler.ValidationFilter(self._api,
                                               resource_class, capabilities)]
        reserver = _scheduler.IronicReserver(self._api, resource_class,
                                             capabilities)
        return _scheduler.schedule_node(nodes, filters, reserver,
                                        dry_run=self._dry_run)

    def provision_node(self, node, image_ref, network_refs,
                       root_disk_size=None, ssh_keys=None, netboot=False,
                       wait=None):
        """Provision the node with the given image.

        :param node: Node object, UUID or name.
        :param image_ref: Image name or UUID to provision.
        :param network_refs: List of network names or UUIDs to use.
        :param root_disk_size: The size of the root partition. By default
            the value of the local_gb property is used.
        :param ssh_keys: list of public parts of the SSH keys to upload
            to the nodes.
        :param netboot: Whether to use networking boot for final instances.
        :param wait: How many seconds to wait for the deployment to finish,
            None to return immediately.
        :return: Reservation
        """
        node = self._api.get_node(node)

        root_disk_size = _utils.get_root_disk(root_disk_size, node)

        image = self._api.get_image_info(image_ref)
        if image is None:
            raise _exceptions.InvalidImage('Image %s does not exist' %
                                           image_ref)

        # TODO(dtantsur): support whole-disk images
        for im_prop in ('kernel_id', 'ramdisk_id'):
            if not getattr(image, im_prop, None):
                raise _exceptions.InvalidImage('%s is required on image' %
                                               im_prop)
        LOG.debug('Image: %s', image)

        networks = self._get_networks(network_refs)

        if self._dry_run:
            LOG.warning('Dry run, not provisioning node %s',
                        _utils.log_node(node))
            return node

        created_ports = self._create_ports(node, networks)

        target_caps = {'boot_option': 'netboot' if netboot else 'local'}
        # TODO(dtantsur): support whole-disk images
        updates = {'/instance_info/ramdisk': image.ramdisk_id,
                   '/instance_info/kernel': image.kernel_id,
                   '/instance_info/image_source': image.id,
                   '/instance_info/root_gb': root_disk_size,
                   '/instance_info/capabilities': target_caps,
                   '/extra/%s' % _CREATED_PORTS: created_ports}

        try:
            node = self._api.update_node(node, updates)
            self._api.validate_node(node, validate_deploy=True)

            with _utils.config_drive_dir(node, ssh_keys) as cd:
                self._api.node_action(node, 'active',
                                      configdrive=cd)
            LOG.info('Provisioning started on node %s', _utils.log_node(node))

            if wait is not None:
                self._api.wait_for_active(node, timeout=wait)

            # Update the node to return it's latest state
            node = self._api.get_node(node)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error('Deploy attempt failed on node %s, cleaning up',
                          _utils.log_node(node))
                try:
                    self._clean_up(node, created_ports)
                except Exception:
                    LOG.exception('Clean up failed')

        if wait is not None:
            LOG.info('Deploy succeeded on node %s', _utils.log_node(node))

        return node

    def _get_networks(self, network_refs):
        """Validate and get the networks."""
        networks = []
        for network_ref in network_refs:
            network = self._api.get_network(network_ref)
            if network is None:
                raise _exceptions.InvalidNetwork('Network %s does not exist' %
                                                 network_ref)
            LOG.debug('Network: %s', network)
            networks.append(network)
        return networks

    def _clean_up(self, node, created_ports=None):
        """Clean up a failed deployment."""
        if self._dry_run:
            LOG.debug("Dry run, not cleaning up")
            return

        if created_ports is None:
            created_ports = node.extra.get(_CREATED_PORTS, [])

        for port_id in created_ports:
            LOG.debug('Detaching port %(port)s from node %(node)s',
                      {'port': port_id, 'node': node.uuid})
            try:
                self._api.detach_port_from_node(node.uuid, port_id)
            except Exception as exc:
                LOG.debug('Failed to remove VIF %(vif)s from node %(node)s, '
                          'assuming already removed: %(exc)s',
                          {'vif': port_id, 'node': _utils.log_node(node),
                           'exc': exc})

            LOG.debug('Deleting port %s', port_id)
            try:
                self._api.delete_port(port_id)
            except Exception:
                LOG.warning('Failed to delete neutron port %s', port_id)

        try:
            self._api.release_node(node)
        except Exception as exc:
            LOG.warning('Failed to remove instance_uuid from node %(node)s, '
                        'assuming already removed: %(exc)s',
                        {'node': _utils.log_node(node), 'exc': exc})

    def _create_ports(self, node, networks):
        """Create and attach ports on given networks."""
        created_ports = []
        try:
            for network in networks:
                port = self._api.create_port(network_id=network.id)
                created_ports.append(port.id)
                LOG.debug('Created Neutron port %s', port)

                self._api.attach_port_to_node(node.uuid, port.id)
                LOG.info('Attached port %(port)s to node %(node)s',
                         {'port': port.id,
                          'node': _utils.log_node(node)})
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error('Creating and binding ports failed, cleaning up')
                try:
                    self._clean_up(node, created_ports)
                except Exception:
                    LOG.exception('Clean up failed, delete and detach ports '
                                  '%s manually', created_ports)
        return created_ports

    def unprovision_node(self, node, wait=None):
        """Unprovision a previously provisioned node.

        :param node: node object, UUID or name.
        :param wait: How many seconds to wait for the process to finish,
            None to return immediately.
        """
        node = self._api.get_node(node)

        self._api.node_action(node.uuid, 'deleted')
        LOG.info('Deleting started for node %s', _utils.log_node(node))

        if wait is not None:
            self._api.ironic.node.wait_for_provision_state(
                node.uuid, 'available', timeout=max(0, wait))

        self._clean_up(node)
        LOG.info('Node %s undeployed successfully', _utils.log_node(node))
