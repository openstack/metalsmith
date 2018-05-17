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

import collections
import logging
import random
import sys

import six

from metalsmith import _exceptions
from metalsmith import _os_api
from metalsmith import _scheduler
from metalsmith import _utils


LOG = logging.getLogger(__name__)

_CREATED_PORTS = 'metalsmith_created_ports'
_ATTACHED_PORTS = 'metalsmith_attached_ports'


class Provisioner(object):
    """API to deploy/undeploy nodes with OpenStack."""

    def __init__(self, session=None, cloud_region=None, dry_run=False):
        self._api = _os_api.API(session=session, cloud_region=cloud_region)
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

    def provision_node(self, node, image_ref, nics=None, root_disk_size=None,
                       ssh_keys=None, netboot=False, wait=None):
        """Provision the node with the given image.

        :param node: Node object, UUID or name.
        :param image_ref: Image name or UUID to provision.
        :param nics: List of virtual NICs to attach to physical ports.
            Each item is a dict with a key describing the type of the NIC:
            either a port (``{"port": "<port name or ID>"}``) or a network
            to create a port on (``{"network": "<network name or ID>"}``).
        :param root_disk_size: The size of the root partition. By default
            the value of the local_gb property is used.
        :param ssh_keys: list of public parts of the SSH keys to upload
            to the nodes.
        :param netboot: Whether to use networking boot for final instances.
        :param wait: How many seconds to wait for the deployment to finish,
            None to return immediately.
        :return: Reservation
        """
        created_ports = []
        attached_ports = []

        try:
            node = self._api.get_node(node)

            root_disk_size = _utils.get_root_disk(root_disk_size, node)

            try:
                image = self._api.get_image_info(image_ref)
            except Exception as exc:
                raise _exceptions.InvalidImage(
                    'Cannot find image %(image)s: %(error)s' %
                    {'image': image_ref, 'error': exc})

            LOG.debug('Image: %s', image)

            nics = self._get_nics(nics or [])

            if self._dry_run:
                LOG.warning('Dry run, not provisioning node %s',
                            _utils.log_node(node))
                return node

            self._create_and_attach_ports(node, nics,
                                          created_ports, attached_ports)

            target_caps = {'boot_option': 'netboot' if netboot else 'local'}

            updates = {'/instance_info/image_source': image.id,
                       '/instance_info/root_gb': root_disk_size,
                       '/instance_info/capabilities': target_caps,
                       '/extra/%s' % _CREATED_PORTS: created_ports,
                       '/extra/%s' % _ATTACHED_PORTS: attached_ports}

            for prop in ('kernel', 'ramdisk'):
                value = getattr(image, '%s_id' % prop, None)
                if value:
                    updates['/instance_info/%s' % prop] = value

            node = self._api.update_node(node, updates)
            self._api.validate_node(node, validate_deploy=True)

            with _utils.config_drive_dir(node, ssh_keys) as cd:
                self._api.node_action(node, 'active',
                                      configdrive=cd)
            LOG.info('Provisioning started on node %s', _utils.log_node(node))

            if wait is not None:
                self._api.wait_for_node_state(node, 'active', timeout=wait)

            # Update the node to return it's latest state
            node = self._api.get_node(node)
        except Exception:
            exc_info = sys.exc_info()

            try:
                LOG.error('Deploy attempt failed on node %s, cleaning up',
                          _utils.log_node(node))
                self._delete_ports(node, created_ports, attached_ports)
                self._api.release_node(node)
            except Exception:
                LOG.exception('Clean up failed')

            six.reraise(*exc_info)

        if wait is not None:
            LOG.info('Deploy succeeded on node %s', _utils.log_node(node))
            self._log_ips(node, created_ports)

        return node

    def _log_ips(self, node, created_ports):
        ips = []
        for port in created_ports:
            # Refresh the port to get its IP(s)
            port = self._api.get_port(port)
            for ip in port.fixed_ips:
                if ip.get('ip_address'):
                    ips.append(ip['ip_address'])
        if ips:
            LOG.info('IPs for %(node)s: %(ips)s',
                     {'node': _utils.log_node(node),
                      'ips': ', '.join(ips)})
        else:
            LOG.warning('No IPs for node %s', _utils.log_node(node))

    def _get_nics(self, nics):
        """Validate and get the NICs."""
        result = []
        if not isinstance(nics, collections.Sequence):
            raise TypeError("NICs must be a list of dicts")

        for nic in nics:
            if not isinstance(nic, collections.Mapping) or len(nic) != 1:
                raise TypeError("Each NIC must be a dict with one item, "
                                "got %s" % nic)

            nic_type, nic_id = next(iter(nic.items()))
            if nic_type == 'network':
                try:
                    network = self._api.get_network(nic_id)
                except Exception as exc:
                    raise _exceptions.InvalidNIC(
                        'Cannot find network %(net)s: %(error)s' %
                        {'net': nic_id, 'error': exc})
                else:
                    result.append((nic_type, network))
            elif nic_type == 'port':
                try:
                    port = self._api.get_port(nic_id)
                except Exception as exc:
                    raise _exceptions.InvalidNIC(
                        'Cannot find port %(port)s: %(error)s' %
                        {'port': nic_id, 'error': exc})
                else:
                    result.append((nic_type, port))
            else:
                raise ValueError("Unexpected NIC type %s, supported values: "
                                 "'port', 'network'" % nic_type)

        return result

    def _create_and_attach_ports(self, node, nics, created_ports,
                                 attached_ports):
        """Create and attach ports on given networks."""
        for nic_type, nic in nics:
            if nic_type == 'network':
                port = self._api.create_port(network_id=nic.id)
                created_ports.append(port.id)
                LOG.debug('Created Neutron port %s', port)
            else:
                port = nic

            self._api.attach_port_to_node(node.uuid, port.id)
            LOG.info('Attached port %(port)s to node %(node)s',
                     {'port': port.id,
                      'node': _utils.log_node(node)})
            attached_ports.append(port.id)

    def _delete_ports(self, node, created_ports=None, attached_ports=None):
        if created_ports is None:
            created_ports = node.extra.get(_CREATED_PORTS, [])
        if attached_ports is None:
            attached_ports = node.extra.get(_ATTACHED_PORTS, [])

        for port_id in set(attached_ports + created_ports):
            LOG.debug('Detaching port %(port)s from node %(node)s',
                      {'port': port_id, 'node': node.uuid})
            try:
                self._api.detach_port_from_node(node, port_id)
            except Exception as exc:
                LOG.debug('Failed to remove VIF %(vif)s from node %(node)s, '
                          'assuming already removed: %(exc)s',
                          {'vif': port_id, 'node': _utils.log_node(node),
                           'exc': exc})

        for port_id in created_ports:
            LOG.debug('Deleting port %s', port_id)
            try:
                self._api.delete_port(port_id)
            except Exception:
                LOG.warning('Failed to delete neutron port %s', port_id)

        update = {'/extra/%s' % item: _os_api.REMOVE
                  for item in (_CREATED_PORTS, _ATTACHED_PORTS)}
        try:
            self._api.update_node(node, update)
        except Exception as exc:
            LOG.warning('Failed to clear node %(node)s extra: %(exc)s',
                        {'node': _utils.log_node(node), 'exc': exc})

    def unprovision_node(self, node, wait=None):
        """Unprovision a previously provisioned node.

        :param node: node object, UUID or name.
        :param wait: How many seconds to wait for the process to finish,
            None to return immediately.
        """
        node = self._api.get_node(node)
        if self._dry_run:
            LOG.debug("Dry run, not unprovisioning")
            return

        self._delete_ports(node)

        self._api.node_action(node, 'deleted')
        LOG.info('Deleting started for node %s', _utils.log_node(node))

        if wait is not None:
            self._api.wait_for_node_state(node, 'available', timeout=wait)

        self._api.release_node(node)
        LOG.info('Node %s undeployed successfully', _utils.log_node(node))
