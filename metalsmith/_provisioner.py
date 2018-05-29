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

from metalsmith import _os_api
from metalsmith import _scheduler
from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)

_CREATED_PORTS = 'metalsmith_created_ports'
_ATTACHED_PORTS = 'metalsmith_attached_ports'
# NOTE(dtantsur): include available since there is a period of time between
# claiming the instance and starting the actual provisioning via ironic.
_DEPLOYING_STATES = frozenset(['available', 'deploying', 'wait call-back',
                               'deploy complete'])
_ACTIVE_STATES = frozenset(['active'])
_ERROR_STATE = frozenset(['error', 'deploy failed'])

_HEALTHY_STATES = frozenset(['deploying', 'active'])


class Instance(object):
    """Instance status in metalsmith."""

    def __init__(self, api, node):
        self._api = api
        self._uuid = node.uuid
        self._node = node

    def ip_addresses(self):
        """Returns IP addresses for this instance.

        :return: dict mapping network name or ID to a list of IP addresses.
        """
        result = {}
        for nic in self.nics():
            net = getattr(nic.network, 'name', None) or nic.network.id
            result.setdefault(net, []).extend(
                ip['ip_address'] for ip in nic.fixed_ips
                if ip.get('ip_address')
            )
        return result

    @property
    def is_deployed(self):
        """Whether the node is deployed."""
        return self._node.provision_state in _ACTIVE_STATES

    @property
    def is_healthy(self):
        """Whether the node is not at fault or maintenance."""
        return self.state in _HEALTHY_STATES and not self._node.maintenance

    def nics(self):
        """List NICs for this instance.

        :return: List of `Port` objects with additional ``network`` fields
            with full representations of their networks.
        """
        result = []
        vifs = self._api.list_node_attached_ports(self.node)
        for vif in vifs:
            port = self._api.get_port(vif.id)
            port.network = self._api.get_network(port.network_id)
            result.append(port)
        return result

    @property
    def node(self):
        """Underlying `Node` object."""
        return self._node

    @property
    def state(self):
        """Instance state.

        ``deploying``
            deployment is in progress
        ``active``
            node is provisioned
        ``maintenance``
            node is provisioned but is in maintenance mode
        ``error``
            node has a failure
        ``unknown``
            node in unexpected state (maybe unprovisioned or modified by
            a third party)
        """
        prov_state = self._node.provision_state
        if prov_state in _DEPLOYING_STATES:
            return 'deploying'
        elif prov_state in _ERROR_STATE:
            return 'error'
        elif prov_state in _ACTIVE_STATES:
            if self._node.maintenance:
                return 'maintenance'
            else:
                return 'active'
        else:
            return 'unknown'

    def to_dict(self):
        """Convert instance to a dict."""
        return {
            'ip_addresses': self.ip_addresses(),
            'node': self._node.to_dict(),
            'state': self.state,
            'uuid': self._uuid,
        }

    @property
    def uuid(self):
        """Instance UUID (the same as `Node` UUID for metalsmith)."""
        return self._uuid


class Provisioner(object):
    """API to deploy/undeploy nodes with OpenStack.

    :param session: `Session` object (from ``keystoneauth``) to use when
        making API requests. Mutually exclusive with **cloud_region**.
    :param cloud_region: cloud configuration object (from ``openstacksdk``)
        to use when making API requests. Mutually exclusive with **session**.
    :param dry_run: boolean value, set to ``True`` to prevent any API calls
        from being actually made.
    """

    def __init__(self, session=None, cloud_region=None, dry_run=False):
        self._api = _os_api.API(session=session, cloud_region=cloud_region)
        self._dry_run = dry_run

    def reserve_node(self, resource_class, capabilities=None):
        """Find and reserve a suitable node.

        Example::

         node = provisioner.reserve_node("compute",
                                         capabilities={"boot_mode": "uefi"})

        :param resource_class: Requested resource class.
        :param capabilities: Requested capabilities as a dict.
        :return: reserved `Node` object.
        :raises: :py:class:`metalsmith.exceptions.ReservationFailed`
        """
        capabilities = capabilities or {}

        nodes = self._api.list_nodes(resource_class=resource_class)
        if not nodes:
            raise exceptions.ResourceClassNotFound(resource_class,
                                                   capabilities)

        # Make sure parallel executions don't try nodes in the same sequence
        random.shuffle(nodes)
        LOG.debug('Ironic nodes: %s', nodes)

        filters = [_scheduler.CapabilitiesFilter(resource_class, capabilities),
                   _scheduler.ValidationFilter(self._api,
                                               resource_class, capabilities)]
        reserver = _scheduler.IronicReserver(self._api, resource_class,
                                             capabilities)
        node = _scheduler.schedule_node(nodes, filters, reserver,
                                        dry_run=self._dry_run)
        LOG.debug('Reserved node: %s', node)
        return node

    def _check_node_for_deploy(self, node):
        """Check that node is ready and reserve it if needed."""
        try:
            node = self._api.get_node(node)
        except Exception as exc:
            raise exceptions.InvalidNode('Cannot find node %(node)s: %(exc)s' %
                                         {'node': node, 'exc': exc})

        if not node.instance_uuid:
            if not self._dry_run:
                LOG.debug('Node %s not reserved yet, reserving',
                          _utils.log_node(node))
                self._api.reserve_node(node, instance_uuid=node.uuid)
        elif node.instance_uuid != node.uuid:
            raise exceptions.InvalidNode('Node %(node)s already reserved '
                                         'by instance %(inst)s outside of '
                                         'metalsmith, cannot deploy on it' %
                                         {'node': _utils.log_node(node),
                                          'inst': node.instance_uuid})

        if node.maintenance:
            raise exceptions.InvalidNode('Refusing to deploy on node %(node)s '
                                         'which is in maintenance mode due to '
                                         '%(reason)s' %
                                         {'node': _utils.log_node(node),
                                          'reason': node.maintenance_reason})

        return node

    def provision_node(self, node, image_ref, nics=None, root_disk_size=None,
                       ssh_keys=None, netboot=False, wait=None):
        """Provision the node with the given image.

        Example::

         provisioner.provision_node("compute-1", "centos",
                                    nics=[{"network": "private"},
                                          {"network": "external"}],
                                    root_disk_size=50,
                                    wait=3600)

        :param node: Node object, UUID or name. Will be reserved first, if
            not reserved already. Must be in the "available" state with
            maintenance mode off.
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
        :return: :py:class:`metalsmith.Instance` object with the current
            status of provisioning. If ``wait`` is not ``None``, provisioning
            is already finished.
        :raises: :py:class:`metalsmith.exceptions.Error`
        """
        node = self._check_node_for_deploy(node)
        created_ports = []
        attached_ports = []

        try:

            root_disk_size = _utils.get_root_disk(root_disk_size, node)

            try:
                image = self._api.get_image_info(image_ref)
            except Exception as exc:
                raise exceptions.InvalidImage(
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

            LOG.debug('Updating node %(node)s with %(updates)s',
                      {'node': _utils.log_node(node), 'updates': updates})
            node = self._api.update_node(node, updates)
            self._api.validate_node(node, validate_deploy=True)

            LOG.debug('Generating a configdrive for node %s',
                      _utils.log_node(node))
            with _utils.config_drive_dir(node, ssh_keys) as cd:
                self._api.node_action(node, 'active',
                                      configdrive=cd)
        except Exception:
            exc_info = sys.exc_info()

            try:
                LOG.error('Deploy attempt failed on node %s, cleaning up',
                          _utils.log_node(node))
                self._delete_ports(node, created_ports, attached_ports)
                LOG.debug('Releasing lock on node %s', _utils.log_node(node))
                self._api.release_node(node)
            except Exception:
                LOG.exception('Clean up failed')

            six.reraise(*exc_info)

        LOG.info('Provisioning started on node %s', _utils.log_node(node))

        if wait is not None:
            LOG.debug('Waiting for node %(node)s to reach state active '
                      'with timeout %(timeout)s',
                      {'node': _utils.log_node(node), 'timeout': wait})
            self._api.wait_for_node_state(node, 'active', timeout=wait)
            LOG.info('Deploy succeeded on node %s', _utils.log_node(node))

        # Update the node to return it's latest state
        node = self._api.get_node(node, refresh=True)
        return Instance(self._api, node)

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
                    raise exceptions.InvalidNIC(
                        'Cannot find network %(net)s: %(error)s' %
                        {'net': nic_id, 'error': exc})
                else:
                    result.append((nic_type, network))
            elif nic_type == 'port':
                try:
                    port = self._api.get_port(nic_id)
                except Exception as exc:
                    raise exceptions.InvalidNIC(
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
                LOG.info('Created port %(port)s for node %(node)s on '
                         'network %(net)s',
                         {'port': _utils.log_res(port),
                          'node': _utils.log_node(node),
                          'net': _utils.log_res(nic)})
            else:
                port = nic

            self._api.attach_port_to_node(node.uuid, port.id)
            LOG.info('Attached port %(port)s to node %(node)s',
                     {'port': _utils.log_res(port),
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
            except Exception as exc:
                LOG.warning('Failed to delete neutron port %(port)s: %(exc)s',
                            {'port': port_id, 'exc': exc})
            else:
                LOG.info('Deleted port %(port)s for node %(node)s',
                         {'port': port_id, 'node': _utils.log_node(node)})

        update = {'/extra/%s' % item: _os_api.REMOVE
                  for item in (_CREATED_PORTS, _ATTACHED_PORTS)}
        LOG.debug('Updating node %(node)s with %(updates)s',
                  {'node': _utils.log_node(node), 'updates': update})
        try:
            self._api.update_node(node, update)
        except Exception as exc:
            LOG.debug('Failed to clear node %(node)s extra: %(exc)s',
                      {'node': _utils.log_node(node), 'exc': exc})

    def unprovision_node(self, node, wait=None):
        """Unprovision a previously provisioned node.

        :param node: `Node` object, :py:class:`metalsmith.Instance`,
            UUID or name.
        :param wait: How many seconds to wait for the process to finish,
            None to return immediately.
        :return: the latest `Node` object.
        """
        node = self._api.get_node(node)
        if self._dry_run:
            LOG.warning("Dry run, not unprovisioning")
            return

        self._delete_ports(node)
        LOG.debug('Releasing lock on node %s', _utils.log_node(node))
        self._api.release_node(node)
        self._api.node_action(node, 'deleted')

        LOG.info('Deleting started for node %s', _utils.log_node(node))

        if wait is not None:
            self._api.wait_for_node_state(node, 'available', timeout=wait)
            LOG.info('Node %s undeployed successfully', _utils.log_node(node))

        return self._api.get_node(node, refresh=True)
