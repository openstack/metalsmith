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

from metalsmith import _instance
from metalsmith import _os_api


class TestNodes(testtools.TestCase):
    def setUp(self):
        super(TestNodes, self).setUp()
        self.session = mock.Mock()
        self.ironic_fixture = self.useFixture(
            fixtures.MockPatchObject(_os_api.ir_client, 'get_client',
                                     autospec=True))
        self.cli = self.ironic_fixture.mock.return_value
        self.api = _os_api.API(session=self.session, connection=mock.Mock())

    def test_get_node_by_uuid(self):
        res = self.api.get_node('uuid1')
        self.cli.node.get.assert_called_once_with('uuid1')
        self.assertIs(res, self.cli.node.get.return_value)

    def test_get_node_by_hostname(self):
        self.cli.node.list.return_value = [
            mock.Mock(uuid='uuid0', instance_info={}),
            mock.Mock(uuid='uuid1',
                      instance_info={'metalsmith_hostname': 'host1'}),
        ]
        res = self.api.get_node('host1', accept_hostname=True)
        # Loading details
        self.cli.node.get.assert_called_once_with('uuid1')
        self.assertIs(res, self.cli.node.get.return_value)

    def test_get_node_by_hostname_not_found(self):
        self.cli.node.list.return_value = [
            mock.Mock(uuid='uuid0', instance_info={}),
            mock.Mock(uuid='uuid1',
                      instance_info={'metalsmith_hostname': 'host0'}),
        ]
        res = self.api.get_node('host1', accept_hostname=True)
        # Loading details
        self.cli.node.get.assert_called_once_with('host1')
        self.assertIs(res, self.cli.node.get.return_value)

    def test_get_node_by_node(self):
        res = self.api.get_node(mock.sentinel.node)
        self.assertIs(res, mock.sentinel.node)
        self.assertFalse(self.cli.node.get.called)

    def test_get_node_by_node_with_refresh(self):
        res = self.api.get_node(mock.Mock(spec=['uuid'], uuid='uuid1'),
                                refresh=True)
        self.cli.node.get.assert_called_once_with('uuid1')
        self.assertIs(res, self.cli.node.get.return_value)

    def test_get_node_by_instance(self):
        inst = _instance.Instance(mock.Mock(), mock.Mock())
        res = self.api.get_node(inst)
        self.assertIs(res, inst.node)
        self.assertFalse(self.cli.node.get.called)

    def test_get_node_by_instance_with_refresh(self):
        inst = _instance.Instance(mock.Mock(),
                                  mock.Mock(spec=['uuid'], uuid='uuid1'))
        res = self.api.get_node(inst, refresh=True)
        self.cli.node.get.assert_called_once_with('uuid1')
        self.assertIs(res, self.cli.node.get.return_value)

    def test_find_node_by_hostname(self):
        self.cli.node.list.return_value = [
            mock.Mock(uuid='uuid0', instance_info={}),
            mock.Mock(uuid='uuid1',
                      instance_info={'metalsmith_hostname': 'host1'}),
        ]
        res = self.api.find_node_by_hostname('host1')
        # Loading details
        self.cli.node.get.assert_called_once_with('uuid1')
        self.assertIs(res, self.cli.node.get.return_value)

    def test_find_node_by_hostname_cached(self):
        self.cli.node.list.return_value = [
            mock.Mock(uuid='uuid0', instance_info={}),
            mock.Mock(uuid='uuid1',
                      instance_info={'metalsmith_hostname': 'host1'}),
        ]
        with self.api.cache_node_list_for_lookup():
            res = self.api.find_node_by_hostname('host1')
            self.assertIs(res, self.cli.node.get.return_value)
            self.assertIsNone(self.api.find_node_by_hostname('host2'))
        self.assertEqual(1, self.cli.node.list.call_count)
        # This call is no longer cached
        self.assertIsNone(self.api.find_node_by_hostname('host2'))
        self.assertEqual(2, self.cli.node.list.call_count)

    def test_find_node_by_hostname_not_found(self):
        self.cli.node.list.return_value = [
            mock.Mock(uuid='uuid0', instance_info={}),
            mock.Mock(uuid='uuid1',
                      instance_info={'metalsmith_hostname': 'host1'}),
        ]
        self.assertIsNone(self.api.find_node_by_hostname('host0'))
        self.assertFalse(self.cli.node.get.called)

    def test_find_node_by_hostname_duplicate(self):
        self.cli.node.list.return_value = [
            mock.Mock(uuid='uuid0',
                      instance_info={'metalsmith_hostname': 'host1'}),
            mock.Mock(uuid='uuid1',
                      instance_info={'metalsmith_hostname': 'host1'}),
        ]
        self.assertRaisesRegex(RuntimeError, 'More than one node',
                               self.api.find_node_by_hostname, 'host1')
        self.assertFalse(self.cli.node.get.called)
