# Copyright 2015-2017 Red Hat, Inc.
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

from ironicclient import exc as ir_exc
import mock

from metalsmith import deploy
from metalsmith import os_api


class TestReserve(unittest.TestCase):
    def setUp(self):
        super(TestReserve, self).setUp()
        self.api = mock.Mock(spec=os_api.API)

    def test_ok(self):
        nodes = [mock.Mock(uuid='1', properties={'local_gb': 42}),
                 mock.Mock(uuid='2', properties={'local_gb': 42})]

        node = deploy.reserve(self.api, nodes, {})

        self.assertEqual(self.api.update_node.return_value, node)
        self.api.validate_node.assert_called_once_with('1')
        self.api.update_node.assert_called_once_with('1', instance_uuid='1')

    def test_validation_failed(self):
        nodes = [mock.Mock(uuid='1', properties={'local_gb': 42}),
                 mock.Mock(uuid='2', properties={'local_gb': 42})]
        self.api.validate_node.side_effect = [RuntimeError('boom'), None]

        node = deploy.reserve(self.api, nodes, {})

        self.assertEqual(self.api.update_node.return_value, node)
        self.assertEqual([mock.call('1'), mock.call('2')],
                         self.api.validate_node.call_args_list)
        self.api.update_node.assert_called_once_with('2', instance_uuid='2')

    def test_with_capabilities(self):
        nodes = [mock.Mock(uuid='1', properties={'local_gb': 42}),
                 mock.Mock(uuid='2', properties={'local_gb': 42,
                                                 'capabilities': '1:2,3:4'})]

        node = deploy.reserve(self.api, nodes, {'3': '4'})

        self.assertEqual(self.api.update_node.return_value, node)
        self.api.validate_node.assert_called_once_with('2')
        self.api.update_node.assert_called_once_with('2', instance_uuid='2')

    def test_no_capabilities(self):
        nodes = [mock.Mock(uuid='1', properties={'local_gb': 42}),
                 mock.Mock(uuid='2', properties={'local_gb': 42,
                                                 'capabilities': '1:2,3:4'})]

        self.assertRaisesRegexp(RuntimeError,
                                'No nodes found with capabilities',
                                deploy.reserve, self.api, nodes, {'3': '5'})

        self.assertFalse(self.api.validate_node.called)
        self.assertFalse(self.api.update_node.called)

    def test_conflict(self):
        nodes = [mock.Mock(uuid='1', properties={'local_gb': 42}),
                 mock.Mock(uuid='2', properties={'local_gb': 42})]
        self.api.update_node.side_effect = [ir_exc.Conflict(''), 'node']

        node = deploy.reserve(self.api, nodes, {})

        self.assertEqual('node', node)
        self.assertEqual([mock.call('1'), mock.call('2')],
                         self.api.validate_node.call_args_list)
        self.assertEqual([mock.call('1', instance_uuid='1'),
                          mock.call('2', instance_uuid='2')],
                         self.api.update_node.call_args_list)
