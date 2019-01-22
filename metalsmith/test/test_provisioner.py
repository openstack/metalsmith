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

import fixtures
import mock
from openstack import exceptions as os_exc
import requests
import testtools

from metalsmith import _config
from metalsmith import _instance
from metalsmith import _provisioner
from metalsmith import _utils
from metalsmith import exceptions
from metalsmith import sources


NODE_FIELDS = ['name', 'id', 'instance_info', 'instance_id', 'is_maintenance',
               'maintenance_reason', 'properties', 'provision_state', 'extra',
               'last_error', 'traits', 'resource_class', 'conductor_group']


class TestInit(testtools.TestCase):
    def test_missing_auth(self):
        self.assertRaisesRegex(TypeError, 'must be provided',
                               _provisioner.Provisioner)

    def test_both_provided(self):
        self.assertRaisesRegex(TypeError, 'not both', _provisioner.Provisioner,
                               session=mock.Mock(), cloud_region=mock.Mock())

    @mock.patch.object(_provisioner.connection, 'Connection', autospec=True)
    def test_session_only(self, mock_conn):
        session = mock.Mock()
        _provisioner.Provisioner(session=session)
        mock_conn.assert_called_once_with(session=session)

    @mock.patch.object(_provisioner.connection, 'Connection', autospec=True)
    def test_cloud_region_only(self, mock_conn):
        region = mock.Mock()
        mock_conn.return_value.baremetal = mock.Mock(spec=['get_endpoint'])
        mock_conn.return_value.baremetal.get_endpoint.return_value = 'http://'
        _provisioner.Provisioner(cloud_region=region)
        mock_conn.assert_called_once_with(config=region)


class Base(testtools.TestCase):

    def setUp(self):
        super(Base, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self._reset_api_mock()
        self.node = mock.Mock(spec=NODE_FIELDS + ['to_dict'],
                              id='000', instance_id=None,
                              properties={'local_gb': 100},
                              instance_info={},
                              is_maintenance=False, extra={})
        self.node.name = 'control-0'

    def _reset_api_mock(self):
        self.mock_get_node = self.useFixture(
            fixtures.MockPatchObject(_provisioner.Provisioner, '_get_node',
                                     autospec=True)).mock
        self.mock_get_node.side_effect = (
            lambda self, n, refresh=False, accept_hostname=False: n
        )
        self.useFixture(
            fixtures.MockPatchObject(_provisioner.Provisioner,
                                     '_cache_node_list_for_lookup',
                                     autospec=True))
        self.api = mock.Mock(spec=['image', 'network', 'baremetal'])
        self.api.baremetal.update_node.side_effect = lambda n, **kw: n
        self.api.network.ports.return_value = [
            mock.Mock(spec=['id'], id=i) for i in ('000', '111')
        ]
        self.api.baremetal.set_node_provision_state.side_effect = (
            lambda node, *args, **kwargs: node)
        self.api.baremetal.wait_for_nodes_provision_state.side_effect = (
            lambda nodes, *args, **kwargs: nodes)
        self.pr.connection = self.api


class TestReserveNode(Base):

    def _node(self, **kwargs):
        kwargs.setdefault('id', '000')
        kwargs.setdefault('properties', {'local_gb': 100})
        kwargs.setdefault('instance_info', {})
        kwargs.setdefault('instance_id', None)
        kwargs.setdefault('is_maintenance', False)
        return mock.Mock(spec=NODE_FIELDS, **kwargs)

    def test_no_nodes(self):
        self.api.baremetal.nodes.return_value = []

        self.assertRaises(exceptions.NodesNotFound,
                          self.pr.reserve_node, resource_class='control')
        self.assertFalse(self.api.baremetal.update_node.called)

    def test_simple_ok(self):
        nodes = [self._node(resource_class='control')]
        self.api.baremetal.nodes.return_value = nodes

        node = self.pr.reserve_node('control')

        self.assertIn(node, nodes)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id, instance_info={})

    def test_any_resource_class(self):
        nodes = [self._node()]
        self.api.baremetal.nodes.return_value = nodes

        node = self.pr.reserve_node()

        self.assertIn(node, nodes)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id, instance_info={})

    def test_with_capabilities(self):
        nodes = [
            self._node(properties={'local_gb': 100, 'capabilities': caps},
                       resource_class='control')
            for caps in ['answer:1', 'answer:42', None]
        ]
        expected = nodes[1]
        self.api.baremetal.nodes.return_value = nodes

        node = self.pr.reserve_node('control', capabilities={'answer': '42'})

        self.assertIs(node, expected)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id,
            instance_info={'capabilities': {'answer': '42'}})

    def test_with_traits(self):
        nodes = [self._node(properties={'local_gb': 100}, traits=traits)
                 for traits in [['foo', 'answer:1'], ['answer:42', 'foo'],
                                ['answer'], None]]
        expected = nodes[1]
        self.api.baremetal.nodes.return_value = nodes

        node = self.pr.reserve_node(traits=['foo', 'answer:42'])

        self.assertIs(node, expected)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id,
            instance_info={'traits': ['foo', 'answer:42']})

    def test_custom_predicate(self):
        nodes = [self._node(properties={'local_gb': i})
                 for i in (100, 150, 200)]
        self.api.baremetal.nodes.return_value = nodes[:]

        node = self.pr.reserve_node(
            predicate=lambda node: 100 < node.properties['local_gb'] < 200)

        self.assertEqual(node, nodes[1])
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id, instance_info={})

    def test_custom_predicate_false(self):
        nodes = [self._node() for _ in range(3)]
        self.api.baremetal.nodes.return_value = nodes[:]

        self.assertRaisesRegex(exceptions.CustomPredicateFailed,
                               'custom predicate',
                               self.pr.reserve_node,
                               predicate=lambda node: False)

        self.assertFalse(self.api.baremetal.update_node.called)

    def test_provided_node(self):
        nodes = [self._node()]

        node = self.pr.reserve_node(candidates=nodes)

        self.assertEqual(node, nodes[0])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id, instance_info={})

    def test_provided_nodes(self):
        nodes = [self._node(), self._node()]

        node = self.pr.reserve_node(candidates=nodes)

        self.assertEqual(node, nodes[0])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id, instance_info={})

    def test_nodes_filtered(self):
        nodes = [self._node(resource_class='banana'),
                 self._node(resource_class='compute'),
                 self._node(properties={'local_gb': 100,
                                        'capabilities': 'cat:meow'},
                            resource_class='compute')]

        node = self.pr.reserve_node('compute', candidates=nodes,
                                    capabilities={'cat': 'meow'})

        self.assertEqual(node, nodes[2])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id,
            instance_info={'capabilities': {'cat': 'meow'}})

    def test_nodes_filtered_by_conductor_group(self):
        nodes = [self._node(conductor_group='loc1'),
                 self._node(properties={'local_gb': 100,
                                        'capabilities': 'cat:meow'},
                            conductor_group=''),
                 self._node(properties={'local_gb': 100,
                                        'capabilities': 'cat:meow'},
                            conductor_group='loc1')]

        node = self.pr.reserve_node(conductor_group='loc1',
                                    candidates=nodes,
                                    capabilities={'cat': 'meow'})

        self.assertEqual(node, nodes[2])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.update_node.assert_called_once_with(
            node, instance_id=node.id,
            instance_info={'capabilities': {'cat': 'meow'}})

    def test_provided_nodes_no_match(self):
        nodes = [
            self._node(resource_class='compute', conductor_group='loc1'),
            self._node(resource_class='control', conductor_group='loc2'),
            self._node(resource_class='control', conductor_group='loc1',
                       is_maintenance=True),
            self._node(resource_class='control', conductor_group='loc1',
                       instance_id='abcd')
        ]

        self.assertRaises(exceptions.NodesNotFound,
                          self.pr.reserve_node, candidates=nodes,
                          resource_class='control', conductor_group='loc1')

        self.assertFalse(self.api.baremetal.nodes.called)
        self.assertFalse(self.api.baremetal.update_node.called)


class TestProvisionNode(Base):

    def setUp(self):
        super(TestProvisionNode, self).setUp()
        self.image = self.api.image.find_image.return_value
        self.node.instance_id = self.node.id
        self.instance_info = {
            'ramdisk': self.image.ramdisk_id,
            'kernel': self.image.kernel_id,
            'image_source': self.image.id,
            'root_gb': 99,  # 100 - 1
            'capabilities': {'boot_option': 'local'},
            _utils.GetNodeMixin.HOSTNAME_FIELD: 'control-0'
        }
        self.extra = {
            'metalsmith_created_ports': [
                self.api.network.create_port.return_value.id
            ],
            'metalsmith_attached_ports': [
                self.api.network.create_port.return_value.id
            ],
        }
        self.configdrive_mock = self.useFixture(
            fixtures.MockPatchObject(_config.InstanceConfig,
                                     'build_configdrive', autospec=True)
        ).mock

    def test_ok(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      self.node.name)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(self.api.network.delete_port.called)

    def test_ok_without_nics(self):
        self.extra['metalsmith_created_ports'] = []
        self.extra['metalsmith_attached_ports'] = []
        inst = self.pr.provision_node(self.node, 'image')

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.network.find_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_ok_with_source(self):
        inst = self.pr.provision_node(self.node, sources.GlanceImage('image'),
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_config(self):
        config = mock.Mock(spec=_config.InstanceConfig)
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      config=config)

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        config.build_configdrive.assert_called_once_with(
            self.node, self.node.name)
        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    @mock.patch.object(_provisioner.Provisioner, '_find_node_by_hostname',
                       autospec=True)
    def test_with_hostname(self, mock_find_node):
        mock_find_node.return_value = None
        hostname = 'control-0.example.com'
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      hostname=hostname)
        self.instance_info[_utils.GetNodeMixin.HOSTNAME_FIELD] = hostname

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      hostname)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_name_not_valid_hostname(self):
        self.node.name = 'node_1'
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])
        self.instance_info[_utils.GetNodeMixin.HOSTNAME_FIELD] = '000'

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_unreserved(self):
        self.node.instance_id = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_has_calls([
            mock.call(self.node, instance_id=self.node.id),
            mock.call(self.node, instance_info=self.instance_info,
                      extra=self.extra)
        ])
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_ports(self):
        port_ids = [self.api.network.find_port.return_value.id] * 2

        self.pr.provision_node(self.node, 'image',
                               [{'port': 'port1'}, {'port': 'port2'}])

        self.assertFalse(self.api.network.create_port.called)
        self.api.baremetal.attach_vif_to_node.assert_called_with(
            self.node, self.api.network.find_port.return_value.id)
        self.assertEqual(2, self.api.baremetal.attach_vif_to_node.call_count)
        self.assertEqual([mock.call('port1', ignore_missing=False),
                          mock.call('port2', ignore_missing=False)],
                         self.api.network.find_port.call_args_list)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={'metalsmith_created_ports': [],
                              'metalsmith_attached_ports': port_ids},
            instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_ip(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network',
                                        'fixed_ip': '10.0.0.2'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id,
            fixed_ips=[{'ip_address': '10.0.0.2'}])
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_subnet(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'subnet': 'subnet'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.get_network.return_value.id,
            fixed_ips=[{'subnet_id':
                        self.api.network.find_subnet.return_value.id}])
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_whole_disk(self):
        self.image.kernel_id = None
        self.image.ramdisk_id = None
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_http_and_checksum_whole_disk(self):
        self.instance_info['image_source'] = 'https://host/image'
        self.instance_info['image_checksum'] = 'abcd'
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']

        inst = self.pr.provision_node(
            self.node,
            sources.HttpWholeDiskImage('https://host/image', checksum='abcd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_with_http_and_checksum_url(self, mock_get):
        self.instance_info['image_source'] = 'https://host/image'
        self.instance_info['image_checksum'] = 'abcd'
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']
        mock_get.return_value.text = """
defg *something else
abcd  image
"""

        inst = self.pr.provision_node(
            self.node,
            sources.HttpWholeDiskImage('https://host/image',
                                       checksum_url='https://host/checksums'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_http_and_checksum_partition(self):
        self.instance_info['image_source'] = 'https://host/image'
        self.instance_info['image_checksum'] = 'abcd'
        self.instance_info['kernel'] = 'https://host/kernel'
        self.instance_info['ramdisk'] = 'https://host/ramdisk'

        inst = self.pr.provision_node(
            self.node,
            sources.HttpPartitionImage('https://host/image',
                                       checksum='abcd',
                                       kernel_url='https://host/kernel',
                                       ramdisk_url='https://host/ramdisk'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_file_whole_disk(self):
        self.instance_info['image_source'] = 'file:///foo/img'
        self.instance_info['image_checksum'] = 'abcd'
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']

        inst = self.pr.provision_node(
            self.node,
            sources.FileWholeDiskImage('file:///foo/img', checksum='abcd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_file_partition(self):
        self.instance_info['image_source'] = 'file:///foo/img'
        self.instance_info['image_checksum'] = 'abcd'
        self.instance_info['kernel'] = 'file:///foo/vmlinuz'
        self.instance_info['ramdisk'] = 'file:///foo/initrd'

        inst = self.pr.provision_node(
            self.node,
            sources.FilePartitionImage('/foo/img',
                                       '/foo/vmlinuz',
                                       '/foo/initrd',
                                       checksum='abcd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_root_size(self):
        self.instance_info['root_gb'] = 50

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               root_size_gb=50)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_swap_size(self):
        self.instance_info['swap_mb'] = 4096

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               swap_size_mb=4096)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_capabilities(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      capabilities={'answer': '42'})
        self.instance_info['capabilities'] = {'boot_option': 'local',
                                              'answer': '42'}

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_existing_capabilities(self):
        self.node.instance_info['capabilities'] = {'answer': '42'}
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])
        self.instance_info['capabilities'] = {'boot_option': 'local',
                                              'answer': '42'}

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_override_existing_capabilities(self):
        self.node.instance_info['capabilities'] = {'answer': '1',
                                                   'cat': 'meow'}
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      capabilities={'answer': '42'})
        self.instance_info['capabilities'] = {'boot_option': 'local',
                                              'answer': '42'}

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_traits(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      traits=['1', '2'])
        self.instance_info['traits'] = ['1', '2']

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_override_existing_traits(self):
        self.node.traits = ['42']
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      traits=['1', '2'])
        self.instance_info['traits'] = ['1', '2']

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_with_wait(self):
        self.api.network.find_port.return_value = mock.Mock(
            spec=['fixed_ips'],
            fixed_ips=[{'ip_address': '192.168.1.5'}, {}]
        )
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               wait=3600)

        self.api.network.create_port.assert_called_once_with(
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        wait_mock = self.api.baremetal.wait_for_nodes_provision_state
        wait_mock.assert_called_once_with([self.node], 'active',
                                          timeout=3600)
        self.assertFalse(self.api.network.delete_port.called)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_unreserve_dry_run(self):
        self.pr._dry_run = True
        self.node.instance_id = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_deploy_failure(self):
        self.api.baremetal.set_node_provision_state.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'n1'}, {'port': 'p1'}],
                               wait=3600)

        self.api.baremetal.update_node.assert_any_call(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.api.network.delete_port.assert_called_once_with(
            self.api.network.create_port.return_value.id,
            ignore_missing=False)
        calls = [
            mock.call(self.node,
                      self.api.network.create_port.return_value.id),
            mock.call(self.node, self.api.network.find_port.return_value.id)
        ]
        self.api.baremetal.detach_vif_from_node.assert_has_calls(
            calls, any_order=True)

    def test_port_creation_failure(self):
        self.api.network.create_port.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)
        self.assertFalse(self.api.baremetal.detach_vif_from_node.called)

    def test_port_attach_failure(self):
        self.api.baremetal.attach_vif_to_node.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.api.network.delete_port.assert_called_once_with(
            self.api.network.create_port.return_value.id,
            ignore_missing=False)
        self.api.baremetal.detach_vif_from_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)

    def test_failure_during_port_deletion(self):
        self.api.network.delete_port.side_effect = AssertionError()
        self.api.baremetal.set_node_provision_state.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}],
                               wait=3600)

        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.api.network.delete_port.assert_called_once_with(
            self.api.network.create_port.return_value.id,
            ignore_missing=False)
        self.api.baremetal.detach_vif_from_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)

    def _test_failure_during_deploy_failure(self):
        self.api.baremetal.set_node_provision_state.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}],
                               wait=3600)

        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.api.network.delete_port.assert_called_once_with(
            self.api.network.create_port.return_value.id,
            ignore_missing=False)
        self.api.baremetal.detach_vif_from_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)

    def test_detach_failed_after_deploy_failure(self):
        self.api.baremetal.detach_port_from_node.side_effect = AssertionError()
        self._test_failure_during_deploy_failure()

    def test_update_failed_after_deploy_failure(self):
        self.api.baremetal.update_node.side_effect = [self.node,
                                                      AssertionError()]
        self._test_failure_during_deploy_failure()

    def test_wait_failure(self):
        self.api.baremetal.wait_for_nodes_provision_state.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(self.api.network.delete_port.called)
        self.assertFalse(self.api.baremetal.detach_vif_from_node.called)

    def test_missing_image(self):
        self.api.image.find_image.side_effect = os_exc.ResourceNotFound(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidImage, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_no_checksum_with_http_image(self, mock_get):
        self.instance_info['image_source'] = 'https://host/image'
        self.instance_info['image_checksum'] = 'abcd'
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']
        mock_get.return_value.text = """
defg *something else
abcd  and-not-image-again
"""

        self.assertRaisesRegex(exceptions.InvalidImage,
                               'no image checksum',
                               self.pr.provision_node,
                               self.node,
                               sources.HttpWholeDiskImage(
                                   'https://host/image',
                                   checksum_url='https://host/checksums'),
                               [{'network': 'network'}])

        self.assertFalse(self.api.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_malformed_checksum_with_http_image(self, mock_get):
        self.instance_info['image_source'] = 'https://host/image'
        self.instance_info['image_checksum'] = 'abcd'
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']
        mock_get.return_value.text = """
<html>
    <p>I am not a checksum file!</p>
</html>"""

        self.assertRaisesRegex(exceptions.InvalidImage,
                               'Invalid checksum file',
                               self.pr.provision_node,
                               self.node,
                               sources.HttpWholeDiskImage(
                                   'https://host/image',
                                   checksum_url='https://host/checksums'),
                               [{'network': 'network'}])

        self.assertFalse(self.api.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_cannot_download_checksum_with_http_image(self, mock_get):
        self.instance_info['image_source'] = 'https://host/image'
        self.instance_info['image_checksum'] = 'abcd'
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']
        mock_get.return_value.raise_for_status.side_effect = (
            requests.RequestException("boom"))

        self.assertRaisesRegex(exceptions.InvalidImage,
                               'Cannot download checksum file',
                               self.pr.provision_node,
                               self.node,
                               sources.HttpWholeDiskImage(
                                   'https://host/image',
                                   checksum_url='https://host/checksums'),
                               [{'network': 'network'}])

        self.assertFalse(self.api.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_network(self):
        self.api.network.find_network.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_port(self):
        self.api.network.find_port.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_subnet(self):
        self.api.network.find_subnet.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'subnet': 'subnet'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_network_of_subnet(self):
        # NOTE(dtantsur): I doubt this can happen, maybe some race?
        self.api.network.get_network.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'subnet': 'subnet'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_no_local_gb(self):
        self.node.properties = {}
        self.assertRaises(exceptions.UnknownRootDiskSize,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_local_gb(self):
        for value in (None, 'meow', -42, []):
            self.node.properties = {'local_gb': value}
            self.assertRaises(exceptions.UnknownRootDiskSize,
                              self.pr.provision_node,
                              self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_root_size_gb(self):
        self.assertRaises(TypeError,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}],
                          root_size_gb={})
        self.assertRaises(ValueError,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}],
                          root_size_gb=0)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_nics(self):
        self.assertRaisesRegex(TypeError, 'must be a list',
                               self.pr.provision_node,
                               self.node, 'image', 42)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_nic(self):
        for item in ('string', ['string']):
            self.assertRaisesRegex(TypeError, 'must be a dict',
                                   self.pr.provision_node,
                                   self.node, 'image', item)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_nic_type(self):
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Unknown NIC record type',
                               self.pr.provision_node,
                               self.node, 'image', [{'foo': 'bar'}])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_nic_type_fields(self):
        for item in ({'port': '1234', 'foo': 'bar'},
                     {'port': '1234', 'network': '4321'},
                     {'network': '4321', 'foo': 'bar'},
                     {'subnet': '4321', 'foo': 'bar'}):
            self.assertRaisesRegex(exceptions.InvalidNIC,
                                   'Unexpected fields',
                                   self.pr.provision_node,
                                   self.node, 'image', [item])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.attach_vif_to_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_hostname(self):
        self.assertRaisesRegex(ValueError, 'n_1 cannot be used as a hostname',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='n_1')
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    @mock.patch.object(_provisioner.Provisioner, '_find_node_by_hostname',
                       autospec=True)
    def test_duplicate_hostname(self, mock_find_node):
        mock_find_node.return_value = mock.Mock(spec=['id', 'name'])
        self.assertRaisesRegex(ValueError, 'already uses hostname host',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='host')
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={}, instance_id=None)
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_node_not_found(self):
        self.mock_get_node.side_effect = RuntimeError('not found')
        self.assertRaisesRegex(exceptions.InvalidNode, 'not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_node_with_external_instance_id(self):
        self.node.instance_id = 'nova'
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'reserved by instance nova',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_node_in_maintenance(self):
        self.node.is_maintenance = True
        self.node.maintenance_reason = 'power failure'
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'in maintenance mode .* power failure',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_http_source(self):
        self.assertRaises(TypeError, sources.HttpWholeDiskImage,
                          'http://host/image')
        self.assertRaises(TypeError, sources.HttpWholeDiskImage,
                          'http://host/image', checksum='abcd',
                          checksum_url='http://host/checksum')
        self.assertRaises(TypeError, sources.HttpPartitionImage,
                          'http://host/image', 'http://host/kernel',
                          'http://host/ramdisk')
        self.assertRaises(TypeError, sources.HttpPartitionImage,
                          'http://host/image', 'http://host/kernel',
                          'http://host/ramdisk', checksum='abcd',
                          checksum_url='http://host/checksum')


class TestUnprovisionNode(Base):

    def test_ok(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        # Check that unrelated extra fields are not touched.
        self.node.extra['foo'] = 'bar'
        result = self.pr.unprovision_node(self.node)
        self.assertIs(result, self.node)

        self.api.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        self.api.baremetal.detach_vif_from_node.assert_called_once_with(
            self.node, 'port1')
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'deleted', wait=False)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info={}, extra={'foo': 'bar'},
            instance_id=None)

    def test_with_attached(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.node.extra['metalsmith_attached_ports'] = ['port1', 'port2']
        self.pr.unprovision_node(self.node)

        self.api.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        calls = [mock.call(self.node, 'port1'), mock.call(self.node, 'port2')]
        self.api.baremetal.detach_vif_from_node.assert_has_calls(
            calls, any_order=True)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'deleted', wait=False)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info={}, extra={}, instance_id=None)

    def test_with_wait(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        result = self.pr.unprovision_node(self.node, wait=3600)
        self.assertIs(result, self.node)

        self.api.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        self.api.baremetal.detach_vif_from_node.assert_called_once_with(
            self.node, 'port1')
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'deleted', wait=False)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info={}, extra={}, instance_id=None)
        wait_mock = self.api.baremetal.wait_for_nodes_provision_state
        wait_mock.assert_called_once_with([self.node], 'available',
                                          timeout=3600)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.pr.unprovision_node(self.node)

        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)
        self.assertFalse(self.api.baremetal.detach_vif_from_node.called)
        self.assertFalse(self.api.baremetal.update_node.called)


class TestShowInstance(Base):
    def setUp(self):
        super(TestShowInstance, self).setUp()
        self.node.provision_state = 'active'

    def test_show_instance(self):
        self.mock_get_node.side_effect = lambda n, *a, **kw: self.node
        inst = self.pr.show_instance('id1')
        self.mock_get_node.assert_called_once_with(self.pr, 'id1',
                                                   accept_hostname=True)
        self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(inst.node, self.node)
        self.assertIs(inst.uuid, self.node.id)

    def test_show_instances(self):
        self.mock_get_node.side_effect = [self.node, self.node]
        result = self.pr.show_instances(['1', '2'])
        self.mock_get_node.assert_has_calls([
            mock.call(self.pr, '1', accept_hostname=True),
            mock.call(self.pr, '2', accept_hostname=True)
        ])
        self.assertIsInstance(result, list)
        for inst in result:
            self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(result[0].node, self.node)
        self.assertIs(result[0].uuid, self.node.id)


class TestWaitForProvisioning(Base):

    def test_success(self):
        node = mock.Mock(spec=NODE_FIELDS)

        result = self.pr.wait_for_provisioning([node])
        self.assertEqual([node], [inst.node for inst in result])
        self.assertIsInstance(result[0], _instance.Instance)


class TestListInstances(Base):
    def setUp(self):
        super(TestListInstances, self).setUp()
        self.nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state,
                      instance_id='1234')
            for state in ('active', 'active', 'deploying', 'wait call-back',
                          'deploy failed', 'available', 'available', 'enroll')
        ]
        self.nodes[6].instance_id = None
        self.api.baremetal.nodes.return_value = self.nodes

    def test_list(self):
        instances = self.pr.list_instances()
        self.assertTrue(isinstance(i, _instance.Instance) for i in instances)
        self.assertEqual(self.nodes[:6], [i.node for i in instances])
        self.api.baremetal.nodes.assert_called_once_with(associated=True,
                                                         details=True)
