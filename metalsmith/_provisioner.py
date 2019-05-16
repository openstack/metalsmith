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
import sys
import warnings

from openstack import connection
from openstack import exceptions as os_exc
import six

from metalsmith import _config
from metalsmith import _instance
from metalsmith import _nics
from metalsmith import _scheduler
from metalsmith import _utils
from metalsmith import exceptions
from metalsmith import sources


LOG = logging.getLogger(__name__)

_CREATED_PORTS = 'metalsmith_created_ports'
_ATTACHED_PORTS = 'metalsmith_attached_ports'
_PRESERVE_INSTANCE_INFO_KEYS = {'capabilities', 'traits',
                                _utils.HOSTNAME_FIELD}


class Provisioner(_utils.GetNodeMixin):
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
        self._check_hostname(hostname)

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
                                  capabilities=capabilities)
        return node

    def _prefilter_nodes(self, resource_class, conductor_group, capabilities,
                         candidates, predicate):
        """Build a list of candidate nodes for allocation."""
        if candidates:
            nodes = [self._get_node(node) for node in candidates]
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
                (node.id if not isinstance(node, six.string_types) else node)
                for node in candidates
            ]

        LOG.debug('Creating an allocation for resource class %(rsc)s '
                  'with traits %(traits)s and candidate nodes %(candidates)s',
                  {'rsc': resource_class, 'traits': traits,
                   'candidates': candidates})
        allocation = self.connection.baremetal.create_allocation(
            name=hostname, candidate_nodes=candidates,
            resource_class=resource_class, traits=traits)

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
            exc_info = sys.exc_info()

            try:
                LOG.error('Processing allocation %(alloc)s for node %(node)s '
                          'failed: %(exc)s; deleting allocation',
                          {'alloc': _utils.log_res(allocation),
                           'node': _utils.log_res(node), 'exc': exc})
                self.connection.baremetal.delete_allocation(allocation)
            except Exception:
                LOG.exception('Failed to delete failed allocation')

            six.reraise(*exc_info)

        LOG.debug('Reserved node: %s', node)
        return node

    def _patch_reserved_node(self, node, allocation, hostname, capabilities):
        """Make required updates on a newly reserved node."""
        if not hostname:
            hostname = _utils.default_hostname(node)
        patch = [
            {'path': '/instance_info/%s' % _utils.HOSTNAME_FIELD,
             'op': 'add', 'value': hostname}
        ]

        if capabilities:
            patch.append({'path': '/instance_info/capabilities',
                          'op': 'add', 'value': capabilities})

        LOG.debug('Patching reserved node %(node)s with %(patch)s',
                  {'node': _utils.log_res(node), 'patch': patch})
        return self.connection.baremetal.patch_node(node, patch)

    def _check_node_for_deploy(self, node):
        """Check that node is ready and reserve it if needed.

        These checks are done outside of the try..except block in
        ``provision_node``, so that we don't touch nodes that fail it at all.
        Particularly, we don't want to try clean up nodes that were not
        reserved by us or are in maintenance mode.
        """
        try:
            node = self._get_node(node)
        except Exception as exc:
            raise exceptions.InvalidNode('Cannot find node %(node)s: %(exc)s' %
                                         {'node': node, 'exc': exc})

        if not node.instance_id:
            if not node.resource_class:
                raise exceptions.InvalidNode(
                    'Cannot create an allocation for node %s that '
                    'does not have a resource class set'
                    % _utils.log_res(node))

            if not self._dry_run:
                LOG.debug('Node %s not reserved yet, reserving',
                          _utils.log_res(node))
                # Not updating instance_info since it will be updated later
                node = self._reserve_node(node.resource_class,
                                          candidates=[node.id],
                                          update_instance_info=False)
        elif node.instance_id != node.id and not node.allocation_id:
            raise exceptions.InvalidNode('Node %(node)s already reserved '
                                         'by instance %(inst)s outside of '
                                         'metalsmith, cannot deploy on it' %
                                         {'node': _utils.log_res(node),
                                          'inst': node.instance_id})

        if node.is_maintenance:
            raise exceptions.InvalidNode('Refusing to deploy on node %(node)s '
                                         'which is in maintenance mode due to '
                                         '%(reason)s' %
                                         {'node': _utils.log_res(node),
                                          'reason': node.maintenance_reason})

        return node

    def _check_hostname(self, hostname, node=None):
        """Check the provided host name.

        :raises: ValueError on inappropriate value of ``hostname``
        """
        if hostname is None:
            return

        if not _utils.is_hostname_safe(hostname):
            raise ValueError("%s cannot be used as a hostname" % hostname)

        existing = self._find_node_by_hostname(hostname)
        if (existing is not None and node is not None
                and existing.id != node.id):
            raise ValueError("The following node already uses hostname "
                             "%(host)s: %(node)s" %
                             {'host': hostname,
                              'node': _utils.log_res(existing)})

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
        :param config: :py:class:`metalsmith.InstanceConfig` object with
            the configuration to pass to the instance.
        :param hostname: Hostname to assign to the instance. If provided,
            overrides the ``hostname`` passed to ``reserve_node``.
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
        :return: :py:class:`metalsmith.Instance` object with the current
            status of provisioning. If ``wait`` is not ``None``, provisioning
            is already finished.
        :raises: :py:class:`metalsmith.exceptions.Error`
        """
        if config is None:
            config = _config.InstanceConfig()
        if isinstance(image, six.string_types):
            image = sources.GlanceImage(image)

        node = self._check_node_for_deploy(node)
        nics = _nics.NICs(self.connection, node, nics)

        try:
            self._check_hostname(hostname, node=node)
            root_size_gb = _utils.get_root_disk(root_size_gb, node)

            image._validate(self.connection)

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
            instance_info['root_gb'] = root_size_gb
            instance_info['capabilities'] = capabilities
            if hostname:
                instance_info[_utils.HOSTNAME_FIELD] = hostname
            elif not instance_info.get(_utils.HOSTNAME_FIELD):
                instance_info[_utils.HOSTNAME_FIELD] = _utils.default_hostname(
                    node)

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

            LOG.debug('Generating a configdrive for node %s',
                      _utils.log_res(node))
            LOG.debug('Starting provisioning of node %s', _utils.log_res(node))
            self.connection.baremetal.set_node_provision_state(
                node, 'active', config_drive=config.generate(node))
        except Exception:
            exc_info = sys.exc_info()

            try:
                LOG.error('Deploy attempt failed on node %s, cleaning up',
                          _utils.log_res(node))
                self._clean_up(node, nics=nics)
            except Exception:
                LOG.exception('Clean up failed')

            six.reraise(*exc_info)

        LOG.info('Provisioning started on node %s', _utils.log_res(node))

        if wait is not None:
            LOG.debug('Waiting for node %(node)s to reach state active '
                      'with timeout %(timeout)s',
                      {'node': _utils.log_res(node), 'timeout': wait})
            instance = self.wait_for_provisioning([node], timeout=wait)[0]
            LOG.info('Deploy succeeded on node %s', _utils.log_res(node))
        else:
            # Update the node to return it's latest state
            node = self._get_node(node, refresh=True)
            instance = self._get_instance(node)

        return instance

    def wait_for_provisioning(self, nodes, timeout=None, delay=None):
        """Wait for nodes to be provisioned.

        Loops until all nodes finish provisioning.

        :param nodes: List of nodes (UUID, name, `Node` object or
            :py:class:`metalsmith.Instance`).
        :param timeout: How much time (in seconds) to wait for all nodes
            to finish provisioning. If ``None`` (the default), wait forever
            (more precisely, until the operation times out on server side).
        :param delay: DEPRECATED, do not use.
        :return: List of updated :py:class:`metalsmith.Instance` objects if
            all succeeded.
        :raises: :py:class:`metalsmith.exceptions.DeploymentFailure`
            if the deployment failed or timed out for any nodes.
        """
        if delay is not None:
            warnings.warn("The delay argument to wait_for_provisioning is "
                          "deprecated and has not effect", DeprecationWarning)
        nodes = [self._get_node(n, accept_hostname=True) for n in nodes]
        nodes = self.connection.baremetal.wait_for_nodes_provision_state(
            nodes, 'active', timeout=timeout)
        # Using _get_instance in case the deployment started by something
        # external that uses allocations.
        return [self._get_instance(node) for node in nodes]

    def _clean_instance_info(self, instance_info):
        return {key: value
                for key, value in instance_info.items()
                if key in _PRESERVE_INSTANCE_INFO_KEYS}

    def _clean_up(self, node, nics=None):
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
                          {'alloc': node.allocaiton_id,
                           'node': _utils.log_res(node), 'exc': exc})
        elif not node.allocation_id:
            # Old-style reservations have to be cleared explicitly
            kwargs['instance_id'] = None

        LOG.debug('Updating node %(node)s with empty instance info (was '
                  '%(iinfo)s) and extras %(extra)s',
                  {'node': _utils.log_res(node),
                   'iinfo': node.instance_info,
                   'extra': extra})
        try:
            self.connection.baremetal.update_node(
                node, instance_info={}, extra=extra, **kwargs)
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
        """
        node = self._get_node(node, accept_hostname=True)
        if self._dry_run:
            LOG.warning("Dry run, not unprovisioning")
            return

        self._clean_up(node)
        node = self.connection.baremetal.set_node_provision_state(
            node, 'deleted', wait=False)

        LOG.info('Deleting started for node %s', _utils.log_res(node))

        if wait is not None:
            node = self.connection.baremetal.wait_for_nodes_provision_state(
                [node], 'available', timeout=wait)[0]
            LOG.info('Node %s undeployed successfully', _utils.log_res(node))

        return node

    def show_instance(self, instance_id):
        """Show information about instance.

        :param instance_id: hostname, UUID or node name.
        :return: :py:class:`metalsmith.Instance` object.
        :raises: :py:class:`metalsmith.exceptions.InvalidInstance`
            if the instance is not a valid instance.
        """
        return self.show_instances([instance_id])[0]

    def _get_instance(self, ident):
        node = self._get_node(ident, accept_hostname=True)
        if node.allocation_id:
            allocation = self.connection.baremetal.get_allocation(
                node.allocation_id)
        else:
            allocation = None
        return _instance.Instance(self.connection, node,
                                  allocation=allocation)

    def show_instances(self, instances):
        """Show information about instance.

        More efficient than calling :meth:`show_instance` in a loop, because
        it caches the node list.

        :param instances: list of hostnames, UUIDs or node names.
        :return: list of :py:class:`metalsmith.Instance` objects in the same
            order as ``instances``.
        :raises: :py:class:`metalsmith.exceptions.InvalidInstance`
            if one of the instances are not valid instances.
        """
        with self._cache_node_list_for_lookup():
            result = [self._get_instance(inst) for inst in instances]
        # NOTE(dtantsur): do not accept node names as valid instances if they
        # are not deployed or being deployed.
        missing = [inst for (res, inst) in zip(result, instances)
                   if res.state == _instance.InstanceState.UNKNOWN]
        if missing:
            raise exceptions.InvalidInstance(
                "Node(s)/instance(s) %s are not valid instances"
                % ', '.join(map(str, missing)))
        return result

    def list_instances(self):
        """List instances deployed by metalsmith.

        :return: list of :py:class:`metalsmith.Instance` objects.
        """
        nodes = self.connection.baremetal.nodes(associated=True, details=True)
        instances = [i for i in map(self._get_instance, nodes)
                     if i.state != _instance.InstanceState.UNKNOWN]
        return instances
