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
import time
import warnings

from openstack import connection
import six

from metalsmith import _config
from metalsmith import _instance
from metalsmith import _nics
from metalsmith import _os_api
from metalsmith import _scheduler
from metalsmith import _utils
from metalsmith import exceptions
from metalsmith import sources


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

    :ivar connection: `openstacksdk` `Connection` object used for accessing
        OpenStack API during provisioning.
    """

    def __init__(self, session=None, cloud_region=None, dry_run=False):
        if cloud_region is None:
            if session is None:
                raise TypeError('Either session or cloud_region must '
                                'be provided')
            self.connection = connection.Connection(session=session)
        elif session is not None:
            raise TypeError('Either session or cloud_region must be provided, '
                            'but not both')
        else:
            session = cloud_region.get_session()
            self.connection = connection.Connection(config=cloud_region)

        self._api = _os_api.API(session, self.connection)
        self._dry_run = dry_run

    def reserve_node(self, resource_class=None, conductor_group=None,
                     capabilities=None, traits=None, candidates=None,
                     predicate=None):
        """Find and reserve a suitable node.

        Example::

         node = provisioner.reserve_node("compute",
                                         capabilities={"boot_mode": "uefi"})

        :param resource_class: Requested resource class. If ``None``, a node
            with any resource class can be chosen.
        :param conductor_group: Conductor group to pick the nodes from.
            Value ``None`` means any group, use empty string "" for nodes
            from the default group.
        :param capabilities: Requested capabilities as a dict.
        :param traits: Requested traits as a list of strings.
        :param candidates: List of nodes (UUIDs, names or `Node` objects)
            to pick from. The filters (for resource class and capabilities)
            are still applied to the provided list. The order in which
            the nodes are considered is retained.
        :param predicate: Custom predicate to run on nodes. A callable that
            accepts a node and returns ``True`` if it should be included,
            ``False`` otherwise. Any exceptions are propagated to the caller.
        :return: reserved `Node` object.
        :raises: :py:class:`metalsmith.exceptions.ReservationFailed`
        """
        capabilities = capabilities or {}

        if candidates:
            nodes = [self._api.get_node(node) for node in candidates]
            filters = [
                _scheduler.NodeTypeFilter(resource_class, conductor_group),
            ]
        else:
            nodes = self._api.list_nodes(resource_class=resource_class,
                                         conductor_group=conductor_group)
            if not nodes:
                raise exceptions.NodesNotFound(resource_class, conductor_group)
            # Ensure parallel executions don't try nodes in the same sequence
            random.shuffle(nodes)
            # No need to filter by resource_class and conductor_group any more
            filters = []

        LOG.debug('Candidate nodes: %s', nodes)

        filters.append(_scheduler.CapabilitiesFilter(capabilities))
        filters.append(_scheduler.TraitsFilter(traits))
        if predicate is not None:
            filters.append(_scheduler.CustomPredicateFilter(predicate))

        reserver = _scheduler.IronicReserver(self._api)
        node = _scheduler.schedule_node(nodes, filters, reserver,
                                        dry_run=self._dry_run)

        update = {}
        if capabilities:
            update['/instance_info/capabilities'] = capabilities
        if traits:
            update['/instance_info/traits'] = traits
        if update:
            node = self._api.update_node(node, update)

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

    def provision_node(self, node, image, nics=None, root_size_gb=None,
                       swap_size_mb=None, config=None, hostname=None,
                       netboot=False, capabilities=None, traits=None,
                       wait=None, root_disk_size=None):
        """Provision the node with the given image.

        Example::

         provisioner.provision_node("compute-1", "centos",
                                    nics=[{"network": "private"},
                                          {"network": "external"}],
                                    root_size_gb=50,
                                    wait=3600)

        :param node: Node object, UUID or name. Will be reserved first, if
            not reserved already. Must be in the "available" state with
            maintenance mode off.
        :param image: Image source - one of :mod:`~metalsmith.sources`,
            `Image` name or UUID.
        :param nics: List of virtual NICs to attach to physical ports.
            Each item is a dict with a key describing the type of the NIC:
            either a port (``{"port": "<port name or ID>"}``) or a network
            to create a port on (``{"network": "<network name or ID>"}``).
            A network record can optionally feature a ``fixed_ip`` argument
            to use this specific fixed IP from a suitable subnet.
        :param root_size_gb: The size of the root partition. By default
            the value of the local_gb property is used.
        :param swap_size_mb: The size of the swap partition. It's an error
            to specify it for a whole disk image.
        :param config: :py:class:`metalsmith.InstanceConfig` object with
            the configuration to pass to the instance.
        :param hostname: Hostname to assign to the instance. Defaults to the
            node's name or UUID.
        :param netboot: Whether to use networking boot for final instances.
        :param capabilities: Requested capabilities of the node. If present,
            overwrites the capabilities set by :meth:`reserve_node`.
            Note that the capabilities are not checked against the ones
            provided by the node - use :meth:`reserve_node` for that.
        :param traits: Requested traits of the node. If present, overwrites
            the traits set by :meth:`reserve_node`. Note that the traits are
            not checked against the ones provided by the node - use
            :meth:`reserve_node` for that.
        :param wait: How many seconds to wait for the deployment to finish,
            None to return immediately.
        :param root_disk_size: DEPRECATED, use ``root_size_gb``.
        :return: :py:class:`metalsmith.Instance` object with the current
            status of provisioning. If ``wait`` is not ``None``, provisioning
            is already finished.
        :raises: :py:class:`metalsmith.exceptions.Error`
        """
        if config is None:
            config = _config.InstanceConfig()
        if isinstance(image, six.string_types):
            image = sources.GlanceImage(image)
        if root_disk_size is not None:
            warnings.warn("root_disk_size is deprecated, use root_size_gb "
                          "instead", DeprecationWarning)
            root_size_gb = root_disk_size

        node = self._check_node_for_deploy(node)
        nics = _nics.NICs(self._api, node, nics)

        try:
            hostname = self._check_hostname(node, hostname)
            root_size_gb = _utils.get_root_disk(root_size_gb, node)

            image._validate(self.connection)

            nics.validate()

            if capabilities is None:
                capabilities = node.instance_info.get('capabilities') or {}

            if self._dry_run:
                LOG.warning('Dry run, not provisioning node %s',
                            _utils.log_node(node))
                return node

            nics.create_and_attach_ports()

            capabilities['boot_option'] = 'netboot' if netboot else 'local'

            updates = {'/instance_info/root_gb': root_size_gb,
                       '/instance_info/capabilities': capabilities,
                       '/extra/%s' % _CREATED_PORTS: nics.created_ports,
                       '/extra/%s' % _ATTACHED_PORTS: nics.attached_ports,
                       '/instance_info/%s' % _os_api.HOSTNAME_FIELD: hostname}
            updates.update(image._node_updates(self.connection))
            if traits is not None:
                updates['/instance_info/traits'] = traits
            if swap_size_mb is not None:
                updates['/instance_info/swap_mb'] = swap_size_mb

            LOG.debug('Updating node %(node)s with %(updates)s',
                      {'node': _utils.log_node(node), 'updates': updates})
            node = self._api.update_node(node, updates)
            self._api.validate_node(node, validate_deploy=True)

            LOG.debug('Generating a configdrive for node %s',
                      _utils.log_node(node))
            with config.build_configdrive_directory(node, hostname) as cd:
                self._api.node_action(node, 'active',
                                      configdrive=cd)
        except Exception:
            exc_info = sys.exc_info()

            try:
                LOG.error('Deploy attempt failed on node %s, cleaning up',
                          _utils.log_node(node))
                self._clean_up(node, nics=nics)
            except Exception:
                LOG.exception('Clean up failed')

            six.reraise(*exc_info)

        LOG.info('Provisioning started on node %s', _utils.log_node(node))

        if wait is not None:
            LOG.debug('Waiting for node %(node)s to reach state active '
                      'with timeout %(timeout)s',
                      {'node': _utils.log_node(node), 'timeout': wait})
            instance = self.wait_for_provisioning([node], timeout=wait)[0]
            LOG.info('Deploy succeeded on node %s', _utils.log_node(node))
        else:
            # Update the node to return it's latest state
            node = self._api.get_node(node, refresh=True)
            instance = _instance.Instance(self._api, node)

        return instance

    def wait_for_provisioning(self, nodes, timeout=None, delay=15):
        """Wait for nodes to be provisioned.

        Loops until all nodes finish provisioning.

        :param nodes: List of nodes (UUID, name, `Node` object or
            :py:class:`metalsmith.Instance`).
        :param timeout: How much time (in seconds) to wait for all nodes
            to finish provisioning. If ``None`` (the default), wait forever
            (more precisely, until the operation times out on server side).
        :param delay: Delay (in seconds) between two provision state checks.
        :return: List of updated :py:class:`metalsmith.Instance` objects if
            all succeeded.
        :raises: :py:class:`metalsmith.exceptions.DeploymentFailure`
            if the deployment failed or timed out for any nodes.
        """
        nodes = self._wait_for_state(nodes, 'active',
                                     timeout=timeout, delay=delay)
        return [_instance.Instance(self._api, node) for node in nodes]

    def _wait_for_state(self, nodes, state, timeout, delay=15):
        if timeout is not None and timeout <= 0:
            raise ValueError("The timeout argument must be a positive int")
        if delay < 0:
            raise ValueError("The delay argument must be a non-negative int")

        failed_nodes = []
        finished_nodes = []

        deadline = time.time() + timeout if timeout is not None else None
        while timeout is None or time.time() < deadline:
            remaining_nodes = []
            for node in nodes:
                node = self._api.get_node(node, refresh=True,
                                          accept_hostname=True)
                if node.provision_state == state:
                    LOG.debug('Node %(node)s reached state %(state)s',
                              {'node': _utils.log_node(node), 'state': state})
                    finished_nodes.append(node)
                elif (node.provision_state == 'error' or
                      node.provision_state.endswith(' failed')):
                    LOG.error('Node %(node)s failed deployment: %(error)s',
                              {'node': _utils.log_node(node),
                               'error': node.last_error})
                    failed_nodes.append(node)
                else:
                    remaining_nodes.append(node)

            if remaining_nodes:
                nodes = remaining_nodes
            else:
                nodes = []
                break

            LOG.debug('Still waiting for the following nodes to reach state '
                      '%(state)s: %(nodes)s',
                      {'state': state,
                       'nodes': ', '.join(_utils.log_node(n) for n in nodes)})
            time.sleep(delay)

        messages = []
        if failed_nodes:
            messages.append('the following nodes failed deployment: %s' %
                            ', '.join('%s (%s)' % (_utils.log_node(node),
                                                   node.last_error)
                                      for node in failed_nodes))
        if nodes:
            messages.append('deployment timed out for nodes %s' %
                            ', '.join(_utils.log_node(node) for node in nodes))

        if messages:
            raise exceptions.DeploymentFailure(
                'Deployment failed: %s' % '; '.join(messages),
                failed_nodes + nodes)
        else:
            LOG.debug('All nodes reached state %s', state)
            return finished_nodes

    def _clean_up(self, node, nics=None):
        if nics is None:
            created_ports = node.extra.get(_CREATED_PORTS, [])
            attached_ports = node.extra.get(_ATTACHED_PORTS, [])
            _nics.detach_and_delete_ports(self._api, node, created_ports,
                                          attached_ports)
        else:
            nics.detach_and_delete_ports()

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

        LOG.debug('Releasing lock on node %s', _utils.log_node(node))
        self._api.release_node(node)

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

        self._clean_up(node)
        self._api.node_action(node, 'deleted')

        LOG.info('Deleting started for node %s', _utils.log_node(node))

        if wait is not None:
            self._wait_for_state([node], 'available', timeout=wait)
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

    def list_instances(self):
        """List instances deployed by metalsmith.

        :return: list of :py:class:`metalsmith.Instance` objects.
        """
        nodes = self._api.list_nodes(provision_state=None, associated=True)
        instances = [i for i in
                     (_instance.Instance(self._api, node) for node in nodes)
                     if i._is_deployed_by_metalsmith]
        return instances
