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

from metalsmith import _scheduler
from metalsmith import exceptions


class TestRunFilters(unittest.TestCase):

    def setUp(self):
        super(TestRunFilters, self).setUp()
        self.nodes = [mock.Mock(spec=['id', 'name']) for _ in range(2)]

    def _filter(self, side_effect, fail=AssertionError('called fail')):
        fltr = mock.Mock(spec=_scheduler.Filter)
        fltr.side_effect = side_effect
        fltr.fail.side_effect = fail
        return fltr

    def test_no_filters(self):
        result = _scheduler.run_filters([], self.nodes)
        self.assertEqual(result, self.nodes)

    def test_all_filters_pass(self):
        filters = [self._filter([True, True]) for _ in range(3)]
        result = _scheduler.run_filters(filters, self.nodes)
        self.assertEqual(result, self.nodes)
        for fltr in filters:
            self.assertEqual([mock.call(n) for n in self.nodes],
                             fltr.call_args_list)
            self.assertFalse(fltr.fail.called)

    def test_one_node_filtered(self):
        filters = [self._filter([True, True]),
                   self._filter([False, True]),
                   self._filter([True])]
        result = _scheduler.run_filters(filters, self.nodes)
        self.assertEqual(result, self.nodes[1:2])
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
                               _scheduler.run_filters,
                               filters, self.nodes)
        for fltr in filters[:2]:
            self.assertEqual([mock.call(n) for n in self.nodes],
                             fltr.call_args_list)
            self.assertFalse(fltr.fail.called)
        filters[2].assert_called_once_with(self.nodes[1])
        filters[2].fail.assert_called_once_with()


class TestCapabilitiesFilter(unittest.TestCase):

    def test_fail_no_capabilities(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute'})
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: none',
                               fltr.fail)

    def test_nothing_requested_nothing_found(self):
        fltr = _scheduler.CapabilitiesFilter({})
        node = mock.Mock(properties={}, spec=['properties', 'name', 'id'])
        self.assertTrue(fltr(node))

    def test_matching_node(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute',
                                              'foo': 'bar'})
        node = mock.Mock(
            properties={'capabilities': 'foo:bar,profile:compute,answer:42'},
            spec=['properties', 'name', 'id'])
        self.assertTrue(fltr(node))

    def test_not_matching_node(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute',
                                              'foo': 'bar'})
        node = mock.Mock(
            properties={'capabilities': 'foo:bar,answer:42'},
            spec=['properties', 'name', 'id'])
        self.assertFalse(fltr(node))

    def test_fail_message(self):
        fltr = _scheduler.CapabilitiesFilter({'profile': 'compute'})
        node = mock.Mock(
            properties={'capabilities': 'profile:control'},
            spec=['properties', 'name', 'id'])
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
                             spec=['properties', 'name', 'id'])
            self.assertFalse(fltr(node))
        self.assertRaisesRegex(exceptions.CapabilitiesNotFound,
                               'No available nodes found with capabilities '
                               'profile=compute, existing capabilities: none',
                               fltr.fail)
