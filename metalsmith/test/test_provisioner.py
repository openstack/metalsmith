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

from metalsmith import _exceptions
from metalsmith import _provisioner


class TestReserveNode(testtools.TestCase):

    def setUp(self):
        super(TestReserveNode, self).setUp()
        self.api = mock.Mock(spec=['list_nodes', 'reserve_node',
                                   'validate_node'])
        self.pr = _provisioner.Provisioner(mock.Mock())
        self.pr._api = self.api

    def test_no_nodes(self):
        self.api.list_nodes.return_value = []

        self.assertRaises(_exceptions.ResourceClassNotFound,
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


class TestProvisionNode(testtools.TestCase):

    def setUp(self):
        super(TestProvisionNode, self).setUp()
        self.api = mock.Mock(spec=['get_node', 'get_image_info', 'get_network',
                                   'update_node', 'validate_node',
                                   'create_port', 'attach_port_to_node',
                                   'node_action', 'wait_for_active'])
        self.api.get_node.side_effect = lambda n: n
        self.api.update_node.side_effect = lambda n, _u: n
        self.pr = _provisioner.Provisioner(mock.Mock())
        self.pr._api = self.api
        self.node = mock.Mock(spec=['name', 'uuid', 'properties'],
                              uuid='000', properties={'local_gb': 100})
        self.node.name = 'control-0'

    def test_ok(self):
        self.pr.provision_node(self.node, 'image', ['network'])
