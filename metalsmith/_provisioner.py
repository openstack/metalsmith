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
import warnings

from openstack import connection
from openstack import exceptions as os_exc

from metalsmith import _instance
from metalsmith import _network_metadata
from metalsmith import _nics
from metalsmith import _scheduler
from metalsmith import _utils
from metalsmith import exceptions
from metalsmith import instance_config
from metalsmith import sources


LOG = logging.getLogger(__name__)

_CREATED_PORTS = 'metalsmith_created_ports'
_ATTACHED_PORTS = 'metalsmith_attached_ports'
_PRESERVE_INSTANCE_INFO_KEYS = {'capabilities', 'traits'}


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

    allocations_cache = dict()

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
            self.connection = connection.Connection(config=cloud_region)

        self._dry_run = dry_run

    def reserve_node(self, resource_class, conductor_group=None,
                     capabilities=None, traits=None, candidates=None,
                     predicate=None, hostname=None):
        """Find and reserve a suitable node.

        Example::

         node = provisioner.reserve_node("compute",
                                         capabilities={"boot_mode": "uefi"})

        :param resource_class: Requested resource class.
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
        :param hostname: Hostname to assign to the instance. Defaults to the
            node's name or UUID.
        :return: reserved `Node` object.
        :raises: :py:class:`metalsmith.exceptions.ReservationFailed`
        """
        capabilities = capabilities or {}
        _utils.check_hostname(hostname)

        if candidates or capabilities or conductor_group or predicate:
            # Predicates, capabilities and conductor groups are not supported
            # by the allocation API natively, so we need to use prefiltering.
            candidates = self._prefilter_nodes(resource_class,
                                               conductor_group=conductor_group,
                                               capabilities=capabilities,
                                               candidates=candidates,
                                               predicate=predicate)

        node = self._reserve_node(resource_class, hostname=hostname,
                                  candidates=candidates, traits=traits,
                                  capabilities=capabilities)[0]
        return node

    def _prefilter_nodes(self, resource_class, conductor_group, capabilities,
                         candidates, predicate):
        """Build a list of candidate nodes for allocation."""
        if candidates:
            try:
                nodes = [self._get_node(node) for node in candidates]
            except os_exc.ResourceNotFound as exc:
                raise exceptions.InvalidNode(str(exc))
        else:
            nodes = list(self.connection.baremetal.nodes(
                details=True,
                associated=False,
                provision_state='available',
                maintenance=False,
                resource_class=resource_class,
                conductor_group=conductor_group))
            if not nodes:
                raise exceptions.NodesNotFound(resource_class, conductor_group)

        filters = [
            _scheduler.NodeTypeFilter(resource_class, conductor_group),
        ]
        if capabilities:
            filters.append(_scheduler.CapabilitiesFilter(capabilities))
        if predicate is not None:
            filters.append(_scheduler.CustomPredicateFilter(predicate))

        return _scheduler.run_filters(filters, nodes)

    def _reserve_node(self, resource_class, hostname=None, candidates=None,
                      traits=None, capabilities=None,
                      update_instance_info=True):
        """Create an allocation with given parameters."""
        if candidates:
            candidates = [
                (node.id if not isinstance(node, str) else node)
                for node in candidates
            ]

        LOG.debug('Creating an allocation for resource class %(rsc)s '
                  'with traits %(traits)s and candidate nodes %(candidates)s',
                  {'rsc': resource_class, 'traits': traits,
                   'candidates': candidates})
        try:
            allocation = self.connection.baremetal.create_allocation(
                name=hostname, candidate_nodes=candidates,
                resource_class=resource_class, traits=traits)
        except os_exc.SDKException as exc:
            # Re-raise the expected exception class
            raise exceptions.ReservationFailed(
                'Failed to create an allocation: %s' % exc)

        node = None
        try:
            try:
                allocation = self.connection.baremetal.wait_for_allocation(
                    allocation)
            except os_exc.SDKException as exc:
                # Re-raise the expected exception class
                raise exceptions.ReservationFailed(
                    'Failed to reserve a node: %s' % exc)

            LOG.info('Successful allocation %(alloc)s for host %(host)s',
                     {'alloc': allocation, 'host': hostname})
            node = self.connection.baremetal.get_node(allocation.node_id)

            if update_instance_info:
                node = self._patch_reserved_node(node, allocation, hostname,
                                                 capabilities)
        except Exception as exc:
            with _utils.reraise_os_exc(
                    exceptions.ReservationFailed,
                    'Failed to delete failed allocation') as expected:
                LOG.error('Processing allocation %(alloc)s for node %(node)s '
                          'failed: %(exc)s; deleting allocation',
                          {'alloc': _utils.log_res(allocation),
                           'node': _utils.log_res(node), 'exc': exc},
                          exc_info=not expected)
                self.connection.baremetal.delete_allocation(allocation)

        LOG.debug('Reserved node: %s', node)
        return node, allocation

    def _patch_reserved_node(self, node, allocation, hostname, capabilities):
        """Make required updates on a newly reserved node."""
        if capabilities:
            patch = [{'path': '/instance_info/capabilities',
                      'op': 'add', 'value': capabilities}]
            LOG.debug('Patching reserved node %(node)s with %(patch)s',
                      {'node': _utils.log_res(node), 'patch': patch})
            return self.connection.baremetal.patch_node(node, patch)
        else:
            return node

    def _check_node_for_deploy(self, node, hostname):
        """Check that node is ready and reserve it if needed.

        These checks are done outside of the try..except block in
        ``provision_node``, so that we don't touch nodes that fail it at all.
        Particularly, we don't want to try clean up nodes that were not
        reserved by us or are in maintenance mode.
        """
        if node.is_maintenance:
            raise exceptions.InvalidNode('Refusing to deploy on node %(node)s '
                                         'which is in maintenance mode due to '
                                         '%(reason)s' %
                                         {'node': _utils.log_res(node),
                                          'reason': node.maintenance_reason})

        allocation = None

        # Make sure the hostname does not correspond to an existing allocation
        # for another node.
        if hostname is not None:
            allocation = self._check_allocation_for_hostname(node, hostname)

        if node.allocation_id:
            if allocation is None:
                # Previously created allocation, verify/update it
                allocation = self._check_and_update_allocation_for_node(
                    node, hostname)
        elif node.instance_id:
            # Old-style reservations with instance_uuid==node.uuid
            if node.instance_id != node.id:
                raise exceptions.InvalidNode(
                    'Node %(node)s already reserved by instance %(inst)s '
                    'outside of metalsmith, cannot deploy on it' %
                    {'node': _utils.log_res(node), 'inst': node.instance_id})
            elif hostname:
                # We have no way to update hostname without allocations
                raise exceptions.InvalidNode(
                    'Node %s does not use allocations, cannot update '
                    'hostname for it' % _utils.log_res(node))
        else:
            # Node is not reserved at all - reserve it
            if not node.resource_class:
                raise exceptions.InvalidNode(
                    'Cannot create an allocation for node %s that '
                    'does not have a resource class set'
                    % _utils.log_res(node))

            if not self._dry_run:
                if not hostname:
                    hostname = _utils.default_hostname(node)
                LOG.debug('Node %(node)s is not reserved yet, reserving for '
                          'hostname %(host)s',
                          {'node': _utils.log_res(node),
                           'host': hostname})
                # Not updating instance_info since it will be updated later
                node, allocation = self._reserve_node(
                    node.resource_class,
                    hostname=hostname,
                    candidates=[node.id],
                    update_instance_info=False)

        return node, allocation

    def _check_allocation_for_hostname(self, node, hostname):
        try:
            allocation = self.connection.baremetal.get_allocation(
                hostname)
        except os_exc.ResourceNotFound:
            return

        if allocation.node_id and allocation.node_id != node.id:
            raise ValueError("The following node already uses "
                             "hostname %(host)s: %(node)s" %
                             {'host': hostname,
                              'node': allocation.node_id})
        else:
            return allocation

    def _check_and_update_allocation_for_node(self, node, hostname=None):
        # No allocation with given hostname, find one corresponding to the
        # node.
        allocation = self.connection.baremetal.get_allocation(
            node.allocation_id)
        if allocation.name and hostname and allocation.name != hostname:
            # Prevent updating of an existing hostname, since we don't
            # understand the intention
            raise exceptions.InvalidNode(
                "Allocation %(alloc)s associated with node %(node)s "
                "uses hostname %(old)s that does not match the expected "
                "hostname %(new)s" %
                {'alloc': _utils.log_res(allocation),
                 'node': _utils.log_res(node),
                 'old': allocation.name,
                 'new': hostname})
        elif not allocation.name and not self._dry_run:
            if not hostname:
                hostname = _utils.default_hostname(node)
            # Set the hostname that was not set in reserve_node.
            LOG.debug('Updating allocation %(alloc)s for node '
                      '%(node)s with hostname %(host)s',
                      {'alloc': _utils.log_res(allocation),
                       'node': _utils.log_res(node),
                       'host': hostname})
            allocation = self.connection.baremetal.update_allocation(
                allocation, name=hostname)

        return allocation

    def provision_node(self, node, image, nics=None, root_size_gb=None,
                       swap_size_mb=None, config=None, hostname=None,
                       netboot=False, capabilities=None, traits=None,
                       wait=None, clean_up_on_failure=True):
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

            * ``{"port": "<port name or ID>"}`` to use the provided pre-created
              port.
            * ``{"network": "<network name or ID>"}`` to create a port on the
              provided network. Optionally, a ``fixed_ip`` argument can be used
              to specify an IP address.
            * ``{"subnet": "<subnet name or ID>"}`` to create a port with an IP
              address from the provided subnet. The network is determined from
              the subnet.

        :param root_size_gb: The size of the root partition. By default
            the value of the local_gb property is used.
        :param swap_size_mb: The size of the swap partition. It's an error
            to specify it for a whole disk image.
        :param config: configuration to pass to the instance, one of
            objects from :py:mod:`metalsmith.instance_config`.
        :param hostname: Hostname to assign to the instance. If provided,
            overrides the ``hostname`` passed to ``reserve_node``.
        :param netboot: Whether to use networking boot for final instances.
            Deprecated and does not work in Ironic Zed.
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
        :param clean_up_on_failure: If True, then on failure the node is
            cleared of instance information, VIFs are detached, created ports
            and allocations are deleted.
        :return: :py:class:`metalsmith.Instance` object with the current
            status of provisioning. If ``wait`` is not ``None``, provisioning
            is already finished.
        :raises: :py:class:`metalsmith.exceptions.Error`
        """
        if netboot:
            warnings.warn("Network boot is deprecated and does not work in "
                          "Ironic Zed", DeprecationWarning)

        if config is None:
            config = instance_config.GenericConfig()
        if isinstance(image, str):
            image = sources.GlanceImage(image)

        _utils.check_hostname(hostname)

        try:
            node = self._get_node(node)
        except Exception as exc:
            raise exceptions.InvalidNode('Cannot find node %(node)s: %(exc)s' %
                                         {'node': node, 'exc': exc})

        node, allocation = self._check_node_for_deploy(node, hostname)
        nics = _nics.NICs(self.connection, node, nics,
                          hostname=allocation and allocation.name or None)

        try:
            root_size_gb = _utils.get_root_disk(root_size_gb, node)

            image._validate(self.connection, root_size_gb)

            nics.validate()

            if capabilities is None:
                capabilities = node.instance_info.get('capabilities') or {}

            if self._dry_run:
                LOG.warning('Dry run, not provisioning node %s',
                            _utils.log_res(node))
                return node

            nics.create_and_attach_ports()

            capabilities['boot_option'] = 'netboot' if netboot else 'local'

            instance_info = self._clean_instance_info(node.instance_info)
            if root_size_gb is not None:
                instance_info['root_gb'] = root_size_gb
            instance_info['capabilities'] = capabilities
            if hostname:
                instance_info['display_name'] = hostname

            extra = node.extra.copy()
            extra[_CREATED_PORTS] = nics.created_ports
            extra[_ATTACHED_PORTS] = nics.attached_ports
            instance_info.update(image._node_updates(self.connection))
            if traits is not None:
                instance_info['traits'] = traits
            if swap_size_mb is not None:
                instance_info['swap_mb'] = swap_size_mb

            LOG.debug('Updating node %(node)s with instance info %(iinfo)s '
                      'and extras %(extra)s', {'node': _utils.log_res(node),
                                               'iinfo': instance_info,
                                               'extra': extra})
            node = self.connection.baremetal.update_node(
                node, instance_info=instance_info, extra=extra)
            self.connection.baremetal.validate_node(node)

            network_data = _network_metadata.create_network_metadata(
                self.connection, node.extra.get(_ATTACHED_PORTS))

            LOG.debug('Generating a configdrive for node %s',
                      _utils.log_res(node))
            cd = config.generate(node, _utils.hostname_for(node, allocation),
                                 network_data)
            LOG.debug('Starting provisioning of node %s', _utils.log_res(node))
            self.connection.baremetal.set_node_provision_state(
                node, 'active', config_drive=cd)
        except Exception:
            with _utils.reraise_os_exc(
                    exceptions.DeploymentFailed) as expected:
                if clean_up_on_failure:
                    LOG.error('Deploy attempt failed on node %s, cleaning up',
                              _utils.log_res(node), exc_info=not expected)
                    self._clean_up(node, nics=nics)

        LOG.info('Provisioning started on node %s', _utils.log_res(node))

        if wait is not None:
            LOG.debug('Waiting for node %(node)s to reach state active '
                      'with timeout %(timeout)s',
                      {'node': _utils.log_res(node), 'timeout': wait})
            instance = self.wait_for_provisioning([node], timeout=wait)[0]
            LOG.info('Deploy succeeded on node %s', _utils.log_res(node))
        else:
            # Update the node to return it's latest state
            node = self.connection.baremetal.get_node(node.id)
            instance = _instance.Instance(self.connection, node, allocation)

        return instance

    def wait_for_provisioning(self, nodes, timeout=None):
        """Wait for nodes to be provisioned.

        Loops until all nodes finish provisioning.

        :param nodes: List of nodes (UUID, name, `Node` object or
            :py:class:`metalsmith.Instance`).
        :param timeout: How much time (in seconds) to wait for all nodes
            to finish provisioning. If ``None`` (the default), wait forever
            (more precisely, until the operation times out on server side).
        :return: List of updated :py:class:`metalsmith.Instance` objects if
            all succeeded.
        :raises: :py:class:`metalsmith.exceptions.DeploymentFailed`
            if deployment fails or times out.
        :raises: :py:class:`metalsmith.exceptions.InstanceNotFound`
            if requested nodes cannot be found.
        """
        nodes = [self._find_node_and_allocation(n)[0] for n in nodes]
        try:
            nodes = self.connection.baremetal.wait_for_nodes_provision_state(
                nodes, 'active', timeout=timeout)
        except os_exc.ResourceTimeout as exc:
            raise exceptions.DeploymentTimeout(str(exc))
        except os_exc.SDKException as exc:
            raise exceptions.DeploymentFailed(str(exc))

        # Using _get_instance in case the deployment started by something
        # external that uses allocations.
        return [self._get_instance(node) for node in nodes]

    def _clean_instance_info(self, instance_info):
        return {key: value
                for key, value in instance_info.items()
                if key in _PRESERVE_INSTANCE_INFO_KEYS}

    def _clean_up(self, node, nics=None, remove_instance_info=True):
        if nics is None:
            created_ports = node.extra.get(_CREATED_PORTS, [])
            attached_ports = node.extra.get(_ATTACHED_PORTS, [])
            _nics.detach_and_delete_ports(self.connection, node,
                                          created_ports, attached_ports)
        else:
            nics.detach_and_delete_ports()

        extra = node.extra.copy()
        for item in (_CREATED_PORTS, _ATTACHED_PORTS):
            extra.pop(item, None)

        kwargs = {}
        if node.allocation_id and node.provision_state != 'active':
            # Try to remove allocation (it will fail for active nodes)
            LOG.debug('Trying to remove allocation %(alloc)s for node '
                      '%(node)s', {'alloc': node.allocation_id,
                                   'node': _utils.log_res(node)})
            try:
                self.connection.baremetal.delete_allocation(node.allocation_id)
            except Exception as exc:
                LOG.debug('Failed to remove allocation %(alloc)s for %(node)s:'
                          ' %(exc)s',
                          {'alloc': node.allocation_id,
                           'node': _utils.log_res(node), 'exc': exc})
        elif not node.allocation_id:
            # Old-style reservations have to be cleared explicitly
            kwargs['instance_id'] = None

        try:
            if remove_instance_info:
                LOG.debug('Updating node %(node)s with empty instance info '
                          '(was %(iinfo)s) and extras %(extra)s',
                          {'node': _utils.log_res(node),
                           'iinfo': node.instance_info,
                           'extra': extra})
                self.connection.baremetal.update_node(
                    node, instance_info={}, extra=extra, **kwargs)
            else:
                LOG.debug('Updating node %(node)s with extras %(extra)s',
                          {'node': _utils.log_res(node), 'extra': extra})
                self.connection.baremetal.update_node(
                    node, extra=extra, **kwargs)
        except Exception as exc:
            LOG.debug('Failed to clear node %(node)s extra: %(exc)s',
                      {'node': _utils.log_res(node), 'exc': exc})

    def unprovision_node(self, node, wait=None):
        """Unprovision a previously provisioned node.

        :param node: `Node` object, :py:class:`metalsmith.Instance`,
            hostname, UUID or node name.
        :param wait: How many seconds to wait for the process to finish,
            None to return immediately.
        :return: the latest `Node` object.
        :raises: :py:class:`metalsmith.exceptions.DeploymentFailed`
            if undeployment fails.
        :raises: :py:class:`metalsmith.exceptions.DeploymentTimeout`
            if undeployment times out.
        :raises: :py:class:`metalsmith.exceptions.InstanceNotFound`
            if requested node cannot be found.
        """
        node = self._find_node_and_allocation(node)[0]
        if self._dry_run:
            LOG.warning("Dry run, not unprovisioning")
            return

        self._clean_up(node, remove_instance_info=False)
        try:
            node = self.connection.baremetal.set_node_provision_state(
                node, 'deleted', wait=False)

            LOG.info('Deleting started for node %s', _utils.log_res(node))

            if wait is None:
                return node

            node = self.connection.baremetal.wait_for_nodes_provision_state(
                [node], 'available', timeout=wait)[0]
        except os_exc.ResourceTimeout as exc:
            raise exceptions.DeploymentTimeout(str(exc))
        except os_exc.SDKException as exc:
            raise exceptions.DeploymentFailed(str(exc))

        LOG.info('Node %s undeployed successfully', _utils.log_res(node))
        return node

    def show_instance(self, instance_id):
        """Show information about instance.

        :param instance_id: hostname, UUID or node name.
        :return: :py:class:`metalsmith.Instance` object.
        :raises: :py:class:`metalsmith.exceptions.InstanceNotFound`
            if the instance is not a valid instance.
        """
        return self.show_instances([instance_id])[0]

    def show_instances(self, instances):
        """Show information about instance.

        More efficient than calling :meth:`show_instance` in a loop, because
        it caches the node list.

        :param instances: list of hostnames, UUIDs or node names.
        :return: list of :py:class:`metalsmith.Instance` objects in the same
            order as ``instances``.
        :raises: :py:class:`metalsmith.exceptions.InstanceNotFound`
            if one of the instances cannot be found or the found node is
            not a valid instance.
        """
        result = [self._get_instance(inst) for inst in instances]
        # NOTE(dtantsur): do not accept node names as valid instances if they
        # are not deployed or being deployed.
        missing = [inst for (res, inst) in zip(result, instances)
                   if res.state == _instance.InstanceState.UNKNOWN]
        if missing:
            raise exceptions.InstanceNotFound(
                "Node(s)/instance(s) %s are not valid instances"
                % ', '.join(map(str, missing)))
        return result

    def list_instances(self):
        """List instances deployed by metalsmith.

        :return: list of :py:class:`metalsmith.Instance` objects.
        """
        nodes = self.connection.baremetal.nodes(associated=True, details=True)
        Provisioner.allocations_cache = {
            a.id: a for a in self.connection.baremetal.allocations()}
        instances = [i for i in map(self._get_instance, nodes)
                     if i.state != _instance.InstanceState.UNKNOWN]
        return instances

    def _get_node(self, node, refresh=False):
        """A helper to find and return a node."""
        if isinstance(node, str):
            return self.connection.baremetal.get_node(node)
        elif hasattr(node, 'node'):
            # Instance object
            node = node.node
        else:
            node = node

        if refresh:
            return self.connection.baremetal.get_node(node.id)
        else:
            return node

    def _find_node_and_allocation(self, node, refresh=False):
        try:
            if (not isinstance(node, str)
                    or not _utils.is_hostname_safe(node)):
                return self._get_node(node, refresh=refresh), None

            try:
                allocation = self.connection.baremetal.get_allocation(node)
            except os_exc.ResourceNotFound:
                return self._get_node(node, refresh=refresh), None
        except os_exc.ResourceNotFound as exc:
            raise exceptions.InstanceNotFound(str(exc))

        if allocation.node_id:
            try:
                return (self.connection.baremetal.get_node(allocation.node_id),
                        allocation)
            except os_exc.ResourceNotFound:
                raise exceptions.InstanceNotFound(
                    'Node %(node)s associated with allocation '
                    '%(alloc)s was not found' %
                    {'node': allocation.node_id,
                     'alloc': allocation.id})
        else:
            raise exceptions.InstanceNotFound(
                'Allocation %s exists but is not associated '
                'with a node' % node)

    def _get_instance(self, ident):
        if hasattr(ident, 'allocation_id'):
            node = ident
            try:
                try:
                    allocation = Provisioner.allocations_cache[
                        node.instance_id]
                except KeyError:
                    allocation = self.connection.baremetal.get_allocation(
                        node.allocation_id)
            except os_exc.ResourceNotFound as exc:
                raise exceptions.InstanceNotFound(str(exc))
        else:
            node, allocation = self._find_node_and_allocation(ident)
            if allocation is None and node.allocation_id:
                try:
                    allocation = self.connection.baremetal.get_allocation(
                        node.allocation_id)
                except os_exc.ResourceNotFound as exc:
                    raise exceptions.InstanceNotFound(str(exc))
        return _instance.Instance(self.connection, node,
                                  allocation=allocation)
