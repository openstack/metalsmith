# Copyright 2021 Red Hat, Inc.
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

import unittest
from unittest import mock

from openstack import exceptions as sdk_exc

from metalsmith import _nics
from metalsmith import exceptions
from metalsmith.test import test_provisioner


class TestNICs(unittest.TestCase):
    def setUp(self):
        super(TestNICs, self).setUp()
        self.connection = mock.Mock(spec=['network', 'baremetal'])
        self.node = mock.Mock(spec=test_provisioner.NODE_FIELDS + ['to_dict'],
                              id='000', instance_id=None,
                              properties={'local_gb': 100},
                              instance_info={},
                              is_maintenance=False, extra={},
                              allocation_id=None)

    def test_init(self):
        nic_info = [{'network': 'uuid',
                     'fixed_ip': '1.1.1.1'}]
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.assertEqual(nics._node, self.node)
        self.assertEqual(nics._connection, self.connection)
        self.assertEqual(nics._nics, nic_info)
        self.assertIsNone(nics._validated)
        self.assertEqual(nics._hostname, 'test-host')
        self.assertEqual(nics.created_ports, [])
        self.assertEqual(nics.attached_ports, [])

    def test_init_wrong_type(self):
        nic_info = {'wrong': 'type'}

        self.assertRaisesRegex(
            TypeError, 'NICs must be a list of dicts',
            _nics.NICs,
            self.connection, self.node, nic_info, hostname='test-host')

        nic_info = [['wrong', 'type']]
        self.assertRaisesRegex(
            TypeError, 'Each NIC must be a dict',
            _nics.NICs,
            self.connection, self.node, nic_info, hostname='test-host')

    def test_get_port(self):
        nic_info = [{'port': 'port_uuid'}]
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        fake_port = mock.Mock()
        self.connection.network.find_port.return_value = fake_port
        return_value = nics._get_port(nic_info[0])
        self.connection.network.find_port.assert_called_once_with(
            nic_info[0]['port'], ignore_missing=False)
        self.assertEqual(fake_port, return_value)

    def test_get_port_unexpected_fields(self):
        nic_info = [{'port': 'port_uuid', 'unexpected': 'field'}]
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Unexpected fields for a port: unexpected',
                               nics._get_port, nic_info[0])

    def test_get_port_resource_not_found(self):
        nic_info = [{'port': 'aaaa-bbbb-cccc'}]
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.connection.network.find_port.side_effect = (
            sdk_exc.SDKException('SDK_ERROR'))
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Cannot find port aaaa-bbbb-cccc: SDK_ERROR',
                               nics._get_port, nic_info[0])

    def test_get_network(self):
        nic_info = [{'network': 'net-name'}]
        hostname = 'test-host'
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname=hostname)
        fake_net = mock.Mock(id='fake_net_id', name='fake_net_name')
        self.connection.network.find_network.return_value = fake_net
        return_value = nics._get_network(nic_info[0])
        self.connection.network.find_network.assert_called_once_with(
            nic_info[0]['network'], ignore_missing=False)
        self.assertEqual({'network_id': fake_net.id,
                          'name': '%s-%s' % (hostname, fake_net.name)},
                         return_value)

    def test_get_network_and_subnet(self):
        nic_info = [{'network': 'net-name', 'subnet': 'subnet-name'}]
        hostname = 'test-host'
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname=hostname)
        fake_net = mock.Mock(id='fake_net_id', name='fake_net_name')
        fake_subnet = mock.Mock(id='fake_subnet_id', name='fake_subnet_name')
        self.connection.network.find_network.return_value = fake_net
        self.connection.network.find_subnet.return_value = fake_subnet
        return_value = nics._get_network(nic_info[0])
        self.connection.network.find_network.assert_called_once_with(
            nic_info[0]['network'], ignore_missing=False)
        self.connection.network.find_subnet.assert_called_once_with(
            nic_info[0]['subnet'], network_id=fake_net.id,
            ignore_missing=False)
        self.assertEqual({'network_id': fake_net.id,
                          'name': '%s-%s' % (hostname, fake_net.name),
                          'fixed_ips': [{'subnet_id': fake_subnet.id}]},
                         return_value)

    def test_get_network_and_subnet_not_found(self):
        nic_info = [{'network': 'net-name', 'subnet': 'subnet-name'}]
        hostname = 'test-host'
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname=hostname)
        fake_net = mock.Mock(id='fake_net_id', name='fake_net_name')
        self.connection.network.find_network.return_value = fake_net
        self.connection.network.find_subnet.side_effect = (
            sdk_exc.SDKException('SDK_ERROR'))
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               ('Cannot find subnet subnet-name on network '
                                'net-name: SDK_ERROR'),
                               nics._get_network, nic_info[0])
        self.connection.network.find_network.assert_called_once_with(
            nic_info[0]['network'], ignore_missing=False)
        self.connection.network.find_subnet.assert_called_once_with(
            nic_info[0]['subnet'], network_id=fake_net.id,
            ignore_missing=False)

    def test_get_network_fixed_ip(self):
        nic_info = [{'network': 'net-name', 'fixed_ip': '1.1.1.1'}]
        hostname = 'test-host'
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname=hostname)
        fake_net = mock.Mock(id='fake_net_id', name='fake_net_name')
        self.connection.network.find_network.return_value = fake_net
        return_value = nics._get_network(nic_info[0])
        self.connection.network.find_network.assert_called_once_with(
            nic_info[0]['network'], ignore_missing=False)
        self.assertEqual({'network_id': fake_net.id,
                          'name': '%s-%s' % (hostname, fake_net.name),
                          'fixed_ips': [{'ip_address': '1.1.1.1'}]},
                         return_value)

    def test_get_network_unexpected_fields(self):
        nic_info = [{'network': 'uuid',
                     'subnet': 'subnet_name',
                     'fixed_ip': '1.1.1.1',
                     'unexpected': 'field'}]
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Unexpected fields for a network: unexpected',
                               nics._get_network, nic_info[0])

    def test_get_network_resource_not_found(self):
        nic_info = [{'network': 'aaaa-bbbb-cccc', 'fixed_ip': '1.1.1.1'}]
        self.connection.network.find_network.side_effect = (
            sdk_exc.SDKException('SDK_ERROR'))
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Cannot find network aaaa-bbbb-cccc: SDK_ERROR',
                               nics._get_network, nic_info[0])

    def test_get_subnet(self):
        nic_info = [{'subnet': 'net-name'}]
        hostname = 'test-host'
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname=hostname)
        fake_net = mock.Mock(id='fake_net_id', name='fake_net_name')
        fake_subnet = mock.Mock(id='fake_subnetnet_id',
                                name='fake_subnetnet_name',
                                network_id=fake_net.id)
        self.connection.network.find_subnet.return_value = fake_subnet
        self.connection.network.get_network.return_value = fake_net
        return_value = nics._get_subnet(nic_info[0])
        self.connection.network.find_subnet.assert_called_once_with(
            nic_info[0]['subnet'], ignore_missing=False)
        self.connection.network.get_network.assert_called_once_with(
            fake_subnet.network_id)
        self.assertEqual({'network_id': fake_net.id,
                          'name': '%s-%s' % (hostname, fake_net.name),
                          'fixed_ips': [{'subnet_id': fake_subnet.id}]},
                         return_value)

    def test_get_subnet_unexpected_fields(self):
        nic_info = [{'subnet': 'uuid', 'unexpected': 'field'}]
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Unexpected fields for a subnet: unexpected',
                               nics._get_subnet, nic_info[0])

    def test_get_subnet_resource_not_found(self):
        nic_info = [{'subnet': 'uuid'}]
        self.connection.network.find_subnet.side_effect = (
            sdk_exc.SDKException('SDK_ERROR'))
        nics = _nics.NICs(self.connection, self.node, nic_info,
                          hostname='test-host')
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Cannot find subnet uuid: SDK_ERROR',
                               nics._get_subnet, nic_info[0])

    @mock.patch.object(_nics.NICs, '_get_port', autospec=True)
    @mock.patch.object(_nics.NICs, '_get_subnet', autospec=True)
    @mock.patch.object(_nics.NICs, '_get_network', autospec=True)
    def test_validate(self, mock_network, mock_subnet, mock_port):
        nic_info = [{'network': 'network'},
                    {'subnet': 'subnet'},
                    {'port': 'port'}]
        nics = _nics.NICs(self.connection, self.node, nic_info)
        mock_network.return_value = {'network_id': 'net_id'}
        mock_subnet.return_value = {'network_id': 'net_id',
                                    'fixed_ips': [
                                        {'subnet_id': 'subnet_id'}]}
        mock_port.return_value = port_mock = mock.Mock(id='port_id')
        nics.validate()
        mock_network.assert_called_once_with(nics, nic_info[0])
        mock_subnet.assert_called_once_with(nics, nic_info[1])
        mock_port.assert_called_once_with(nics, nic_info[2])
        self.assertEqual(('network', {'network_id': 'net_id'}),
                         nics._validated[0])
        self.assertEqual(('subnet', {'network_id': 'net_id',
                                     'fixed_ips': [
                                         {'subnet_id': 'subnet_id'}]}),
                         nics._validated[1])
        self.assertEqual(('port', port_mock), nics._validated[2])

    @mock.patch.object(_nics.NICs, '_get_port', autospec=True)
    @mock.patch.object(_nics.NICs, '_get_subnet', autospec=True)
    @mock.patch.object(_nics.NICs, '_get_network', autospec=True)
    def test_create_and_attach_ports(self, mock_network, mock_subnet,
                                     mock_port):
        nic_info = [{'network': 'network'},
                    {'subnet': 'subnet'},
                    {'port': 'port'}]
        nics = _nics.NICs(self.connection, self.node, nic_info)
        mock_network.return_value = {'network_id': 'net_id'}
        mock_subnet.return_value = {'network_id': 'net_id',
                                    'fixed_ips': [
                                        {'subnet_id': 'subnet_id'}]}
        port_a_mock = mock.Mock(id='port_a_id')
        port_b_mock = mock.Mock(id='port_b_id')
        port_c_mock = mock.Mock(id='port_c_id')
        self.connection.network.create_port.side_effect = [port_a_mock,
                                                           port_b_mock]
        mock_port.return_value = port_c_mock
        nics.create_and_attach_ports()
        self.connection.network.create_port.assert_has_calls(
            [mock.call(binding_host_id=nics._node.id,
                       **{'network_id': 'net_id'}),
             mock.call(binding_host_id=nics._node.id,
                       **{'network_id': 'net_id',
                          'fixed_ips': [{'subnet_id': 'subnet_id'}]})])
        self.connection.network.update_port.assert_has_calls(
            [mock.call(port_c_mock, binding_host_id=nics._node.id)])
        self.connection.baremetal.attach_vif_to_node.assert_has_calls(
            [mock.call(nics._node, port_a_mock.id),
             mock.call(nics._node, port_b_mock.id),
             mock.call(nics._node, port_c_mock.id)])
        self.assertEqual([port_a_mock.id, port_b_mock.id],
                         nics.created_ports)
        self.assertEqual([port_a_mock.id, port_b_mock.id, port_c_mock.id],
                         nics.attached_ports)

    @mock.patch.object(_nics, 'detach_and_delete_ports', autospec=True)
    def test_detach_and_delete_ports(self, mock_detach_delete):
        nics = _nics.NICs(self.connection, self.node, [])
        nics.created_ports = ['port_a_id']
        nics.attached_ports = ['port_a_id', 'port_b_id']
        nics.detach_and_delete_ports()
        mock_detach_delete.assert_called_once_with(
            self.connection, nics._node, nics.created_ports,
            nics.attached_ports)

    def test_nics_detach_and_delete_ports(self):
        created_ports = ['port_a_id']
        attached_ports = ['port_a_id', 'port_b_id']
        _nics.detach_and_delete_ports(
            self.connection, self.node, created_ports, attached_ports)
        self.connection.baremetal.detach_vif_from_node.assert_any_call(
            self.node, attached_ports[0])
        self.connection.baremetal.detach_vif_from_node.assert_any_call(
            self.node, attached_ports[1])
        self.connection.network.delete_port.assert_called_once_with(
            created_ports[0], ignore_missing=False)
