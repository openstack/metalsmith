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

import enum
import logging

from metalsmith import _utils


LOG = logging.getLogger(__name__)

_PROGRESS_STATES = frozenset(['deploying', 'wait call-back',
                              'deploy complete'])
_ACTIVE_STATES = frozenset(['active'])
_ERROR_STATES = frozenset(['error', 'deploy failed'])
_RESERVED_STATES = frozenset(['available'])


class InstanceState(enum.Enum):
    """A state of an instance."""

    DEPLOYING = 'deploying'
    """Provisioning is in progress.

    This includes the case when a node is still in the ``available`` state, but
    already has an instance associated with it.
    """

    ACTIVE = 'active'
    """The instance is provisioned."""

    MAINTENANCE = 'maintenance'
    """The instance is provisioned but is in the maintenance mode."""

    ERROR = 'error'
    """The instance has a failure."""

    UNKNOWN = 'unknown'
    """The node is in an unexpected state.

    It can be unprovisioned or modified by a third party.
    """

    @property
    def is_deployed(self):
        """Whether the state designates a finished deployment."""
        return self in _DEPLOYED_STATES

    @property
    def is_healthy(self):
        """Whether the state is considered healthy."""
        return self in _HEALTHY_STATES


_HEALTHY_STATES = frozenset([InstanceState.ACTIVE, InstanceState.DEPLOYING])
_DEPLOYED_STATES = frozenset([InstanceState.ACTIVE, InstanceState.MAINTENANCE])


class Instance(object):
    """Instance status in metalsmith."""

    network_cache = dict()

    def __init__(self, connection, node, allocation=None):
        self._connection = connection
        self._uuid = node.id
        self._node = node
        self._allocation = allocation

    @property
    def allocation(self):
        """Allocation object associated with the node (if any)."""
        return self._allocation

    @property
    def hostname(self):
        """Node's hostname."""
        return _utils.hostname_for(self._node, self._allocation)

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
        return self.state.is_deployed

    @property
    def is_healthy(self):
        """Whether the instance is not at fault or maintenance."""
        return self.state.is_healthy and not self._node.is_maintenance

    def nics(self):
        """List NICs for this instance.

        :return: List of `Port` objects with additional ``network`` fields
            with full representations of their networks.
        """
        result = []
        ports_query = {'binding:host_id': self.node.id}
        ports = self._connection.network.ports(**ports_query)
        for port in ports:
            if port.network_id not in Instance.network_cache:
                Instance.network_cache[port.network_id] = (
                    self._connection.network.get_network(port.network_id))
            port.network = Instance.network_cache[port.network_id]
            result.append(port)
        return result

    @property
    def node(self):
        """Underlying `Node` object."""
        return self._node

    @property
    def state(self):
        """Instance state, one of :py:class:`InstanceState`."""
        prov_state = self._node.provision_state
        if prov_state in _PROGRESS_STATES:
            return InstanceState.DEPLOYING
        # NOTE(dtantsur): include available since there is a period of time
        # between claiming the instance and starting the actual provisioning.
        elif prov_state in _RESERVED_STATES and self._node.instance_id:
            return InstanceState.DEPLOYING
        elif prov_state in _ERROR_STATES:
            return InstanceState.ERROR
        elif prov_state in _ACTIVE_STATES:
            if self._node.is_maintenance:
                return InstanceState.MAINTENANCE
            else:
                return InstanceState.ACTIVE
        else:
            return InstanceState.UNKNOWN

    def to_dict(self):
        """Convert instance to a dict."""
        return {
            'allocation': (self._allocation.to_dict()
                           if self._allocation is not None else None),
            'hostname': self.hostname,
            'ip_addresses': self.ip_addresses(),
            'node': self._node.to_dict(),
            'state': self.state.value,
            'uuid': self._uuid,
        }

    @property
    def uuid(self):
        """Instance UUID (the same as `Node` UUID for metalsmith)."""
        return self._uuid
