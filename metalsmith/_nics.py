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

import collections.abc
import logging

from openstack import exceptions as sdk_exc

from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)


class NICs(object):
    """Requested NICs."""

    def __init__(self, connection, node, nics, hostname=None):
        if nics is None:
            nics = []

        if not isinstance(nics, collections.abc.Sequence):
            raise TypeError("NICs must be a list of dicts")

        for nic in nics:
            if not isinstance(nic, collections.abc.Mapping):
                raise TypeError("Each NIC must be a dict got %s" % nic)

        self._node = node
        self._connection = connection
        self._nics = nics
        self._validated = None
        self._hostname = hostname
        self.created_ports = []
        self.attached_ports = []

    def validate(self):
        """Validate provided NIC records."""
        if self._validated is not None:
            return

        result = []
        for nic in self._nics:
            if 'port' in nic:
                result.append(('port', self._get_port(nic)))
            elif 'network' in nic:
                result.append(('network', self._get_network(nic)))
            elif 'subnet' in nic:
                result.append(('subnet', self._get_subnet(nic)))
            else:
                raise exceptions.InvalidNIC(
                    'Unknown NIC record type, export "port", "subnet" or '
                    '"network", got %s' % nic)

        self._validated = result

    def create_and_attach_ports(self):
        """Attach ports to the node, creating them if requested."""
        self.validate()

        for nic_type, nic in self._validated:
            if nic_type != 'port':
                # The 'binding:host_id' must be set to ensure IP allocation
                # is not deferred.
                # See: https://storyboard.openstack.org/#!/story/2009715
                port = self._connection.network.create_port(
                    binding_host_id=self._node.id, **nic)
                self.created_ports.append(port.id)
                LOG.info('Created port %(port)s for node %(node)s with '
                         '%(nic)s', {'port': _utils.log_res(port),
                                     'node': _utils.log_res(self._node),
                                     'nic': nic})
            else:
                # The 'binding:host_id' must be set to ensure IP allocation
                # is not deferred.
                # See: https://storyboard.openstack.org/#!/story/2009715
                self._connection.network.update_port(
                    nic, binding_host_id=self._node.id)
                port = nic

            self._connection.baremetal.attach_vif_to_node(self._node,
                                                          port.id)
            LOG.info('Attached port %(port)s to node %(node)s',
                     {'port': _utils.log_res(port),
                      'node': _utils.log_res(self._node)})
            self.attached_ports.append(port.id)

    def detach_and_delete_ports(self):
        """Detach attached port and delete previously created ones."""
        detach_and_delete_ports(self._connection, self._node,
                                self.created_ports, self.attached_ports)

    def _get_port(self, nic):
        """Validate and get the NIC information for a port.

        :param nic: NIC information in the form ``{"port": "<port ident>"}``.
        :returns: `Port` object to use.
        """
        unexpected = set(nic) - {'port'}
        if unexpected:
            raise exceptions.InvalidNIC(
                'Unexpected fields for a port: %s' % ', '.join(unexpected))

        try:
            port = self._connection.network.find_port(
                nic['port'], ignore_missing=False)
        except sdk_exc.SDKException as exc:
            raise exceptions.InvalidNIC(
                'Cannot find port %(port)s: %(error)s' %
                {'port': nic['port'], 'error': exc})

        return port

    def _get_network(self, nic):
        """Validate and get the NIC information for a network.

        :param nic: NIC information in the form ``{"network": "<net ident>"}``
            or ``{"network": "<net ident>", "fixed_ip": "<desired IP>"}``.
        :returns: keyword arguments to use when creating a port.
        """
        unexpected = set(nic) - {'network', 'fixed_ip', 'subnet'}
        if unexpected:
            raise exceptions.InvalidNIC(
                'Unexpected fields for a network: %s' % ', '.join(unexpected))

        try:
            network = self._connection.network.find_network(
                nic['network'], ignore_missing=False)
        except sdk_exc.SDKException as exc:
            raise exceptions.InvalidNIC(
                'Cannot find network %(net)s: %(error)s' %
                {'net': nic['network'], 'error': exc})

        fixed_ip = {}
        if nic.get('fixed_ip'):
            fixed_ip['ip_address'] = nic['fixed_ip']
        if nic.get('subnet'):
            try:
                subnet = self._connection.network.find_subnet(
                    nic['subnet'], network_id=network.id, ignore_missing=False)
            except sdk_exc.SDKException as exc:
                raise exceptions.InvalidNIC(
                    'Cannot find subnet %(subnet)s on network %(net)s: '
                    '%(error)s' %
                    {'net': nic['network'], 'subnet': nic['subnet'],
                     'error': exc})

            fixed_ip['subnet_id'] = subnet.id

        port_args = {'network_id': network.id}
        if fixed_ip:
            port_args['fixed_ips'] = [fixed_ip]
        if self._hostname:
            port_args['name'] = '%s-%s' % (self._hostname, network.name)

        return port_args

    def _get_subnet(self, nic):
        """Validate and get the NIC information for a subnet.

        :param nic: NIC information in the form ``{"subnet": "<id or name>"}``.
        :returns: keyword arguments to use when creating a port.
        """
        unexpected = set(nic) - {'subnet'}
        if unexpected:
            raise exceptions.InvalidNIC(
                'Unexpected fields for a subnet: %s' % ', '.join(unexpected))

        try:
            subnet = self._connection.network.find_subnet(
                nic['subnet'], ignore_missing=False)
        except sdk_exc.SDKException as exc:
            raise exceptions.InvalidNIC(
                'Cannot find subnet %(sub)s: %(error)s' %
                {'sub': nic['subnet'], 'error': exc})

        try:
            network = self._connection.network.get_network(subnet.network_id)
        except sdk_exc.SDKException as exc:
            raise exceptions.InvalidNIC(
                'Cannot find network %(net)s for subnet %(sub)s: %(error)s' %
                {'net': subnet.network_id, 'sub': nic['subnet'], 'error': exc})

        port_args = {'network_id': network.id,
                     'fixed_ips': [{'subnet_id': subnet.id}]}
        if self._hostname:
            port_args['name'] = '%s-%s' % (self._hostname, network.name)
        return port_args


def detach_and_delete_ports(connection, node, created_ports, attached_ports):
    """Detach attached port and delete previously created ones.

    :param connection: `openstacksdk.Connection` instance.
    :param node: `Node` object to detach ports from.
    :param created_ports: List of IDs of previously created ports.
    :param attached_ports: List of IDs of previously attached_ports.
    """
    for port_id in set(attached_ports + created_ports):
        LOG.debug('Detaching port %(port)s from node %(node)s',
                  {'port': port_id, 'node': _utils.log_res(node)})
        try:
            connection.baremetal.detach_vif_from_node(node, port_id)
        except Exception as exc:
            LOG.debug('Failed to remove VIF %(vif)s from node %(node)s, '
                      'assuming already removed: %(exc)s',
                      {'vif': port_id, 'node': _utils.log_res(node),
                       'exc': exc})

    for port_id in created_ports:
        LOG.debug('Deleting port %s', port_id)
        try:
            connection.network.delete_port(port_id, ignore_missing=False)
        except Exception as exc:
            LOG.warning('Failed to delete neutron port %(port)s: %(exc)s',
                        {'port': port_id, 'exc': exc})
        else:
            LOG.info('Deleted port %(port)s for node %(node)s',
                     {'port': port_id, 'node': _utils.log_res(node)})
