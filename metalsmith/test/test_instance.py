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

import mock

from metalsmith import _instance
from metalsmith.test import test_provisioner


class TestInstanceIPAddresses(test_provisioner.Base):
    def setUp(self):
        super(TestInstanceIPAddresses, self).setUp()
        self.instance = _instance.Instance(self.api, self.node)
        self.api.list_node_attached_ports.return_value = [
            mock.Mock(spec=['id'], id=i) for i in ('111', '222')
        ]
        self.ports = [
            mock.Mock(spec=['network_id', 'fixed_ips', 'network'],
                      network_id=n, fixed_ips=[{'ip_address': ip}])
            for n, ip in [('0', '192.168.0.1'), ('1', '10.0.0.2')]
        ]
        self.conn.network.get_port.side_effect = self.ports
        self.nets = [
            mock.Mock(spec=['id', 'name'], id=str(i)) for i in range(2)
        ]
        for n in self.nets:
            n.name = 'name-%s' % n.id
        self.conn.network.get_network.side_effect = self.nets

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


class TestInstanceStates(test_provisioner.Base):
    def setUp(self):
        super(TestInstanceStates, self).setUp()
        self.instance = _instance.Instance(self.api, self.node)

    def test_state_deploying(self):
        self.node.provision_state = 'wait call-back'
        self.assertEqual('deploying', self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertTrue(self.instance.is_healthy)

    def test_state_deploying_when_available(self):
        self.node.provision_state = 'available'
        self.assertEqual('deploying', self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertTrue(self.instance.is_healthy)

    def test_state_deploying_maintenance(self):
        self.node.maintenance = True
        self.node.provision_state = 'wait call-back'
        self.assertEqual('deploying', self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)

    def test_state_active(self):
        self.node.provision_state = 'active'
        self.assertEqual('active', self.instance.state)
        self.assertTrue(self.instance.is_deployed)
        self.assertTrue(self.instance.is_healthy)

    def test_state_maintenance(self):
        self.node.maintenance = True
        self.node.provision_state = 'active'
        self.assertEqual('maintenance', self.instance.state)
        self.assertTrue(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)

    def test_state_error(self):
        self.node.provision_state = 'deploy failed'
        self.assertEqual('error', self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)

    def test_state_unknown(self):
        self.node.provision_state = 'enroll'
        self.assertEqual('unknown', self.instance.state)
        self.assertFalse(self.instance.is_deployed)
        self.assertFalse(self.instance.is_healthy)

    @mock.patch.object(_instance.Instance, 'ip_addresses', autospec=True)
    def test_to_dict(self, mock_ips):
        self.node.provision_state = 'wait call-back'
        self.node.to_dict.return_value = {'node': 'dict'}
        self.node.instance_info = {'metalsmith_hostname': 'host'}
        mock_ips.return_value = {'private': ['1.2.3.4']}

        self.assertEqual({'hostname': 'host',
                          'ip_addresses': {'private': ['1.2.3.4']},
                          'node': {'node': 'dict'},
                          'state': 'deploying',
                          'uuid': self.node.uuid},
                         self.instance.to_dict())
