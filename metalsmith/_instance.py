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

from metalsmith import _os_api


_PROGRESS_STATES = frozenset(['deploying', 'wait call-back',
                              'deploy complete'])
# NOTE(dtantsur): include available since there is a period of time between
# claiming the instance and starting the actual provisioning via ironic.
_DEPLOYING_STATES = _PROGRESS_STATES | {'available'}
_ACTIVE_STATES = frozenset(['active'])
_ERROR_STATES = frozenset(['error', 'deploy failed'])

_HEALTHY_STATES = _PROGRESS_STATES | _ACTIVE_STATES


class Instance(object):
    """Instance status in metalsmith."""

    def __init__(self, api, node):
        self._api = api
        self._uuid = node.uuid
        self._node = node

    @property
    def hostname(self):
        """Node's hostname."""
        return self._node.instance_info.get(_os_api.HOSTNAME_FIELD)

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
    def _is_deployed_by_metalsmith(self):
        return _os_api.HOSTNAME_FIELD in self._node.instance_info

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
            port = self._api.connection.network.get_port(vif.id)
            port.network = self._api.connection.network.get_network(
                port.network_id)
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
        elif prov_state in _ERROR_STATES:
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
            'hostname': self.hostname,
            'ip_addresses': self.ip_addresses(),
            'node': self._node.to_dict(),
            'state': self.state,
            'uuid': self._uuid,
        }

    @property
    def uuid(self):
        """Instance UUID (the same as `Node` UUID for metalsmith)."""
        return self._uuid
