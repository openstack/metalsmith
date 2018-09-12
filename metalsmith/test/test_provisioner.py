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
from metalsmith import _os_api
from metalsmith import _provisioner
from metalsmith import exceptions
from metalsmith import sources


NODE_FIELDS = ['name', 'uuid', 'instance_info', 'instance_uuid', 'maintenance',
               'maintenance_reason', 'properties', 'provision_state', 'extra',
               'last_error', 'traits']


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
        _provisioner.Provisioner(cloud_region=region)
        mock_conn.assert_called_once_with(config=region)


class Base(testtools.TestCase):

    def setUp(self):
        super(Base, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self._reset_api_mock()
        self.node = mock.Mock(spec=NODE_FIELDS + ['to_dict'],
                              uuid='000', instance_uuid=None,
                              properties={'local_gb': 100},
                              instance_info={},
                              maintenance=False, extra={})
        self.node.name = 'control-0'

    def _reset_api_mock(self):
        self.api = mock.Mock(spec=_os_api.API)
        self.api.get_node.side_effect = (
            lambda n, refresh=False, accept_hostname=False: n
        )
        self.api.update_node.side_effect = lambda n, _u: n
        self.api.list_node_ports.return_value = [
            mock.Mock(spec=['uuid', 'pxe_enabled'],
                      uuid=uuid, pxe_enabled=pxe)
            for (uuid, pxe) in [('000', True), ('111', False)]
        ]
        self.api.find_node_by_hostname.return_value = None
        self.api.cache_node_list_for_lookup = mock.MagicMock()
        self.pr._api = self.api

        self.conn = mock.Mock(spec=['image', 'network', 'baremetal'])
        self.pr.connection = self.conn
        self.api.connection = self.conn


class TestReserveNode(Base):

    def test_no_nodes(self):
        self.api.list_nodes.return_value = []

        self.assertRaises(exceptions.NodesNotFound,
                          self.pr.reserve_node, resource_class='control')
        self.assertFalse(self.api.reserve_node.called)

    def test_simple_ok(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100})
        ]
        self.api.list_nodes.return_value = nodes
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node('control')

        self.assertIn(node, nodes)
        self.assertFalse(self.api.update_node.called)

    def test_any_resource_class(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100})
        ]
        self.api.list_nodes.return_value = nodes
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node()

        self.assertIn(node, nodes)
        self.assertFalse(self.api.update_node.called)

    def test_with_capabilities(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100, 'capabilities': caps})
            for caps in ['answer:1', 'answer:42', None]
        ]
        expected = nodes[1]
        self.api.list_nodes.return_value = nodes
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node('control', capabilities={'answer': '42'})

        self.assertIs(node, expected)
        self.api.update_node.assert_called_once_with(
            node, {'/instance_info/capabilities': {'answer': '42'}})

    def test_with_traits(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100}, traits=traits)
            for traits in [['foo', 'answer:1'], ['answer:42', 'foo'],
                           ['answer'], None]
        ]
        expected = nodes[1]
        self.api.list_nodes.return_value = nodes
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node(traits=['foo', 'answer:42'])

        self.assertIs(node, expected)
        self.api.update_node.assert_called_once_with(
            node, {'/instance_info/traits': ['foo', 'answer:42']})

    def test_custom_predicate(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100}),
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 150}),
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 200}),
        ]
        self.api.list_nodes.return_value = nodes[:]
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node(
            predicate=lambda node: 100 < node.properties['local_gb'] < 200)

        self.assertEqual(node, nodes[1])
        self.assertFalse(self.api.update_node.called)

    def test_custom_predicate_false(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100}),
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 150}),
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 200}),
        ]
        self.api.list_nodes.return_value = nodes[:]

        self.assertRaisesRegex(exceptions.CustomPredicateFailed,
                               'custom predicate',
                               self.pr.reserve_node,
                               predicate=lambda node: False)

        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.reserve_node.called)

    def test_provided_node(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100})
        ]
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node(candidates=nodes)

        self.assertEqual(node, nodes[0])
        self.assertFalse(self.api.list_nodes.called)
        self.assertFalse(self.api.update_node.called)

    def test_provided_nodes(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100}),
            mock.Mock(spec=['uuid', 'name', 'properties'],
                      properties={'local_gb': 100})
        ]
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node(candidates=nodes)

        self.assertEqual(node, nodes[0])
        self.assertFalse(self.api.list_nodes.called)
        self.assertFalse(self.api.update_node.called)

    def test_nodes_filtered(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties', 'resource_class'],
                      properties={'local_gb': 100}, resource_class='banana'),
            mock.Mock(spec=['uuid', 'name', 'properties', 'resource_class'],
                      properties={'local_gb': 100}, resource_class='compute'),
            mock.Mock(spec=['uuid', 'name', 'properties', 'resource_class'],
                      properties={'local_gb': 100, 'capabilities': 'cat:meow'},
                      resource_class='compute'),
        ]
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node('compute', candidates=nodes,
                                    capabilities={'cat': 'meow'})

        self.assertEqual(node, nodes[2])
        self.assertFalse(self.api.list_nodes.called)
        self.api.update_node.assert_called_once_with(
            node, {'/instance_info/capabilities': {'cat': 'meow'}})

    def test_nodes_filtered_by_conductor_group(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties', 'conductor_group'],
                      properties={'local_gb': 100}, conductor_group='loc1'),
            mock.Mock(spec=['uuid', 'name', 'properties', 'conductor_group'],
                      properties={'local_gb': 100, 'capabilities': 'cat:meow'},
                      conductor_group=''),
            mock.Mock(spec=['uuid', 'name', 'properties', 'conductor_group'],
                      properties={'local_gb': 100, 'capabilities': 'cat:meow'},
                      conductor_group='loc1'),
        ]
        self.api.reserve_node.side_effect = lambda n, instance_uuid: n

        node = self.pr.reserve_node(conductor_group='loc1',
                                    candidates=nodes,
                                    capabilities={'cat': 'meow'})

        self.assertEqual(node, nodes[2])
        self.assertFalse(self.api.list_nodes.called)
        self.api.update_node.assert_called_once_with(
            node, {'/instance_info/capabilities': {'cat': 'meow'}})

    def test_provided_nodes_no_match(self):
        nodes = [
            mock.Mock(spec=['uuid', 'name', 'properties', 'resource_class',
                            'conductor_group'],
                      properties={'local_gb': 100}, resource_class='compute',
                      conductor_group='loc1'),
            mock.Mock(spec=['uuid', 'name', 'properties', 'resource_class',
                            'conductor_group'],
                      properties={'local_gb': 100}, resource_class='control',
                      conductor_group='loc2'),
        ]

        self.assertRaises(exceptions.NodesNotFound,
                          self.pr.reserve_node, candidates=nodes,
                          resource_class='control', conductor_group='loc1')

        self.assertFalse(self.api.list_nodes.called)
        self.assertFalse(self.api.reserve_node.called)
        self.assertFalse(self.api.update_node.called)


CLEAN_UP = {
    '/extra/metalsmith_created_ports': _os_api.REMOVE,
    '/extra/metalsmith_attached_ports': _os_api.REMOVE,
    '/instance_info/%s' % _os_api.HOSTNAME_FIELD: _os_api.REMOVE
}


class TestProvisionNode(Base):

    def setUp(self):
        super(TestProvisionNode, self).setUp()
        self.image = self.conn.image.find_image.return_value
        self.node.instance_uuid = self.node.uuid
        self.updates = {
            '/instance_info/ramdisk': self.image.ramdisk_id,
            '/instance_info/kernel': self.image.kernel_id,
            '/instance_info/image_source': self.image.id,
            '/instance_info/root_gb': 99,  # 100 - 1
            '/instance_info/capabilities': {'boot_option': 'local'},
            '/extra/metalsmith_created_ports': [
                self.conn.network.create_port.return_value.id
            ],
            '/extra/metalsmith_attached_ports': [
                self.conn.network.create_port.return_value.id
            ],
            '/instance_info/%s' % _os_api.HOSTNAME_FIELD: 'control-0'
        }
        self.wait_fixture = self.useFixture(
            fixtures.MockPatchObject(_provisioner.Provisioner,
                                     '_wait_for_state', autospec=True))
        self.wait_mock = self.wait_fixture.mock
        self.wait_mock.side_effect = lambda self, nodes, *a, **kw: nodes

    def test_ok(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_ok_without_nics(self):
        self.updates['/extra/metalsmith_created_ports'] = []
        self.updates['/extra/metalsmith_attached_ports'] = []
        inst = self.pr.provision_node(self.node, 'image')

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.conn.network.find_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_ok_with_source(self):
        inst = self.pr.provision_node(self.node, sources.GlanceImage('image'),
                                      [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_config(self):
        config = mock.MagicMock(spec=_config.InstanceConfig)
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      config=config)

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        config.build_configdrive_directory.assert_called_once_with(
            self.node, self.node.name)
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_hostname(self):
        hostname = 'control-0.example.com'
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      hostname=hostname)
        self.updates['/instance_info/%s' % _os_api.HOSTNAME_FIELD] = hostname

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_name_not_valid_hostname(self):
        self.node.name = 'node_1'
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])
        self.updates['/instance_info/%s' % _os_api.HOSTNAME_FIELD] = '000'

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_unreserved(self):
        self.node.instance_uuid = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_ports(self):
        self.updates['/extra/metalsmith_created_ports'] = []
        self.updates['/extra/metalsmith_attached_ports'] = [
            self.conn.network.find_port.return_value.id
        ] * 2

        self.pr.provision_node(self.node, 'image',
                               [{'port': 'port1'}, {'port': 'port2'}])

        self.assertFalse(self.conn.network.create_port.called)
        self.api.attach_port_to_node.assert_called_with(
            self.node.uuid, self.conn.network.find_port.return_value.id)
        self.assertEqual(2, self.api.attach_port_to_node.call_count)
        self.assertEqual([mock.call('port1', ignore_missing=False),
                          mock.call('port2', ignore_missing=False)],
                         self.conn.network.find_port.call_args_list)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_ip(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network',
                                        'fixed_ip': '10.0.0.2'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id,
            fixed_ips=[{'ip_address': '10.0.0.2'}])
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_whole_disk(self):
        self.image.kernel_id = None
        self.image.ramdisk_id = None
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_http_and_checksum_whole_disk(self):
        self.updates['/instance_info/image_source'] = 'https://host/image'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']

        inst = self.pr.provision_node(
            self.node,
            sources.HttpWholeDiskImage('https://host/image', checksum='abcd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.conn.image.find_image.called)
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_with_http_and_checksum_url(self, mock_get):
        self.updates['/instance_info/image_source'] = 'https://host/image'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']
        mock_get.return_value.text = """
defg *something else
abcd  image
"""

        inst = self.pr.provision_node(
            self.node,
            sources.HttpWholeDiskImage('https://host/image',
                                       checksum_url='https://host/checksums'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.conn.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_http_and_checksum_partition(self):
        self.updates['/instance_info/image_source'] = 'https://host/image'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        self.updates['/instance_info/kernel'] = 'https://host/kernel'
        self.updates['/instance_info/ramdisk'] = 'https://host/ramdisk'

        inst = self.pr.provision_node(
            self.node,
            sources.HttpPartitionImage('https://host/image',
                                       checksum='abcd',
                                       kernel_url='https://host/kernel',
                                       ramdisk_url='https://host/ramdisk'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.conn.image.find_image.called)
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_file_whole_disk(self):
        self.updates['/instance_info/image_source'] = 'file:///foo/img'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']

        inst = self.pr.provision_node(
            self.node,
            sources.FileWholeDiskImage('file:///foo/img', checksum='abcd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.conn.image.find_image.called)
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_file_partition(self):
        self.updates['/instance_info/image_source'] = 'file:///foo/img'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        self.updates['/instance_info/kernel'] = 'file:///foo/vmlinuz'
        self.updates['/instance_info/ramdisk'] = 'file:///foo/initrd'

        inst = self.pr.provision_node(
            self.node,
            sources.FilePartitionImage('/foo/img',
                                       '/foo/vmlinuz',
                                       '/foo/initrd',
                                       checksum='abcd'),
            [{'network': 'network'}])

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.assertFalse(self.conn.image.find_image.called)
        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_root_size(self):
        self.updates['/instance_info/root_gb'] = 50

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               root_size_gb=50)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_deprecated_root_size(self):
        self.updates['/instance_info/root_gb'] = 50

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               root_disk_size=50)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_swap_size(self):
        self.updates['/instance_info/swap_mb'] = 4096

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               swap_size_mb=4096)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_capabilities(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      capabilities={'answer': '42'})
        self.updates['/instance_info/capabilities'] = {'boot_option': 'local',
                                                       'answer': '42'}

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_existing_capabilities(self):
        self.node.instance_info['capabilities'] = {'answer': '42'}
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])
        self.updates['/instance_info/capabilities'] = {'boot_option': 'local',
                                                       'answer': '42'}

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_override_existing_capabilities(self):
        self.node.instance_info['capabilities'] = {'answer': '1',
                                                   'cat': 'meow'}
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      capabilities={'answer': '42'})
        self.updates['/instance_info/capabilities'] = {'boot_option': 'local',
                                                       'answer': '42'}

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_traits(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      traits=['1', '2'])
        self.updates['/instance_info/traits'] = ['1', '2']

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_override_existing_traits(self):
        self.node.traits = ['42']
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      traits=['1', '2'])
        self.updates['/instance_info/traits'] = ['1', '2']

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_with_wait(self):
        self.conn.network.find_port.return_value = mock.Mock(
            spec=['fixed_ips'],
            fixed_ips=[{'ip_address': '192.168.1.5'}, {}]
        )
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               wait=3600)

        self.conn.network.create_port.assert_called_once_with(
            network_id=self.conn.network.find_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.conn.network.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.wait_mock.assert_called_once_with(self.pr,
                                               [self.node],
                                               'active',
                                               delay=15,
                                               timeout=3600)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_unreserve_dry_run(self):
        self.pr._dry_run = True
        self.node.instance_uuid = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.reserve_node.called)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)

    def test_deploy_failure(self):
        self.api.node_action.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'n1'}, {'port': 'p1'}],
                               wait=3600)

        self.api.update_node.assert_any_call(self.node, CLEAN_UP)
        self.assertFalse(self.wait_mock.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.conn.network.delete_port.assert_called_once_with(
            self.conn.network.create_port.return_value.id,
            ignore_missing=False)
        calls = [
            mock.call(self.node,
                      self.conn.network.create_port.return_value.id),
            mock.call(self.node, self.conn.network.find_port.return_value.id)
        ]
        self.api.detach_port_from_node.assert_has_calls(calls, any_order=True)

    def test_port_creation_failure(self):
        self.conn.network.create_port.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.assertFalse(self.conn.network.delete_port.called)
        self.assertFalse(self.api.detach_port_from_node.called)

    def test_port_attach_failure(self):
        self.api.attach_port_to_node.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.conn.network.delete_port.assert_called_once_with(
            self.conn.network.create_port.return_value.id,
            ignore_missing=False)
        self.api.detach_port_from_node.assert_called_once_with(
            self.node, self.conn.network.create_port.return_value.id)

    def test_failure_during_port_deletion(self):
        self.conn.network.delete_port.side_effect = AssertionError()
        self.api.node_action.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}],
                               wait=3600)

        self.assertFalse(self.wait_mock.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.conn.network.delete_port.assert_called_once_with(
            self.conn.network.create_port.return_value.id,
            ignore_missing=False)
        self.api.detach_port_from_node.assert_called_once_with(
            self.node, self.conn.network.create_port.return_value.id)

    @mock.patch.object(_provisioner.LOG, 'exception', autospec=True)
    def test_failure_during_deploy_failure(self, mock_log_exc):
        for failed_call in ['detach_port_from_node',
                            'release_node']:
            self._reset_api_mock()
            getattr(self.api, failed_call).side_effect = AssertionError()
            self.api.node_action.side_effect = RuntimeError('boom')
            self.assertRaisesRegex(RuntimeError, 'boom',
                                   self.pr.provision_node, self.node,
                                   'image', [{'network': 'network'}],
                                   wait=3600)

            self.assertFalse(self.wait_mock.called)
            self.api.release_node.assert_called_once_with(self.node)
            self.conn.network.delete_port.assert_called_once_with(
                self.conn.network.create_port.return_value.id,
                ignore_missing=False)
            self.api.detach_port_from_node.assert_called_once_with(
                self.node, self.conn.network.create_port.return_value.id)
            self.assertEqual(mock_log_exc.called,
                             failed_call == 'release_node')

    def test_failure_during_extra_update_on_deploy_failure(self):
        self.api.update_node.side_effect = [self.node, AssertionError()]
        self.api.node_action.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}],
                               wait=3600)

        self.assertFalse(self.wait_mock.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.conn.network.delete_port.assert_called_once_with(
            self.conn.network.create_port.return_value.id,
            ignore_missing=False)
        self.api.detach_port_from_node.assert_called_once_with(
            self.node, self.conn.network.create_port.return_value.id)

    def test_wait_failure(self):
        self.wait_mock.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)
        self.assertFalse(self.api.detach_port_from_node.called)

    def test_missing_image(self):
        self.conn.image.find_image.side_effect = os_exc.ResourceNotFound(
            'Not found')
        self.assertRaisesRegex(exceptions.InvalidImage, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_no_checksum_with_http_image(self, mock_get):
        self.updates['/instance_info/image_source'] = 'https://host/image'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']
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

        self.assertFalse(self.conn.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_malformed_checksum_with_http_image(self, mock_get):
        self.updates['/instance_info/image_source'] = 'https://host/image'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']
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

        self.assertFalse(self.conn.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    @mock.patch.object(requests, 'get', autospec=True)
    def test_cannot_download_checksum_with_http_image(self, mock_get):
        self.updates['/instance_info/image_source'] = 'https://host/image'
        self.updates['/instance_info/image_checksum'] = 'abcd'
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']
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

        self.assertFalse(self.conn.image.find_image.called)
        mock_get.assert_called_once_with('https://host/checksums')
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_network(self):
        self.conn.network.find_network.side_effect = RuntimeError('Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_port(self):
        self.conn.network.find_port.side_effect = RuntimeError('Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}])
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_no_local_gb(self):
        self.node.properties = {}
        self.assertRaises(exceptions.UnknownRootDiskSize,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_local_gb(self):
        for value in (None, 'meow', -42, []):
            self.node.properties = {'local_gb': value}
            self.assertRaises(exceptions.UnknownRootDiskSize,
                              self.pr.provision_node,
                              self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_with(self.node)

    def test_invalid_root_disk_size(self):
        self.assertRaises(TypeError,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}],
                          root_disk_size={})
        self.assertRaises(ValueError,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}],
                          root_disk_size=0)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_with(self.node)

    def test_invalid_nics(self):
        self.assertRaisesRegex(TypeError, 'must be a list',
                               self.pr.provision_node,
                               self.node, 'image', 42)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)

    def test_invalid_nic(self):
        for item in ('string', ['string']):
            self.assertRaisesRegex(TypeError, 'must be a dict',
                                   self.pr.provision_node,
                                   self.node, 'image', item)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)

    def test_invalid_nic_type(self):
        self.assertRaisesRegex(exceptions.InvalidNIC,
                               'Unknown NIC record type',
                               self.pr.provision_node,
                               self.node, 'image', [{'foo': 'bar'}])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_nic_type_fields(self):
        for item in ({'port': '1234', 'foo': 'bar'},
                     {'port': '1234', 'network': '4321'},
                     {'network': '4321', 'foo': 'bar'}):
            self.assertRaisesRegex(exceptions.InvalidNIC,
                                   'Unexpected fields',
                                   self.pr.provision_node,
                                   self.node, 'image', [item])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_with(self.node)

    def test_invalid_hostname(self):
        self.assertRaisesRegex(ValueError, 'n_1 cannot be used as a hostname',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='n_1')
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_duplicate_hostname(self):
        self.api.find_node_by_hostname.return_value = mock.Mock(spec=['uuid',
                                                                      'name'])
        self.assertRaisesRegex(ValueError, 'already uses hostname host',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='host')
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_node_not_found(self):
        self.api.get_node.side_effect = RuntimeError('not found')
        self.assertRaisesRegex(exceptions.InvalidNode, 'not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.release_node.called)

    def test_node_with_external_instance_uuid(self):
        self.node.instance_uuid = 'nova'
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'reserved by instance nova',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.release_node.called)

    def test_node_in_maintenance(self):
        self.node.maintenance = True
        self.node.maintenance_reason = 'power failure'
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'in maintenance mode .* power failure',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.conn.network.create_port.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.release_node.called)

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
        self.wait_fixture = self.useFixture(
            fixtures.MockPatchObject(_provisioner.Provisioner,
                                     '_wait_for_state', autospec=True))
        self.wait_mock = self.wait_fixture.mock

    def test_ok(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        result = self.pr.unprovision_node(self.node)
        self.assertIs(result, self.node)

        self.conn.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        self.api.detach_port_from_node.assert_called_once_with(self.node,
                                                               'port1')
        self.api.node_action.assert_called_once_with(self.node, 'deleted')
        self.api.release_node.assert_called_once_with(self.node)
        self.assertFalse(self.wait_mock.called)
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)

    def test_with_attached(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.node.extra['metalsmith_attached_ports'] = ['port1', 'port2']
        self.pr.unprovision_node(self.node)

        self.conn.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        calls = [mock.call(self.node, 'port1'), mock.call(self.node, 'port2')]
        self.api.detach_port_from_node.assert_has_calls(calls, any_order=True)
        self.api.node_action.assert_called_once_with(self.node, 'deleted')
        self.api.release_node.assert_called_once_with(self.node)
        self.assertFalse(self.wait_mock.called)
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)

    def test_with_wait(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        result = self.pr.unprovision_node(self.node, wait=3600)
        self.assertIs(result, self.node)

        self.conn.network.delete_port.assert_called_once_with(
            'port1', ignore_missing=False)
        self.api.detach_port_from_node.assert_called_once_with(self.node,
                                                               'port1')
        self.api.node_action.assert_called_once_with(self.node, 'deleted')
        self.api.release_node.assert_called_once_with(self.node)
        self.wait_mock.assert_called_once_with(self.pr,
                                               [self.node],
                                               'available',
                                               timeout=3600)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.pr.unprovision_node(self.node)

        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.conn.network.delete_port.called)
        self.assertFalse(self.api.detach_port_from_node.called)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.update_node.called)


class TestShowInstance(Base):
    def test_show_instance(self):
        self.api.get_node.side_effect = lambda n, *a, **kw: self.node
        inst = self.pr.show_instance('uuid1')
        self.api.get_node.assert_called_once_with('uuid1',
                                                  accept_hostname=True)
        self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(inst.node, self.node)
        self.assertIs(inst.uuid, self.node.uuid)
        self.api.cache_node_list_for_lookup.assert_called_once_with()

    def test_show_instances(self):
        self.api.get_node.side_effect = [self.node, mock.Mock()]
        result = self.pr.show_instances(['1', '2'])
        self.api.get_node.assert_has_calls([
            mock.call('1', accept_hostname=True),
            mock.call('2', accept_hostname=True)
        ])
        self.assertIsInstance(result, list)
        for inst in result:
            self.assertIsInstance(inst, _instance.Instance)
        self.assertIs(result[0].node, self.node)
        self.assertIs(result[0].uuid, self.node.uuid)
        self.api.cache_node_list_for_lookup.assert_called_once_with()


@mock.patch('time.sleep', autospec=True)
class TestWaitForState(Base):
    def test_invalid_timeout(self, mock_sleep):
        for invalid in (0, -42):
            self.assertRaisesRegex(ValueError,
                                   'timeout argument must be a positive',
                                   self.pr.wait_for_provisioning,
                                   ['uuid1'], timeout=invalid)

    def test_invalid_delay(self, mock_sleep):
        self.assertRaisesRegex(ValueError,
                               'delay argument must be a non-negative',
                               self.pr.wait_for_provisioning,
                               ['uuid1'], delay=-42)

    def test_success_one_node(self, mock_sleep):
        nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state)
            for state in ('deploying', 'deploy wait', 'deploying', 'active')
        ]
        self.api.get_node.side_effect = nodes

        result = self.pr.wait_for_provisioning(['uuid1'])
        self.assertEqual(nodes[-1:], [inst.node for inst in result])
        self.assertIsInstance(result[0], _instance.Instance)

        mock_sleep.assert_called_with(15)
        self.assertEqual(3, mock_sleep.call_count)

    def test_success_several_nodes(self, mock_sleep):
        nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state)
            for state in ('deploying', 'deploy wait',  # iteration 1
                          'deploying', 'active',       # iteration 2
                          'active')                    # iteration 3
        ]
        self.api.get_node.side_effect = nodes

        result = self.pr.wait_for_provisioning(['uuid1', 'uuid2'])
        self.assertEqual(nodes[-2:], [inst.node for inst in result])
        for inst in result:
            self.assertIsInstance(inst, _instance.Instance)

        mock_sleep.assert_called_with(15)
        self.assertEqual(2, mock_sleep.call_count)

    def test_one_node_failed(self, mock_sleep):
        nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state)
            for state in ('deploying', 'deploy wait',    # iteration 1
                          'deploying', 'deploy failed',  # iteration 2
                          'active')                      # iteration 3
        ]
        self.api.get_node.side_effect = nodes

        exc = self.assertRaises(exceptions.DeploymentFailure,
                                self.pr.wait_for_provisioning,
                                ['uuid1', 'uuid2'])
        # The exception contains the failed node
        self.assertEqual(exc.nodes, [nodes[-2]])

        mock_sleep.assert_called_with(15)
        self.assertEqual(2, mock_sleep.call_count)

    def test_timeout(self, mock_sleep):
        def _fake_get(*args, **kwargs):
            while True:
                yield mock.Mock(spec=NODE_FIELDS,
                                provision_state='deploying')

        self.api.get_node.side_effect = _fake_get()

        exc = self.assertRaises(exceptions.DeploymentFailure,
                                self.pr.wait_for_provisioning,
                                ['uuid1', 'uuid2'],
                                timeout=0.001)
        self.assertEqual(2, len(exc.nodes))

        mock_sleep.assert_called_with(15)

    def test_custom_delay(self, mock_sleep):
        nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state)
            for state in ('deploying', 'deploy wait', 'deploying', 'active')
        ]
        self.api.get_node.side_effect = nodes

        result = self.pr.wait_for_provisioning(['uuid1'], delay=1)
        self.assertEqual(nodes[-1:], [inst.node for inst in result])
        self.assertIsInstance(result[0], _instance.Instance)

        mock_sleep.assert_called_with(1)
        self.assertEqual(3, mock_sleep.call_count)


class TestListInstances(Base):
    def setUp(self):
        super(TestListInstances, self).setUp()
        self.nodes = [
            mock.Mock(spec=NODE_FIELDS, provision_state=state,
                      instance_info={'metalsmith_hostname': '1234'})
            for state in ('active', 'active', 'deploying', 'wait call-back',
                          'deploy failed', 'available')
        ]
        del self.nodes[-1].instance_info['metalsmith_hostname']
        self.api.list_nodes.return_value = self.nodes

    def test_list(self):
        instances = self.pr.list_instances()
        self.assertTrue(isinstance(i, _instance.Instance) for i in instances)
        self.assertEqual(self.nodes[:5], [i.node for i in instances])
        self.api.list_nodes.assert_called_once_with(provision_state=None,
                                                    associated=True)
