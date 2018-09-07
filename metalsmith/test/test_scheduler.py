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

from metalsmith import _scheduler
from metalsmith import exceptions


class TestScheduleNode(testtools.TestCase):

    def setUp(self):
        super(TestScheduleNode, self).setUp()
        self.nodes = [mock.Mock(spec=['uuid', 'name']) for _ in range(2)]
        self.reserver = self._reserver(lambda x: x)

    def _reserver(self, side_effect):
        reserver = mock.Mock(spec=_scheduler.Reserver)
        reserver.side_effect = side_effect
        if isinstance(side_effect, Exception):
            reserver.fail.side_effect = RuntimeError('failed')
        else:
            reserver.fail.side_effect = AssertionError('called fail')
        return reserver

    def _filter(self, side_effect, fail=AssertionError('called fail')):
        fltr = mock.Mock(spec=_scheduler.Filter)
        fltr.side_effect = side_effect
        fltr.fail.side_effect = fail
        return fltr

    def test_no_filters(self):
        result = _scheduler.schedule_node(self.nodes, [], self.reserver)
        self.assertIs(result, self.nodes[0])
        self.reserver.assert_called_once_with(self.nodes[0])
        self.assertFalse(self.reserver.fail.called)

    def test_dry_run(self):
        result = _scheduler.schedule_node(self.nodes, [], self.reserver,
                                          dry_run=True)
        self.assertIs(result, self.nodes[0])
        self.assertFalse(self.reserver.called)
        self.assertFalse(self.reserver.fail.called)

    def test_reservation_one_failed(self):
        reserver = self._reserver([Exception("boom"), self.nodes[1]])
        result = _scheduler.schedule_node(self.nodes, [], reserver)
        self.assertIs(result, self.nodes[1])
        self.assertEqual([mock.call(n) for n in self.nodes],
                         reserver.call_args_list)

    def test_reservation_all_failed(self):
        reserver = self._reserver(Exception("boom"))
        self.assertRaisesRegex(RuntimeError, 'failed',
                               _scheduler.schedule_node,
                               self.nodes, [], reserver)
        self.assertEqual([mock.call(n) for n in self.nodes],
                         reserver.call_args_list)

    def test_all_filters_pass(self):
        filters = [self._filter([True, True]) for _ in range(3)]
        result = _scheduler.schedule_node(self.nodes, filters, self.reserver)
        self.assertIs(result, self.nodes[0])
        self.reserver.assert_called_once_with(self.nodes[0])
        for fltr in filters:
            self.assertEqual([mock.call(n) for n in self.nodes],
                             fltr.call_args_list)
            self.assertFalse(fltr.fail.called)

    def test_one_node_filtered(self):
        filters = [self._filter([True, True]),
                   self._filter([False, True]),
                   self._filter([True])]
        result = _scheduler.schedule_node(self.nodes, filters, self.reserver)
        self.assertIs(result, self.nodes[1])
        self.reserver.assert_called_once_with(self.nodes[1])
        for fltr in filters:
            self.assertFalse(fltr.fail.called)
        for fltr in filters[:2]:
            self.assertEqual([mock.call(n) for n in self.nodes],
                             fltr.call_args_list)
        filters[2].assert_called_once_with(self.nodes[1])

    def test_all_nodes_filtered(self):
        filters = [self._filter([True, True]),
                   self._filter([False, True]),
                   self._filter([False], fail=RuntimeError('failed'))]
        self.assertRaisesRegex(RuntimeError, 'failed',
                               _scheduler.schedule_node,
                               self.nodes, filters, self.reserver)
        self.assertFalse(self.reserver.called)
        for fltr in filters[:2]:
            self.assertEqual([mock.call(n) for n in self.nodes],
                             fltr.call_args_list)
            self.assertFalse(fltr.fail.called)
        filters[2].assert_called_once_with(self.nodes[1])
        filters[2].fail.assert_called_once_with()


class TestCapabilitiesFilter(testtools.TestCase):

    def test_fail_no_capabilities(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute'})
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: none',
                               fltr.fail)

    def test_nothing_requested_nothing_found(self):
        fltr = _scheduler.CapabilitiesFilter({})
        node = mock.Mock(properties={}, spec=['properties', 'name', 'uuid'])
        self.assertTrue(fltr(node))

    def test_matching_node(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute',
                                              'foo': 'bar'})
        node = mock.Mock(
            properties={'capabilities': 'foo:bar,profile:compute,answer:42'},
            spec=['properties', 'name', 'uuid'])
        self.assertTrue(fltr(node))

    def test_not_matching_node(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute',
                                              'foo': 'bar'})
        node = mock.Mock(
            properties={'capabilities': 'foo:bar,answer:42'},
            spec=['properties', 'name', 'uuid'])
        self.assertFalse(fltr(node))

    def test_fail_message(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute'})
        node = mock.Mock(
            properties={'capabilities': 'profile:control'},
            spec=['properties', 'name', 'uuid'])
        self.assertFalse(fltr(node))
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: '
                               r'profile=control \(1 node\(s\)\)',
                               fltr.fail)

    def test_malformed_capabilities(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute'})
        for cap in ['foo,profile:control', 42, 'a:b:c']:
            node = mock.Mock(properties={'capabilities': cap},
                             spec=['properties', 'name', 'uuid'])
            self.assertFalse(fltr(node))
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: none',
                               fltr.fail)


class TestTraitsFilter(testtools.TestCase):

    def test_fail_no_traits(self):
        fltr = _scheduler.TraitsFilter(['tr1', 'tr2'])
        self.assertRaisesRegex(exceptions.TraitsNotFound,
                               'No available nodes found with traits '
                               'tr1, tr2, existing traits: none',
                               fltr.fail)

    def test_no_traits(self):
        fltr = _scheduler.TraitsFilter([])
        node = mock.Mock(spec=['name', 'uuid'])
        self.assertTrue(fltr(node))

    def test_ok(self):
        fltr = _scheduler.TraitsFilter(['tr1', 'tr2'])
        node = mock.Mock(spec=['name', 'uuid', 'traits'],
                         traits=['tr3', 'tr2', 'tr1'])
        self.assertTrue(fltr(node))

    def test_missing_one(self):
        fltr = _scheduler.TraitsFilter(['tr1', 'tr2'])
        node = mock.Mock(spec=['name', 'uuid', 'traits'],
                         traits=['tr3', 'tr1'])
        self.assertFalse(fltr(node))

    def test_missing_all(self):
        fltr = _scheduler.TraitsFilter(['tr1', 'tr2'])
        node = mock.Mock(spec=['name', 'uuid', 'traits'], traits=None)
        self.assertFalse(fltr(node))


class TestIronicReserver(testtools.TestCase):

    def setUp(self):
        super(TestIronicReserver, self).setUp()
        self.node = mock.Mock(spec=['uuid', 'name'])
        self.api = mock.Mock(spec=['reserve_node', 'release_node',
                                   'validate_node'])
        self.api.reserve_node.side_effect = lambda node, instance_uuid: node
        self.reserver = _scheduler.IronicReserver(self.api)

    def test_fail(self):
        self.assertRaisesRegex(exceptions.NoNodesReserved,
                               'All the candidate nodes are already reserved',
                               self.reserver.fail)

    def test_ok(self):
        self.assertEqual(self.node, self.reserver(self.node))
        self.api.validate_node.assert_called_with(self.node)
        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)

    def test_reservation_failed(self):
        self.api.reserve_node.side_effect = RuntimeError('conflict')
        self.assertRaisesRegex(RuntimeError, 'conflict',
                               self.reserver, self.node)
        self.api.validate_node.assert_called_with(self.node)
        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)

    def test_validation_failed(self):
        self.api.validate_node.side_effect = RuntimeError('fail')
        self.assertRaisesRegex(exceptions.ValidationFailed, 'fail',
                               self.reserver, self.node)
        self.api.validate_node.assert_called_once_with(self.node)
        self.assertFalse(self.api.reserve_node.called)
        self.assertFalse(self.api.release_node.called)
