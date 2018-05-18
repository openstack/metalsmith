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
        fltr = _scheduler.CapabilitiesFilter('rsc', {'profile': 'compute'})
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: none',
                               fltr.fail)

    def test_nothing_requested_nothing_found(self):
        fltr = _scheduler.CapabilitiesFilter('rsc', {})
        node = mock.Mock(properties={}, spec=['properties', 'name', 'uuid'])
        self.assertTrue(fltr(node))

    def test_matching_node(self):
        fltr = _scheduler.CapabilitiesFilter('rsc', {'profile': 'compute',
                                                     'foo': 'bar'})
        node = mock.Mock(
            properties={'capabilities': 'foo:bar,profile:compute,answer:42'},
            spec=['properties', 'name', 'uuid'])
        self.assertTrue(fltr(node))

    def test_not_matching_node(self):
        fltr = _scheduler.CapabilitiesFilter('rsc', {'profile': 'compute',
                                                     'foo': 'bar'})
        node = mock.Mock(
            properties={'capabilities': 'foo:bar,answer:42'},
            spec=['properties', 'name', 'uuid'])
        self.assertFalse(fltr(node))

    def test_fail_message(self):
        fltr = _scheduler.CapabilitiesFilter('rsc', {'profile': 'compute'})
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
        fltr = _scheduler.CapabilitiesFilter('rsc', {'profile': 'compute'})
        for cap in ['foo,profile:control', 42, 'a:b:c']:
            node = mock.Mock(properties={'capabilities': cap},
                             spec=['properties', 'name', 'uuid'])
            self.assertFalse(fltr(node))
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: none',
                               fltr.fail)


class TestValidationFilter(testtools.TestCase):

    def setUp(self):
        super(TestValidationFilter, self).setUp()
        self.api = mock.Mock(spec=['validate_node'])
        self.fltr = _scheduler.ValidationFilter(self.api, 'rsc',
                                                {'profile': 'compute'})

    def test_pass(self):
        node = mock.Mock(spec=['uuid', 'name'])
        self.assertTrue(self.fltr(node))

    def test_fail_validation(self):
        node = mock.Mock(spec=['uuid', 'name'])
        self.api.validate_node.side_effect = RuntimeError('boom')
        self.assertFalse(self.fltr(node))

        self.assertRaisesRegex(exceptions.ValidationFailed,
                               'All available nodes have failed validation: '
                               'Node .* failed validation: boom',
                               self.fltr.fail)


@mock.patch.object(_scheduler, 'ValidationFilter', autospec=True)
class TestIronicReserver(testtools.TestCase):

    def setUp(self):
        super(TestIronicReserver, self).setUp()
        self.node = mock.Mock(spec=['uuid', 'name'])
        self.api = mock.Mock(spec=['reserve_node', 'release_node'])
        self.api.reserve_node.side_effect = lambda node, instance_uuid: node
        self.reserver = _scheduler.IronicReserver(self.api, 'rsc', {})

    def test_fail(self, mock_validation):
        self.assertRaisesRegex(exceptions.AllNodesReserved,
                               'All the candidate nodes are already reserved',
                               self.reserver.fail)

    def test_ok(self, mock_validation):
        self.assertEqual(self.node, self.reserver(self.node))
        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)
        mock_validation.return_value.assert_called_once_with(self.node)

    def test_reservation_failed(self, mock_validation):
        self.api.reserve_node.side_effect = RuntimeError('conflict')
        self.assertRaisesRegex(RuntimeError, 'conflict',
                               self.reserver, self.node)
        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)
        self.assertFalse(mock_validation.return_value.called)

    def test_validation_failed(self, mock_validation):
        mock_validation.return_value.return_value = False
        mock_validation.return_value.fail.side_effect = RuntimeError('fail')
        self.assertRaisesRegex(RuntimeError, 'fail',
                               self.reserver, self.node)
        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)
        mock_validation.return_value.assert_called_once_with(self.node)
        self.api.release_node.assert_called_once_with(self.node)

    @mock.patch.object(_scheduler.LOG, 'exception', autospec=True)
    def test_validation_and_release_failed(self, mock_log_exc,
                                           mock_validation):
        mock_validation.return_value.return_value = False
        mock_validation.return_value.fail.side_effect = RuntimeError('fail')
        self.api.release_node.side_effect = Exception()
        self.assertRaisesRegex(RuntimeError, 'fail',
                               self.reserver, self.node)
        self.api.reserve_node.assert_called_once_with(
            self.node, instance_uuid=self.node.uuid)
        mock_validation.return_value.assert_called_once_with(self.node)
        self.api.release_node.assert_called_once_with(self.node)
        self.assertTrue(mock_log_exc.called)
