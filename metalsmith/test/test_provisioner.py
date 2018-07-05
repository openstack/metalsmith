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
import testtools

from metalsmith import _config
from metalsmith import _instance
from metalsmith import _os_api
from metalsmith import _provisioner
from metalsmith import exceptions


class Base(testtools.TestCase):

    def setUp(self):
        super(Base, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self._reset_api_mock()
        self.node = mock.Mock(spec=_os_api.NODE_FIELDS + ['to_dict'],
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


class TestReserveNode(Base):

    def test_no_nodes(self):
        self.api.list_nodes.return_value = []

        self.assertRaises(exceptions.ResourceClassNotFound,
                          self.pr.reserve_node, 'control')
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

        node = self.pr.reserve_node('control', {'answer': '42'})

        self.assertIs(node, expected)
        self.api.update_node.assert_called_once_with(
            node, {'/instance_info/capabilities': {'answer': '42'}})

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


CLEAN_UP = {
    '/extra/metalsmith_created_ports': _os_api.REMOVE,
    '/extra/metalsmith_attached_ports': _os_api.REMOVE,
    '/instance_info/%s' % _os_api.HOSTNAME_FIELD: _os_api.REMOVE
}


class TestProvisionNode(Base):

    def setUp(self):
        super(TestProvisionNode, self).setUp()
        image = self.api.get_image.return_value
        self.node.instance_uuid = self.node.uuid
        self.updates = {
            '/instance_info/ramdisk': image.ramdisk_id,
            '/instance_info/kernel': image.kernel_id,
            '/instance_info/image_source': image.id,
            '/instance_info/root_gb': 99,  # 100 - 1
            '/instance_info/capabilities': {'boot_option': 'local'},
            '/extra/metalsmith_created_ports': [
                self.api.create_port.return_value.id
            ],
            '/extra/metalsmith_attached_ports': [
                self.api.create_port.return_value.id
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

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_config(self):
        config = mock.MagicMock(spec=_config.InstanceConfig)
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      config=config)

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        config.build_configdrive_directory.assert_called_once_with(
            self.node, self.node.name)
        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_hostname(self):
        hostname = 'control-0.example.com'
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      hostname=hostname)
        self.updates['/instance_info/%s' % _os_api.HOSTNAME_FIELD] = hostname

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_name_not_valid_hostname(self):
        self.node.name = 'node_1'
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])
        self.updates['/instance_info/%s' % _os_api.HOSTNAME_FIELD] = '000'

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_unreserved(self):
        self.node.instance_uuid = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)
        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_ports(self):
        self.updates['/extra/metalsmith_created_ports'] = []
        self.updates['/extra/metalsmith_attached_ports'] = [
            self.api.get_port.return_value.id
        ] * 2

        self.pr.provision_node(self.node, 'image',
                               [{'port': 'port1'}, {'port': 'port2'}])

        self.assertFalse(self.api.create_port.called)
        self.api.attach_port_to_node.assert_called_with(
            self.node.uuid, self.api.get_port.return_value.id)
        self.assertEqual(2, self.api.attach_port_to_node.call_count)
        self.assertEqual([mock.call('port1'), mock.call('port2')],
                         self.api.get_port.call_args_list)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_whole_disk(self):
        image = self.api.get_image.return_value
        image.kernel_id = None
        image.ramdisk_id = None
        del self.updates['/instance_info/kernel']
        del self.updates['/instance_info/ramdisk']

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_root_disk_size(self):
        self.updates['/instance_info/root_gb'] = 50

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               root_disk_size=50)

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_capabilities(self):
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}],
                                      capabilities={'answer': '42'})
        self.updates['/instance_info/capabilities'] = {'boot_option': 'local',
                                                       'answer': '42'}

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_existing_capabilities(self):
        self.node.instance_info['capabilities'] = {'answer': '42'}
        inst = self.pr.provision_node(self.node, 'image',
                                      [{'network': 'network'}])
        self.updates['/instance_info/capabilities'] = {'boot_option': 'local',
                                                       'answer': '42'}

        self.assertEqual(inst.uuid, self.node.uuid)
        self.assertEqual(inst.node, self.node)

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

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

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.validate_node.assert_called_once_with(self.node,
                                                       validate_deploy=True)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_with_wait(self):
        self.api.get_port.return_value = mock.Mock(
            spec=['fixed_ips'],
            fixed_ips=[{'ip_address': '192.168.1.5'}, {}]
        )
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               wait=3600)

        self.api.create_port.assert_called_once_with(
            network_id=self.api.get_network.return_value.id)
        self.api.attach_port_to_node.assert_called_once_with(
            self.node.uuid, self.api.create_port.return_value.id)
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
        self.assertFalse(self.api.delete_port.called)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_unreserve_dry_run(self):
        self.pr._dry_run = True
        self.node.instance_uuid = None

        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.reserve_node.called)
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.wait_mock.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_deploy_failure(self):
        self.api.node_action.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'n1'}, {'port': 'p1'}],
                               wait=3600)

        self.api.update_node.assert_any_call(self.node, CLEAN_UP)
        self.assertFalse(self.wait_mock.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.api.delete_port.assert_called_once_with(
            self.api.create_port.return_value.id)
        calls = [
            mock.call(self.node, self.api.create_port.return_value.id),
            mock.call(self.node, self.api.get_port.return_value.id)
        ]
        self.api.detach_port_from_node.assert_has_calls(calls, any_order=True)

    def test_port_creation_failure(self):
        self.api.create_port.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.assertFalse(self.api.delete_port.called)
        self.assertFalse(self.api.detach_port_from_node.called)

    def test_port_attach_failure(self):
        self.api.attach_port_to_node.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.api.delete_port.assert_called_once_with(
            self.api.create_port.return_value.id)
        self.api.detach_port_from_node.assert_called_once_with(
            self.node, self.api.create_port.return_value.id)

    @mock.patch.object(_provisioner.LOG, 'exception', autospec=True)
    def test_failure_during_deploy_failure(self, mock_log_exc):
        for failed_call in ['detach_port_from_node',
                            'delete_port', 'release_node']:
            self._reset_api_mock()
            getattr(self.api, failed_call).side_effect = AssertionError()
            self.api.node_action.side_effect = RuntimeError('boom')
            self.assertRaisesRegex(RuntimeError, 'boom',
                                   self.pr.provision_node, self.node,
                                   'image', [{'network': 'network'}],
                                   wait=3600)

            self.assertFalse(self.wait_mock.called)
            self.api.release_node.assert_called_once_with(self.node)
            self.api.delete_port.assert_called_once_with(
                self.api.create_port.return_value.id)
            self.api.detach_port_from_node.assert_called_once_with(
                self.node, self.api.create_port.return_value.id)
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
        self.api.delete_port.assert_called_once_with(
            self.api.create_port.return_value.id)
        self.api.detach_port_from_node.assert_called_once_with(
            self.node, self.api.create_port.return_value.id)

    def test_wait_failure(self):
        self.wait_mock.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'network'}], wait=3600)

        self.api.update_node.assert_called_once_with(self.node, self.updates)
        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)
        self.assertFalse(self.api.detach_port_from_node.called)

    def test_missing_image(self):
        self.api.get_image.side_effect = RuntimeError('Not found')
        self.assertRaisesRegex(exceptions.InvalidImage, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_network(self):
        self.api.get_network.side_effect = RuntimeError('Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_port(self):
        self.api.get_port.side_effect = RuntimeError('Not found')
        self.assertRaisesRegex(exceptions.InvalidNIC, 'Not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}])
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_no_local_gb(self):
        self.node.properties = {}
        self.assertRaises(exceptions.UnknownRootDiskSize,
                          self.pr.provision_node,
                          self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_local_gb(self):
        for value in (None, 'meow', -42, []):
            self.node.properties = {'local_gb': value}
            self.assertRaises(exceptions.UnknownRootDiskSize,
                              self.pr.provision_node,
                              self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.create_port.called)
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
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_with(self.node)

    def test_invalid_nics(self):
        self.assertRaisesRegex(TypeError, 'must be a list',
                               self.pr.provision_node,
                               self.node, 'image', 42)
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_nic(self):
        for item in ('string', ['string'], [{1: 2, 3: 4}]):
            self.assertRaisesRegex(TypeError, 'must be a dict',
                                   self.pr.provision_node,
                                   self.node, 'image', item)
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_with(self.node)

    def test_invalid_nic_type(self):
        self.assertRaisesRegex(ValueError, r'Unexpected NIC type\(s\) foo',
                               self.pr.provision_node,
                               self.node, 'image', [{'foo': 'bar'}])
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_invalid_hostname(self):
        self.assertRaisesRegex(ValueError, 'n_1 cannot be used as a hostname',
                               self.pr.provision_node,
                               self.node, 'image', [{'port': 'port1'}],
                               hostname='n_1')
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)
        self.assertFalse(self.api.create_port.called)
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
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.node_action.called)
        self.api.release_node.assert_called_once_with(self.node)

    def test_node_not_found(self):
        self.api.get_node.side_effect = RuntimeError('not found')
        self.assertRaisesRegex(exceptions.InvalidNode, 'not found',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.release_node.called)

    def test_node_with_external_instance_uuid(self):
        self.node.instance_uuid = 'nova'
        self.assertRaisesRegex(exceptions.InvalidNode,
                               'reserved by instance nova',
                               self.pr.provision_node,
                               self.node, 'image', [{'network': 'network'}])
        self.assertFalse(self.api.create_port.called)
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
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.release_node.called)


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

        self.api.delete_port.assert_called_once_with('port1')
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

        self.api.delete_port.assert_called_once_with('port1')
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

        self.api.delete_port.assert_called_once_with('port1')
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
        self.assertFalse(self.api.delete_port.called)
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
            mock.Mock(spec=_os_api.NODE_FIELDS, provision_state=state)
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
            mock.Mock(spec=_os_api.NODE_FIELDS, provision_state=state)
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
            mock.Mock(spec=_os_api.NODE_FIELDS, provision_state=state)
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
                yield mock.Mock(spec=_os_api.NODE_FIELDS,
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
            mock.Mock(spec=_os_api.NODE_FIELDS, provision_state=state)
            for state in ('deploying', 'deploy wait', 'deploying', 'active')
        ]
        self.api.get_node.side_effect = nodes

        result = self.pr.wait_for_provisioning(['uuid1'], delay=1)
        self.assertEqual(nodes[-1:], [inst.node for inst in result])
        self.assertIsInstance(result[0], _instance.Instance)

        mock_sleep.assert_called_with(1)
        self.assertEqual(3, mock_sleep.call_count)
