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

from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)


class NICs(object):
    """Requested NICs."""

    def __init__(self, api, node, nics):
        if nics is None:
            nics = []

        if not isinstance(nics, collections.Sequence):
            raise TypeError("NICs must be a list of dicts")

        for nic in nics:
            if not isinstance(nic, collections.Mapping):
                raise TypeError("Each NIC must be a dict got %s" % nic)

        self._node = node
        self._api = api
        self._nics = nics
        self._validated = None
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
            else:
                raise exceptions.InvalidNIC(
                    'Unknown NIC record type, export "port" or "network", '
                    'got %s' % nic)

        self._validated = result

    def create_and_attach_ports(self):
        """Attach ports to the node, creating them if requested."""
        self.validate()

        for nic_type, nic in self._validated:
            if nic_type == 'network':
                port = self._api.connection.network.create_port(**nic)
                self.created_ports.append(port.id)
                LOG.info('Created port %(port)s for node %(node)s with '
                         '%(nic)s', {'port': _utils.log_res(port),
                                     'node': _utils.log_node(self._node),
                                     'nic': nic})
            else:
                port = nic

            self._api.attach_port_to_node(self._node.uuid, port.id)
            LOG.info('Attached port %(port)s to node %(node)s',
                     {'port': _utils.log_res(port),
                      'node': _utils.log_node(self._node)})
            self.attached_ports.append(port.id)

    def detach_and_delete_ports(self):
        """Detach attached port and delete previously created ones."""
        detach_and_delete_ports(self._api, self._node, self.created_ports,
                                self.attached_ports)

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
            port = self._api.connection.network.find_port(
                nic['port'], ignore_missing=False)
        except Exception as exc:
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
        unexpected = set(nic) - {'network', 'fixed_ip'}
        if unexpected:
            raise exceptions.InvalidNIC(
                'Unexpected fields for a network: %s' % ', '.join(unexpected))

        try:
            network = self._api.connection.network.find_network(
                nic['network'], ignore_missing=False)
        except Exception as exc:
            raise exceptions.InvalidNIC(
                'Cannot find network %(net)s: %(error)s' %
                {'net': nic['network'], 'error': exc})

        port_args = {'network_id': network.id}
        if nic.get('fixed_ip'):
            port_args['fixed_ips'] = [{'ip_address': nic['fixed_ip']}]

        return port_args


def detach_and_delete_ports(api, node, created_ports, attached_ports):
    """Detach attached port and delete previously created ones.

    :param api: `Api` instance.
    :param node: `Node` object to detach ports from.
    :param created_ports: List of IDs of previously created ports.
    :param attached_ports: List of IDs of previously attached_ports.
    """
    for port_id in set(attached_ports + created_ports):
        LOG.debug('Detaching port %(port)s from node %(node)s',
                  {'port': port_id, 'node': node.uuid})
        try:
            api.detach_port_from_node(node, port_id)
        except Exception as exc:
            LOG.debug('Failed to remove VIF %(vif)s from node %(node)s, '
                      'assuming already removed: %(exc)s',
                      {'vif': port_id, 'node': _utils.log_node(node),
                       'exc': exc})

    for port_id in created_ports:
        LOG.debug('Deleting port %s', port_id)
        try:
            api.connection.network.delete_port(port_id,
                                               ignore_missing=False)
        except Exception as exc:
            LOG.warning('Failed to delete neutron port %(port)s: %(exc)s',
                        {'port': port_id, 'exc': exc})
        else:
            LOG.info('Deleted port %(port)s for node %(node)s',
                     {'port': port_id, 'node': _utils.log_node(node)})
