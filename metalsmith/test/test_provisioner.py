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
import testtools

from metalsmith import _os_api
from metalsmith import _provisioner
from metalsmith import exceptions


class Base(testtools.TestCase):

    def setUp(self):
        super(Base, self).setUp()
        self.pr = _provisioner.Provisioner(mock.Mock())
        self._reset_api_mock()
        self.node = mock.Mock(spec=_os_api.NODE_FIELDS,
                              uuid='000', instance_uuid=None,
                              properties={'local_gb': 100},
                              maintenance=False, extra={})
        self.node.name = 'control-0'

    def _reset_api_mock(self):
        self.api = mock.Mock(spec=_os_api.API)
        self.api.get_node.side_effect = lambda n: n
        self.api.update_node.side_effect = lambda n, _u: n
        self.api.list_node_ports.return_value = [
            mock.Mock(spec=['uuid', 'pxe_enabled'],
                      uuid=uuid, pxe_enabled=pxe)
            for (uuid, pxe) in [('000', True), ('111', False)]
        ]
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


CLEAN_UP = {
    '/extra/metalsmith_created_ports': _os_api.REMOVE,
    '/extra/metalsmith_attached_ports': _os_api.REMOVE
}


class TestProvisionNode(Base):

    def setUp(self):
        super(TestProvisionNode, self).setUp()
        image = self.api.get_image_info.return_value
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
            ]
        }

    def test_ok(self):
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
        self.assertFalse(self.api.wait_for_node_state.called)
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
        self.assertFalse(self.api.wait_for_node_state.called)
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
        self.assertFalse(self.api.wait_for_node_state.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_whole_disk(self):
        image = self.api.get_image_info.return_value
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
        self.assertFalse(self.api.wait_for_node_state.called)
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
        self.assertFalse(self.api.wait_for_node_state.called)
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
        self.api.wait_for_node_state.assert_called_once_with(self.node,
                                                             'active',
                                                             timeout=3600)
        self.api.get_port.assert_called_once_with(
            self.api.create_port.return_value.id)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    @mock.patch.object(_provisioner.LOG, 'warning', autospec=True)
    def test_with_wait_no_ips(self, mock_warn):
        self.api.get_port.return_value = mock.Mock(
            spec=['fixed_ips'], fixed_ips=[]
        )
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}],
                               wait=3600)

        self.api.node_action.assert_called_once_with(self.node, 'active',
                                                     configdrive=mock.ANY)
        self.api.wait_for_node_state.assert_called_once_with(self.node,
                                                             'active',
                                                             timeout=3600)
        mock_warn.assert_called_once_with('No IPs for node %s', mock.ANY)

    def test_dry_run(self):
        self.pr._dry_run = True
        self.pr.provision_node(self.node, 'image', [{'network': 'network'}])

        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
        self.assertFalse(self.api.update_node.called)
        self.assertFalse(self.api.node_action.called)
        self.assertFalse(self.api.wait_for_node_state.called)
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
        self.assertFalse(self.api.wait_for_node_state.called)
        self.assertFalse(self.api.release_node.called)
        self.assertFalse(self.api.delete_port.called)

    def test_deploy_failure(self):
        self.api.node_action.side_effect = RuntimeError('boom')
        self.assertRaisesRegex(RuntimeError, 'boom',
                               self.pr.provision_node, self.node,
                               'image', [{'network': 'n1'}, {'port': 'p1'}],
                               wait=3600)

        self.api.update_node.assert_any_call(self.node, CLEAN_UP)
        self.assertFalse(self.api.wait_for_node_state.called)
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

            self.assertFalse(self.api.wait_for_node_state.called)
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

        self.assertFalse(self.api.wait_for_node_state.called)
        self.api.release_node.assert_called_once_with(self.node)
        self.api.delete_port.assert_called_once_with(
            self.api.create_port.return_value.id)
        self.api.detach_port_from_node.assert_called_once_with(
            self.node, self.api.create_port.return_value.id)

    def test_missing_image(self):
        self.api.get_image_info.side_effect = RuntimeError('Not found')
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
        self.assertRaisesRegex(ValueError, 'Unexpected NIC type foo',
                               self.pr.provision_node,
                               self.node, 'image', [{'foo': 'bar'}])
        self.assertFalse(self.api.create_port.called)
        self.assertFalse(self.api.attach_port_to_node.called)
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

    def test_ok(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.pr.unprovision_node(self.node)

        self.api.delete_port.assert_called_once_with('port1')
        self.api.detach_port_from_node.assert_called_once_with(self.node,
                                                               'port1')
        self.api.node_action.assert_called_once_with(self.node, 'deleted')
        self.api.release_node.assert_called_once_with(self.node)
        self.assertFalse(self.api.wait_for_node_state.called)
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
        self.assertFalse(self.api.wait_for_node_state.called)
        self.api.update_node.assert_called_once_with(self.node, CLEAN_UP)

    def test_with_wait(self):
        self.node.extra['metalsmith_created_ports'] = ['port1']
        self.pr.unprovision_node(self.node, wait=3600)

        self.api.delete_port.assert_called_once_with('port1')
        self.api.detach_port_from_node.assert_called_once_with(self.node,
                                                               'port1')
        self.api.node_action.assert_called_once_with(self.node, 'deleted')
        self.api.release_node.assert_called_once_with(self.node)
        self.api.wait_for_node_state.assert_called_once_with(self.node,
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
        self.assertFalse(self.api.wait_for_node_state.called)
        self.assertFalse(self.api.update_node.called)
