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

import unittest
from unittest import mock

from openstack import exceptions as os_exc
import requests

from metalsmith import _instance
from metalsmith import _network_metadata
from metalsmith import _provisioner
from metalsmith import _utils
from metalsmith import exceptions
from metalsmith import instance_config
from metalsmith import sources


NODE_FIELDS = ['name', 'id', 'instance_info', 'instance_id', 'is_maintenance',
               'maintenance_reason', 'properties', 'provision_state', 'extra',
               'last_error', 'traits', 'resource_class', 'conductor_group',
               'allocation_id']
ALLOCATION_FIELDS = ['id', 'name', 'node_id']


class TestInit(unittest.TestCase):
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


class Base(unittest.TestCase):

    def setUp(self):
        super(Base, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self._reset_api_mock()
        self.node = mock.Mock(spec=NODE_FIELDS + ['to_dict'],
                              id='000', instance_id=None,
                              properties={'local_gb': 100},
                              instance_info={},
                              is_maintenance=False, extra={},
                              allocation_id=None)
        self.node.name = 'control-0'

    def _reset_api_mock(self):
        get_node_patcher = mock.patch.object(
            _provisioner.Provisioner, '_get_node', autospec=True)
        self.mock_get_node = get_node_patcher.start()
        self.addCleanup(get_node_patcher.stop)

        self.mock_get_node.side_effect = (
            lambda self, n, refresh=False: n
        )
        self.api = mock.Mock(spec=['image', 'network', 'baremetal'])
        self.api.baremetal.update_node.side_effect = lambda n, **kw: n
        self.api.baremetal.patch_node.side_effect = lambda n, _p: n
        self.api.network.ports.return_value = [
            mock.Mock(spec=['id'], id=i) for i in ('000', '111')
        ]
        self.api.baremetal.set_node_provision_state.side_effect = (
            lambda node, *args, **kwargs: node)
        self.api.baremetal.wait_for_nodes_provision_state.side_effect = (
            lambda nodes, *args, **kwargs: nodes)
        self.pr.connection = self.api


class TestGetFindNode(unittest.TestCase):

    def setUp(self):
        super(TestGetFindNode, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self.api = mock.Mock(spec=['baremetal'])
        self.pr.connection = self.api

    def test__get_node_with_node(self):
        node = mock.Mock(spec=['id', 'name'])
        result = self.pr._get_node(node)
        self.assertIs(result, node)
        self.assertFalse(self.api.baremetal.get_node.called)

    def test__get_node_with_node_refresh(self):
        node = mock.Mock(spec=['id', 'name'])
        result = self.pr._get_node(node, refresh=True)
        self.assertIs(result, self.api.baremetal.get_node.return_value)
        self.api.baremetal.get_node.assert_called_once_with(node.id)

    def test__get_node_with_instance(self):
        node = mock.Mock(spec=['uuid', 'node'])
        result = self.pr._get_node(node)
        self.assertIs(result, node.node)
        self.assertFalse(self.api.baremetal.get_node.called)

    def test__get_node_with_instance_refresh(self):
        node = mock.Mock(spec=['uuid', 'node'])
        result = self.pr._get_node(node, refresh=True)
        self.assertIs(result, self.api.baremetal.get_node.return_value)
        self.api.baremetal.get_node.assert_called_once_with(node.node.id)

    def test__get_node_with_string(self):
        result = self.pr._get_node('node')
        self.assertIs(result, self.api.baremetal.get_node.return_value)
        self.api.baremetal.get_node.assert_called_once_with('node')

    def test__find_node_and_allocation_by_node(self):
        node = mock.Mock(spec=['id', 'name'])
        result, alloc = self.pr._find_node_and_allocation(node)
        self.assertIs(result, node)
        self.assertIsNone(alloc)

    def test__find_node_and_allocation_by_node_not_found(self):
        node = mock.Mock(spec=['id', 'name'])
        self.api.baremetal.get_node.side_effect = os_exc.ResourceNotFound
        self.assertRaises(exceptions.InstanceNotFound,
                          self.pr._find_node_and_allocation, node,
                          refresh=True)

    def test__find_node_and_allocation_by_hostname(self):
        result, alloc = self.pr._find_node_and_allocation('node')
        self.assertIs(result, self.api.baremetal.get_node.return_value)
        self.assertIs(alloc, self.api.baremetal.get_allocation.return_value)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.get_allocation.return_value.node_id)

    def test__find_node_and_allocation_by_node_id(self):
        self.api.baremetal.get_allocation.side_effect = (
            os_exc.ResourceNotFound())
        result, alloc = self.pr._find_node_and_allocation('node')
        self.assertIs(result, self.api.baremetal.get_node.return_value)
        self.assertIsNone(alloc)
        self.api.baremetal.get_node.assert_called_once_with('node')

    def test__find_node_and_allocation_by_hostname_node_in_allocation(self):
        self.api.baremetal.get_node.side_effect = os_exc.ResourceNotFound
        self.assertRaises(exceptions.InstanceNotFound,
                          self.pr._find_node_and_allocation, 'node')
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.get_allocation.return_value.node_id)

    def test__find_node_and_allocation_by_hostname_bad_allocation(self):
        self.api.baremetal.get_allocation.return_value.node_id = None
        self.assertRaises(exceptions.InstanceNotFound,
                          self.pr._find_node_and_allocation, 'node')
        self.assertFalse(self.api.baremetal.get_node.called)


class TestReserveNode(Base):

    RSC = 'baremetal'

    def _node(self, **kwargs):
        kwargs.setdefault('id', '000')
        kwargs.setdefault('properties', {'local_gb': 100})
        kwargs.setdefault('instance_info', {})
        kwargs.setdefault('instance_id', None)
        kwargs.setdefault('is_maintenance', False)
        kwargs.setdefault('resource_class', self.RSC)
        result = mock.Mock(spec=NODE_FIELDS, **kwargs)
        result.name = kwargs.get('name')
        return result

    def test_no_nodes(self):
        self.api.baremetal.nodes.return_value = []

        self.assertRaises(exceptions.NodesNotFound,
                          self.pr.reserve_node, self.RSC,
                          conductor_group='foo')
        self.assertFalse(self.api.baremetal.update_node.called)

    def test_simple_ok(self):
        expected = self._node()
        self.api.baremetal.get_node.return_value = expected

        node = self.pr.reserve_node(self.RSC)

        self.assertIs(expected, node)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=None,
            resource_class=self.RSC, traits=None)
        self.api.baremetal.wait_for_allocation.assert_called_once_with(
            self.api.baremetal.create_allocation.return_value)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.assertFalse(self.api.baremetal.patch_node.called)
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_create_allocation_failed(self):
        self.api.baremetal.create_allocation.side_effect = (
            os_exc.SDKException('boom'))

        self.assertRaisesRegex(exceptions.ReservationFailed, 'boom',
                               self.pr.reserve_node, self.RSC)

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=None,
            resource_class=self.RSC, traits=None)
        self.assertFalse(self.api.baremetal.delete_allocation.called)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_allocation_failed(self):
        self.api.baremetal.wait_for_allocation.side_effect = (
            os_exc.SDKException('boom'))

        self.assertRaisesRegex(exceptions.ReservationFailed, 'boom',
                               self.pr.reserve_node, self.RSC)

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=None,
            resource_class=self.RSC, traits=None)
        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.api.baremetal.create_allocation.return_value)
        self.assertFalse(self.api.baremetal.patch_node.called)

    @mock.patch.object(_utils.LOG, 'exception', autospec=True)
    def test_allocation_failed_clean_up_failed(self, mock_log):
        self.api.baremetal.delete_allocation.side_effect = RuntimeError()
        self.api.baremetal.wait_for_allocation.side_effect = (
            os_exc.SDKException('boom'))

        self.assertRaisesRegex(exceptions.ReservationFailed, 'boom',
                               self.pr.reserve_node, self.RSC)

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=None,
            resource_class=self.RSC, traits=None)
        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.api.baremetal.create_allocation.return_value)
        self.assertFalse(self.api.baremetal.patch_node.called)
        mock_log.assert_called_once_with('Failed to delete failed allocation')

    def test_with_hostname(self):
        expected = self._node()
        self.api.baremetal.get_node.return_value = expected
        self.api.baremetal.nodes.return_value = [expected, self._node()]

        node = self.pr.reserve_node(self.RSC, hostname='example.com')

        self.assertIs(expected, node)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name='example.com', candidate_nodes=None,
            resource_class=self.RSC, traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_with_capabilities(self):
        nodes = [
            self._node(properties={'local_gb': 100, 'capabilities': caps})
            for caps in ['answer:1', 'answer:42', None]
        ]
        expected = nodes[1]
        self.api.baremetal.nodes.return_value = nodes
        self.api.baremetal.get_node.return_value = expected

        node = self.pr.reserve_node(self.RSC, capabilities={'answer': '42'})

        self.assertIs(node, expected)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[expected.id],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.api.baremetal.patch_node.assert_called_once_with(
            node, [{'path': '/instance_info/capabilities',
                    'op': 'add', 'value': {'answer': '42'}}])

    def test_node_update_failed(self):
        expected = self._node(properties={'local_gb': 100,
                                          'capabilities': {'answer': '42'}})
        self.api.baremetal.get_node.return_value = expected
        self.api.baremetal.nodes.return_value = [expected]
        self.api.baremetal.patch_node.side_effect = os_exc.SDKException('boom')

        self.assertRaisesRegex(exceptions.ReservationFailed, 'boom',
                               self.pr.reserve_node, self.RSC,
                               capabilities={'answer': '42'})

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[expected.id],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value)
        self.api.baremetal.patch_node.assert_called_once_with(
            expected, [{'path': '/instance_info/capabilities',
                        'op': 'add', 'value': {'answer': '42'}}])

    def test_node_update_unexpected_exception(self):
        expected = self._node(properties={'local_gb': 100,
                                          'capabilities': {'answer': '42'}})
        self.api.baremetal.get_node.return_value = expected
        self.api.baremetal.nodes.return_value = [expected]
        self.api.baremetal.patch_node.side_effect = RuntimeError('boom')

        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.reserve_node, self.RSC,
                               capabilities={'answer': '42'})

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[expected.id],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value)
        self.api.baremetal.patch_node.assert_called_once_with(
            expected, [{'path': '/instance_info/capabilities',
                        'op': 'add', 'value': {'answer': '42'}}])

    def test_with_traits(self):
        expected = self._node(properties={'local_gb': 100},
                              traits=['foo', 'answer:42'])
        self.api.baremetal.get_node.return_value = expected

        node = self.pr.reserve_node(self.RSC, traits=['foo', 'answer:42'])

        self.assertIs(node, expected)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_custom_predicate(self):
        nodes = [self._node(properties={'local_gb': i})
                 for i in (100, 150, 200)]
        self.api.baremetal.nodes.return_value = nodes[:]
        self.api.baremetal.get_node.return_value = nodes[1]

        node = self.pr.reserve_node(
            self.RSC,
            predicate=lambda node: 100 < node.properties['local_gb'] < 200)

        self.assertEqual(node, nodes[1])
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[nodes[1].id],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_custom_predicate_false(self):
        nodes = [self._node() for _ in range(3)]
        self.api.baremetal.nodes.return_value = nodes[:]

        self.assertRaisesRegex(exceptions.CustomPredicateFailed,
                               'custom predicate',
                               self.pr.reserve_node,
                               self.RSC,
                               predicate=lambda node: False)

        self.assertFalse(self.api.baremetal.update_node.called)

    def test_provided_node(self):
        nodes = [self._node()]
        self.api.baremetal.get_node.return_value = nodes[0]

        node = self.pr.reserve_node(self.RSC, candidates=nodes)

        self.assertEqual(node, nodes[0])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[nodes[0].id],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_provided_nodes(self):
        nodes = [self._node(id=1), self._node(id=2)]
        self.api.baremetal.get_node.return_value = nodes[0]

        node = self.pr.reserve_node(self.RSC, candidates=nodes)

        self.assertEqual(node, nodes[0])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[1, 2],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_provided_node_not_found(self):
        self.mock_get_node.side_effect = os_exc.ResourceNotFound

        self.assertRaises(exceptions.InvalidNode, self.pr.reserve_node,
                          self.RSC, candidates=['node1'])

        self.assertFalse(self.api.baremetal.nodes.called)
        self.assertFalse(self.api.baremetal.create_allocation.called)
        self.assertFalse(self.api.baremetal.patch_node.called)

    def test_nodes_filtered(self):
        nodes = [self._node(resource_class='banana'),
                 self._node(resource_class='compute'),
                 self._node(properties={'local_gb': 100,
                                        'capabilities': 'cat:meow'},
                            resource_class='compute')]
        self.api.baremetal.get_node.return_value = nodes[2]

        node = self.pr.reserve_node('compute', candidates=nodes,
                                    capabilities={'cat': 'meow'})

        self.assertEqual(node, nodes[2])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[nodes[0].id],
            resource_class='compute', traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.api.baremetal.patch_node.assert_called_once_with(
            node, [{'path': '/instance_info/capabilities',
                    'op': 'add', 'value': {'cat': 'meow'}}])

    def test_nodes_filtered_by_conductor_group(self):
        nodes = [self._node(conductor_group='loc1'),
                 self._node(properties={'local_gb': 100,
                                        'capabilities': 'cat:meow'},
                            conductor_group=''),
                 self._node(properties={'local_gb': 100,
                                        'capabilities': 'cat:meow'},
                            conductor_group='loc1')]
        self.api.baremetal.get_node.return_value = nodes[2]

        node = self.pr.reserve_node(self.RSC,
                                    conductor_group='loc1',
                                    candidates=nodes,
                                    capabilities={'cat': 'meow'})

        self.assertEqual(node, nodes[2])
        self.assertFalse(self.api.baremetal.nodes.called)
        self.api.baremetal.create_allocation.assert_called_once_with(
            name=None, candidate_nodes=[nodes[2].id],
            resource_class=self.RSC, traits=None)
        self.api.baremetal.get_node.assert_called_once_with(
            self.api.baremetal.wait_for_allocation.return_value.node_id)
        self.api.baremetal.patch_node.assert_called_once_with(
            node, [{'path': '/instance_info/capabilities',
                    'op': 'add', 'value': {'cat': 'meow'}}])

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
        self.node.instance_id = '123456'
        self.node.allocation_id = '123456'
        self.allocation = mock.Mock(spec=['id', 'node_id', 'name'],
                                    id='123456',
                                    node_id=self.node.id)
        self.allocation.name = 'example.com'
        self.instance_info = {
            'ramdisk': self.image.ramdisk_id,
            'kernel': self.image.kernel_id,
            'image_source': self.image.id,
            'root_gb': 99,  # 100 - 1
            'capabilities': {'boot_option': 'local'},
        }
        self.extra = {
            'metalsmith_created_ports': [
                self.api.network.create_port.return_value.id
            ],
            'metalsmith_attached_ports': [
                self.api.network.create_port.return_value.id
            ],
        }

        configdrive_patcher = mock.patch.object(
            instance_config.GenericConfig, 'generate', autospec=True)
        self.configdrive_mock = configdrive_patcher.start()
        self.addCleanup(configdrive_patcher.stop)

        create_network_metadata_patches = mock.patch.object(
            _network_metadata, 'create_network_metadata', autospec=True
        )
        self.network_metadata_mock = create_network_metadata_patches.start()
        self.addCleanup(create_network_metadata_patches.stop)

        self.api.baremetal.get_node.side_effect = lambda _n: self.node
        self.api.baremetal.get_allocation.side_effect = (
            lambda _a: self.allocation)

    def test_ok(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      self.allocation.name,
                                                      mock.ANY)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(self.api.network.delete_port.called)

    def test_old_style_reservation(self):
        self.node.allocation_id = None
        self.node.instance_id = self.node.id
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      self.node.name, mock.ANY)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
        config = mock.Mock(spec=instance_config.GenericConfig)
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      config=config)

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        config.generate.assert_called_once_with(self.node,
                                                self.allocation.name, mock.ANY)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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

    def test_with_hostname_override(self):
        self.allocation.name = None
        self.api.baremetal.get_allocation.side_effect = [
            os_exc.ResourceNotFound(),
            self.allocation
        ]

        def _update(allocation, name):
            allocation.name = name
            return allocation

        self.api.baremetal.update_allocation.side_effect = _update
        hostname = 'control-0.example.com'
        self.instance_info['display_name'] = hostname
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      hostname=hostname)

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)
        self.assertIs(inst.allocation, self.allocation)

        self.api.baremetal.update_allocation.assert_called_once_with(
            self.allocation, name=hostname)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='control-0.example.com-%s' %
            self.api.network.find_network.return_value.name)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      hostname, mock.ANY)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_existing_hostname(self):
        hostname = 'control-0.example.com'
        self.allocation.name = hostname
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)
        self.assertIs(inst.allocation, self.allocation)

        self.assertFalse(self.api.baremetal.update_allocation.called)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='control-0.example.com-%s' %
            self.api.network.find_network.return_value.name)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      hostname, mock.ANY)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_existing_hostname_match(self):
        hostname = 'control-0.example.com'
        self.instance_info['display_name'] = hostname
        self.allocation.name = hostname
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      hostname=hostname)

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)
        self.assertIs(inst.allocation, self.allocation)

        self.assertFalse(self.api.baremetal.update_allocation.called)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='control-0.example.com-%s' %
            self.api.network.find_network.return_value.name)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, instance_info=self.instance_info, extra=self.extra)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      hostname, mock.ANY)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_existing_hostname_mismatch(self):
        self.api.baremetal.get_allocation.side_effect = [
            # No allocation with requested hostname
            os_exc.ResourceNotFound(),
            # Allocation associated with the node
            self.allocation
        ]
        self.allocation.name = 'control-0.example.com'
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'does not match the expected hostname',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}],
                               hostname='control-1.example.com')

        self.api.baremetal.get_allocation.assert_has_calls([
            mock.call('control-1.example.com'),
            mock.call(self.node.allocation_id),
        ])
        self.assertFalse(self.api.baremetal.create_allocation.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_node_name_as_hostname(self):
        self.allocation.name = None

        def _update(allocation, name):
            allocation.name = name
            return allocation

        self.api.baremetal.update_allocation.side_effect = _update
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)
        self.assertIs(inst.allocation, self.allocation)

        self.api.baremetal.update_allocation.assert_called_once_with(
            self.allocation, name=self.node.name)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='control-0-%s' %
            self.api.network.find_network.return_value.name)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      self.node.name, mock.ANY)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_name_not_valid_hostname(self):
        self.node.name = 'node_1'
        self.allocation.name = None

        def _update(allocation, name):
            allocation.name = name
            return allocation

        self.api.baremetal.update_allocation.side_effect = _update
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)
        self.assertIs(inst.allocation, self.allocation)

        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='000-%s' %
            self.api.network.find_network.return_value.name)
        self.api.baremetal.attach_vif_to_node.assert_called_once_with(
            self.node, self.api.network.create_port.return_value.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra=self.extra, instance_info=self.instance_info)
        self.api.baremetal.validate_node.assert_called_once_with(self.node)
        self.configdrive_mock.assert_called_once_with(mock.ANY, self.node,
                                                      self.node.id, mock.ANY)
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'active', config_drive=mock.ANY)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)

    def test_unreserved(self):
        self.node.instance_id = None
        self.node.allocation_id = None
        self.api.baremetal.get_node.return_value = self.node

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=self.node.name, candidate_nodes=[self.node.id],
            resource_class=self.node.resource_class, traits=None)
        self.api.baremetal.get_node.assert_has_calls([
            # After allocation
            mock.call(
                self.api.baremetal.wait_for_allocation.return_value.node_id),
            # After deployment
            mock.call(self.node.id)
        ])
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='%s-%s' % (
                self.api.baremetal.wait_for_allocation.return_value.name,
                self.api.network.find_network.return_value.name
            ))
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

    def test_unreserved_with_hostname(self):
        self.node.instance_id = None
        self.node.allocation_id = None
        self.api.baremetal.get_node.return_value = self.node
        hostname = 'control-2.example.com'
        self.instance_info['display_name'] = hostname

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               hostname=hostname)

        self.api.baremetal.create_allocation.assert_called_once_with(
            name=hostname, candidate_nodes=[self.node.id],
            resource_class=self.node.resource_class, traits=None)
        self.api.baremetal.get_node.assert_has_calls([
            # After allocation
            mock.call(
                self.api.baremetal.wait_for_allocation.return_value.node_id),
            # After deployment
            mock.call(self.node.id)
        ])
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='%s-%s' % (
                self.api.baremetal.wait_for_allocation.return_value.name,
                self.api.network.find_network.return_value.name
            ))
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

    def test_unreserved_without_resource_class(self):
        self.node.instance_id = None
        self.node.allocation_id = None
        self.node.resource_class = None
        self.api.baremetal.get_node.return_value = self.node

        self.assertRaisesRegex(exceptions.InvalidNode,
                               'does not have a resource class',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.baremetal.create_allocation.called)
        self.assertFalse(self.api.baremetal.update_node.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_with_ports(self):
        port_ids = [self.api.network.find_port.return_value.id] * 2

        self.pr.provision_node(self.node, 'image',
                               [{'port': 'port1'}, {'port': 'port2'}])

        self.assertFalse(self.api.network.create_port.called)
        self.api.network.update_port.assert_has_calls([
            mock.call(self.api.network.find_port.return_value,
                      binding_host_id=self.node.id),
            mock.call(self.api.network.find_port.return_value,
                      binding_host_id=self.node.id)])
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name,
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
            binding_host_id=self.node.id,
            network_id=self.api.network.get_network.return_value.id,
            name='example.com-%s' %
            self.api.network.get_network.return_value.name,
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
        # Ensure stale values clean up
        self.node.instance_info['kernel'] = 'bad value'
        self.node.instance_info['ramdisk'] = 'bad value'

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
        del self.instance_info['kernel']
        del self.instance_info['ramdisk']

        inst = self.pr.provision_node(
            self.node,
            sources.FileWholeDiskImage('file:///foo/img'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
        self.instance_info['kernel'] = 'file:///foo/vmlinuz'
        self.instance_info['ramdisk'] = 'file:///foo/initrd'

        inst = self.pr.provision_node(
            self.node,
            sources.FilePartitionImage('/foo/img',
                                       '/foo/vmlinuz',
                                       '/foo/initrd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.id)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.api.image.find_image.called)
        self.api.network.create_port.assert_called_once_with(
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
            binding_host_id=self.node.id,
            network_id=self.api.network.find_network.return_value.id,
            name='example.com-%s' %
            self.api.network.find_network.return_value.name)
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
        self.node.allocation_id = None
        self.node.instance_id = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.create_allocation.called)
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
            self.node, extra={}, instance_info={})
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
        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.allocation.id)

    def test_deploy_failure_without_allocation(self):
        self.node.instance_id = None
        self.node.allocation_id = None
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
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_deploy_failure_no_cleanup(self):
        self.node.allocation_id = 'id2'
        self.api.baremetal.set_node_provision_state.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'n1'}, {'port': 'p1'}],
                               wait=3600, clean_up_on_failure=False)

        self.assertEqual(1, self.api.baremetal.update_node.call_count)
        self.assertFalse(
            self.api.baremetal.wait_for_nodes_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)
        self.assertFalse(self.api.baremetal.detach_vif_from_node.called)
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_port_creation_failure(self):
        self.api.network.create_port.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.allocation.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={})
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)
        self.assertFalse(self.api.baremetal.detach_vif_from_node.called)

    def test_port_attach_failure(self):
        self.api.baremetal.attach_vif_to_node.side_effect = (
            RuntimeError('boom'))
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.baremetal.delete_allocation.assert_called_once_with(
            self.allocation.id)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={})
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
        self.api.baremetal.detach_vif_from_node.side_effect = AssertionError()
        self._test_failure_during_deploy_failure()

    def test_update_failed_after_deploy_failure(self):
        self.api.baremetal.update_node.side_effect = [self.node,
                                                      AssertionError()]
        self._test_failure_during_deploy_failure()

    def test_deallocation_failed_after_deploy_failure(self):
        self.api.baremetal.delete_allocation.side_effect = AssertionError()
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
            self.node, extra={}, instance_info={})
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
            self.node, extra={}, instance_info={})
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
            self.node, extra={}, instance_info={})
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
            self.node, extra={}, instance_info={})
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_network(self):
        self.api.network.find_network.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={})
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_port(self):
        self.api.network.find_port.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={})
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_invalid_subnet(self):
        self.api.network.find_subnet.side_effect = os_exc.SDKException(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'subnet': 'subnet'}])
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={}, instance_info={})
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
            self.node, extra={}, instance_info={})
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
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_duplicate_hostname(self):
        allocation = mock.Mock(spec=['id', 'name', 'node_id'],
                               node_id='another node')
        self.api.baremetal.get_allocation.side_effect = [allocation]
        self.assertRaisesRegex(ValueError, 'already uses hostname host',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='host')
        self.assertFalse(self.api.network.create_port.called)
        self.assertFalse(self.api.baremetal.set_node_provision_state.called)

    def test_old_style_reservation_with_override(self):
        self.node.allocation_id = None
        self.node.instance_id = self.node.id
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'does not use allocations',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='host')
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
        self.node.allocation_id = None
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

    def setUp(self):
        super(TestUnprovisionNode, self).setUp()
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.node.allocation_id = '123'
        self.node.provision_state = 'active'

    def test_ok(self):
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
            self.node, extra={'foo': 'bar'})
        self.assertFalse(self.api.baremetal.delete_allocation.called)
        # We cannot delete an allocation for an active node, it will be deleted
        # automatically.
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_delete_allocation(self):
        self.node.provision_state = 'deploy failed'
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
            self.node, extra={'foo': 'bar'})
        self.api.baremetal.delete_allocation.assert_called_once_with('123')

    def test_with_attached(self):
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
            self.node, extra={})

    def test_with_wait(self):
        result = self.pr.unprovision_node(self.node, wait=3600)
        self.assertIs(result, self.node)

        self.api.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        self.api.baremetal.detach_vif_from_node.assert_called_once_with(
            self.node, 'port1')
        self.api.baremetal.set_node_provision_state.assert_called_once_with(
            self.node, 'deleted', wait=False)
        self.api.baremetal.update_node.assert_called_once_with(
            self.node, extra={})
        wait_mock = self.api.baremetal.wait_for_nodes_provision_state
        wait_mock.assert_called_once_with([self.node], 'available',
                                          timeout=3600)

    def test_with_wait_failed(self):
        for caught, expected in [(os_exc.ResourceTimeout,
                                  exceptions.DeploymentTimeout),
                                 (os_exc.SDKException,
                                  exceptions.DeploymentFailed)]:
            self.api.baremetal.wait_for_nodes_provision_state.side_effect = (
                caught)
            self.assertRaises(expected, self.pr.unprovision_node,
                              self.node, wait=3600)

    def test_without_allocation(self):
        self.node.allocation_id = None
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
            self.node, extra={'foo': 'bar'},
            instance_id=None)
        self.assertFalse(self.api.baremetal.delete_allocation.called)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.pr.unprovision_node(self.node)

        self.assertFalse(self.api.baremetal.set_node_provision_state.called)
        self.assertFalse(self.api.network.delete_port.called)
        self.assertFalse(self.api.baremetal.detach_vif_from_node.called)
        self.assertFalse(self.api.baremetal.update_node.called)


class TestShowInstance(unittest.TestCase):
    def setUp(self):
        super(TestShowInstance, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self.api = mock.Mock(spec=['baremetal'])
        self.pr.connection = self.api

        self.node = mock.Mock(spec=NODE_FIELDS + ['to_dict'],
                              id='000', instance_id=None,
                              properties={'local_gb': 100},
                              instance_info={},
                              is_maintenance=False, extra={},
                              provision_state='active',
                              allocation_id=None)
        self.node.name = 'control-0'
        self.api.baremetal.get_node.return_value = self.node

    def test_show_instance(self):
        self.api.baremetal.get_allocation.side_effect = (
            os_exc.ResourceNotFound())
        inst = self.pr.show_instance('id1')
        self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(inst.node, self.node)
        self.assertIs(inst.uuid, self.node.id)
        self.api.baremetal.get_node.assert_called_once_with('id1')

    def test_show_instance_with_allocation(self):
        self.api.baremetal.get_allocation.return_value.node_id = '1234'
        inst = self.pr.show_instance('id1')
        self.api.baremetal.get_allocation.assert_called_once_with('id1')
        self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(inst.allocation,
                      self.api.baremetal.get_allocation.return_value)
        self.assertIs(inst.node, self.node)
        self.assertIs(inst.uuid, self.node.id)
        self.api.baremetal.get_node.assert_called_once_with('1234')

    def test_show_instances(self):
        self.api.baremetal.get_allocation.side_effect = [
            os_exc.ResourceNotFound(),
            mock.Mock(node_id='4321'),
        ]
        result = self.pr.show_instances(['inst-1', 'inst-2'])
        self.api.baremetal.get_node.assert_has_calls([
            mock.call('inst-1'),
            mock.call('4321'),
        ])
        self.api.baremetal.get_allocation.assert_has_calls([
            mock.call('inst-1'),
            mock.call('inst-2'),
        ])
        self.assertIsInstance(result, list)
        for inst in result:
            self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(result[0].node, self.node)
        self.assertIs(result[0].uuid, self.node.id)

    def test_show_instance_invalid_state(self):
        self.node.provision_state = 'manageable'
        self.api.baremetal.get_allocation.side_effect = (
            os_exc.ResourceNotFound())
        self.assertRaises(exceptions.InstanceNotFound,
                          self.pr.show_instance, 'id1')
        self.api.baremetal.get_node.assert_called_once_with('id1')


class TestWaitForProvisioning(Base):

    def test_success(self):
        node = mock.Mock(spec=NODE_FIELDS)

        result = self.pr.wait_for_provisioning([node])
        self.assertEqual([node], [inst.node for inst in result])
        self.assertIsInstance(result[0], _instance.Instance)

    def test_exceptions(self):
        node = mock.Mock(spec=NODE_FIELDS)

        for caught, expected in [(os_exc.ResourceTimeout,
                                  exceptions.DeploymentTimeout),
                                 (os_exc.SDKException,
                                  exceptions.DeploymentFailed)]:
            self.api.baremetal.wait_for_nodes_provision_state.side_effect = (
                caught)
            self.assertRaises(expected, self.pr.wait_for_provisioning, [node])


class TestListInstances(Base):
    def setUp(self):
        super(TestListInstances, self).setUp()
        self.nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state,
                      instance_id='1234', allocation_id=None)
            for state in ('active', 'active', 'deploying', 'wait call-back',
                          'deploy failed', 'available', 'available', 'enroll')
        ]
        self.nodes[0].allocation_id = 'id2'
        self.nodes[6].instance_id = None
        self.api.baremetal.nodes.return_value = self.nodes
        self.allocations = [mock.Mock(id='id2')]
        self.api.baremetal.allocations.return_value = self.allocations

    def test_list(self):
        instances = self.pr.list_instances()
        self.assertTrue(all(isinstance(i, _instance.Instance)
                            for i in instances))
        self.assertEqual(self.nodes[:6], [i.node for i in instances])
        self.assertEqual([self.api.baremetal.get_allocation.return_value] * 6,
                         [i.allocation for i in instances])
        self.api.baremetal.nodes.assert_called_once_with(associated=True,
                                                         details=True)
        self.api.baremetal.allocations.assert_called_once()
