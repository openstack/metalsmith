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

from unittest import mock

from metalsmith import _instance
from metalsmith.test import test_provisioner


class TestInstanceIPAddresses(test_provisioner.Base):
    def setUp(self):
        super(TestInstanceIPAddresses, self).setUp()
        self.instance = _instance.Instance(self.api, self.node)
        self.ports = [
            mock.Mock(spec=['network_id', 'fixed_ips', 'network'],
                      network_id=n, fixed_ips=[{'ip_address': ip}])
            for n, ip in [('0', '192.168.0.1'), ('1', '10.0.0.2')]
        ]
        self.api.network.ports.return_value = self.ports
        self.nets = [
            mock.Mock(spec=['id', 'name'], id=str(i)) for i in range(2)
        ]
        for n in self.nets:
            n.name = 'name-%s' % n.id
        self.api.network.get_network.side_effect = self.nets

    def test_ip_addresses(self):
        ips = self.instance.ip_addresses()
        self.assertEqual({'name-0': ['192.168.0.1'],
                          'name-1': ['10.0.0.2']},
                         ips)

    def test_missing_ip(self):
        self.ports[0].fixed_ips = {}
        ips = self.instance.ip_addresses()
        self.assertEqual({'name-0': [],
                          'name-1': ['10.0.0.2']}, ips)

    def test_missing_port(self):
        self.ports = [
            mock.Mock(spec=['network_id', 'fixed_ips', 'network'],
                      network_id='0',
                      fixed_ips=[{'ip_address': '192.168.0.1'}]),
        ]
        self.api.network.ports.return_value = self.ports
        ips = self.instance.ip_addresses()
        self.assertEqual({'name-0': ['192.168.0.1']}, ips)


class TestInstanceStates(test_provisioner.Base):
    def setUp(self):
        super(TestInstanceStates, self).setUp()
        self.instance = _instance.Instance(self.api, self.node)

    def test_state_deploying(self):
        self.node.provision_state = 'wait call-back'
        self.assertEqual(_instance.InstanceState.DEPLOYING,
                         self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertTrue(self.instance.is_healthy)
        self.assertTrue(self.instance.state.is_healthy)
        self.assertFalse(self.instance.state.is_deployed)

    def test_state_deploying_when_available(self):
        self.node.provision_state = 'available'
        self.node.instance_id = 'abcd'
        self.assertEqual(_instance.InstanceState.DEPLOYING,
                         self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertTrue(self.instance.is_healthy)

    def test_state_unknown_when_available(self):
        self.node.provision_state = 'available'
        self.node.instance_id = None
        self.assertEqual(_instance.InstanceState.UNKNOWN, self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)
        self.assertFalse(self.instance.state.is_healthy)

    def test_state_deploying_maintenance(self):
        self.node.is_maintenance = True
        self.node.provision_state = 'wait call-back'
        self.assertEqual(_instance.InstanceState.DEPLOYING,
                         self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)
        # The state itself is considered healthy
        self.assertTrue(self.instance.state.is_healthy)

    def test_state_active(self):
        self.node.provision_state = 'active'
        self.assertEqual(_instance.InstanceState.ACTIVE, self.instance.state)
        self.assertTrue(self.instance.is_deployed)
        self.assertTrue(self.instance.is_healthy)
        self.assertTrue(self.instance.state.is_deployed)

    def test_state_maintenance(self):
        self.node.is_maintenance = True
        self.node.provision_state = 'active'
        self.assertEqual(_instance.InstanceState.MAINTENANCE,
                         self.instance.state)
        self.assertTrue(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)
        self.assertFalse(self.instance.state.is_healthy)

    def test_state_error(self):
        self.node.provision_state = 'deploy failed'
        self.assertEqual(_instance.InstanceState.ERROR, self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)
        self.assertFalse(self.instance.state.is_healthy)

    def test_state_unknown(self):
        self.node.provision_state = 'enroll'
        self.assertEqual(_instance.InstanceState.UNKNOWN, self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)
        self.assertFalse(self.instance.state.is_healthy)

    @mock.patch.object(_instance.Instance, 'ip_addresses', autospec=True)
    def test_to_dict(self, mock_ips):
        self.node.provision_state = 'wait call-back'
        self.node.to_dict.return_value = {'node': 'dict'}
        mock_ips.return_value = {'private': ['1.2.3.4']}

        to_dict = self.instance.to_dict()
        self.assertEqual({'allocation': None,
                          'hostname': self.node.name,
                          'ip_addresses': {'private': ['1.2.3.4']},
                          'node': {'node': 'dict'},
                          'state': 'deploying',
                          'uuid': self.node.id},
                         to_dict)
        # States are converted to strings
        self.assertIsInstance(to_dict['state'], str)

    @mock.patch.object(_instance.Instance, 'ip_addresses', autospec=True)
    def test_to_dict_with_allocation(self, mock_ips):
        self.node.provision_state = 'wait call-back'
        self.node.to_dict.return_value = {'node': 'dict'}
        mock_ips.return_value = {'private': ['1.2.3.4']}
        self.instance._allocation = mock.Mock()
        self.instance._allocation.name = 'host'
        self.instance._allocation.to_dict.return_value = {'alloc': 'dict'}

        to_dict = self.instance.to_dict()
        self.assertEqual({'allocation': {'alloc': 'dict'},
                          'hostname': 'host',
                          'ip_addresses': {'private': ['1.2.3.4']},
                          'node': {'node': 'dict'},
                          'state': 'deploying',
                          'uuid': self.node.id},
                         to_dict)
        # States are converted to strings
        self.assertIsInstance(to_dict['state'], str)
