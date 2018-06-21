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
import sys

import six

from metalsmith import _instance
from metalsmith import _os_api
from metalsmith import _scheduler
from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)

_CREATED_PORTS = 'metalsmith_created_ports'
_ATTACHED_PORTS = 'metalsmith_attached_ports'


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
        """Check that node is ready and reserve it if needed.

        These checks are done outside of the try..except block in
        ``provision_node``, so that we don't touch nodes that fail it at all.
        Particularly, we don't want to try clean up nodes that were not
        reserved by us or are in maintenance mode.
        """
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

    def _check_hostname(self, node, hostname):
        """Check the provided host name.

        If the ``hostname`` is not provided, use either the name or the UUID,
        whichever is appropriate for a host name.

        :return: appropriate hostname
        :raises: ValueError on inappropriate value of ``hostname``
        """
        if hostname is None:
            if node.name and _utils.is_hostname_safe(node.name):
                return node.name
            else:
                return node.uuid

        if not _utils.is_hostname_safe(hostname):
            raise ValueError("%s cannot be used as a hostname" % hostname)

        existing = self._api.find_node_by_hostname(hostname)
        if existing is not None and existing.uuid != node.uuid:
            raise ValueError("The following node already uses hostname "
                             "%(host)s: %(node)s" %
                             {'host': hostname,
                              'node': _utils.log_node(existing)})

        return hostname

    def provision_node(self, node, image, nics=None, root_disk_size=None,
                       ssh_keys=None, hostname=None, netboot=False, wait=None):
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
        :param image: Image name or UUID to provision.
        :param nics: List of virtual NICs to attach to physical ports.
            Each item is a dict with a key describing the type of the NIC:
            either a port (``{"port": "<port name or ID>"}``) or a network
            to create a port on (``{"network": "<network name or ID>"}``).
        :param root_disk_size: The size of the root partition. By default
            the value of the local_gb property is used.
        :param ssh_keys: list of public parts of the SSH keys to upload
            to the nodes.
        :param hostname: Hostname to assign to the instance. Defaults to the
            node's name or UUID.
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
            hostname = self._check_hostname(node, hostname)
            root_disk_size = _utils.get_root_disk(root_disk_size, node)

            try:
                image = self._api.get_image(image)
            except Exception as exc:
                raise exceptions.InvalidImage(
                    'Cannot find image %(image)s: %(error)s' %
                    {'image': image, 'error': exc})

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
                       '/extra/%s' % _ATTACHED_PORTS: attached_ports,
                       '/instance_info/%s' % _os_api.HOSTNAME_FIELD: hostname}

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
            with _utils.config_drive_dir(node, ssh_keys, hostname) as cd:
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
        return _instance.Instance(self._api, node)

    def _get_nics(self, nics):
        """Validate and get the NICs."""
        _utils.validate_nics(nics)

        result = []
        for nic in nics:
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
            else:
                try:
                    port = self._api.get_port(nic_id)
                except Exception as exc:
                    raise exceptions.InvalidNIC(
                        'Cannot find port %(port)s: %(error)s' %
                        {'port': nic_id, 'error': exc})
                else:
                    result.append((nic_type, port))

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
        update['/instance_info/%s' % _os_api.HOSTNAME_FIELD] = _os_api.REMOVE
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
            hostname, UUID or node name.
        :param wait: How many seconds to wait for the process to finish,
            None to return immediately.
        :return: the latest `Node` object.
        """
        node = self._api.get_node(node, accept_hostname=True)
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

    def show_instance(self, instance_id):
        """Show information about instance.

        :param instance_id: hostname, UUID or node name.
        :return: :py:class:`metalsmith.Instance` object.
        """
        return self.show_instances([instance_id])[0]

    def show_instances(self, instances):
        """Show information about instance.

        More efficient than calling :meth:`show_instance` in a loop, because
        it caches the node list.

        :param instances: list of hostnames, UUIDs or node names.
        :return: list of :py:class:`metalsmith.Instance` objects in the same
            order as ``instances``.
        """
        with self._api.cache_node_list_for_lookup():
            return [
                _instance.Instance(
                    self._api,
                    self._api.get_node(inst, accept_hostname=True))
                for inst in instances
            ]
