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

import abc
import collections
import logging

from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)


class Filter(object, metaclass=abc.ABCMeta):
    """Base class for filters."""

    @abc.abstractmethod
    def __call__(self, node):
        """Validate this node.

        :param node: Node object.
        :return: True/False
        """

    @abc.abstractmethod
    def fail(self):
        """Fail scheduling because no nodes are left.

        Must raise an exception.
        """


def run_filters(filters, nodes):
    """Filter the node list by provided filters.

    :param filters: List of callable Filter objects to filter/validate nodes.
        They are called in passes. If a pass yields no nodes, an error is
        raised.
    :param nodes: List of input nodes.
    :return: The resulting nodes
    """
    for f in filters:
        f_name = f.__class__.__name__
        LOG.debug('Running filter %(filter)s on %(count)d node(s)',
                  {'filter': f_name, 'count': len(nodes)})

        nodes = list(filter(f, nodes))
        if not nodes:
            LOG.debug('Filter %s yielded no nodes', f_name)
            f.fail()
            assert False, "BUG: %s.fail did not raise" % f_name

        LOG.debug('Filter %(filter)s yielded %(count)d node(s)',
                  {'filter': f_name, 'count': len(nodes)})
    return nodes


class NodeTypeFilter(Filter):
    """Filter that checks resource class and conductor group."""

    def __init__(self, resource_class=None, conductor_group=None):
        self.resource_class = resource_class
        self.conductor_group = conductor_group

    def __call__(self, node):
        if node.instance_id:
            LOG.debug('Node %s is already reserved', _utils.log_res(node))
            return False

        if node.is_maintenance:
            LOG.debug('Node %s is in maintenance', _utils.log_res(node))
            return False

        if (self.resource_class is not None
                and node.resource_class != self.resource_class):
            LOG.debug('Resource class %(real)s does not match the expected '
                      'value of %(exp)s for node %(node)s',
                      {'node': _utils.log_res(node),
                       'exp': self.resource_class,
                       'real': node.resource_class})
            return False

        if (self.conductor_group is not None
                and node.conductor_group != self.conductor_group):
            LOG.debug('Conductor group %(real)s does not match the expected '
                      'value of %(exp)s for node %(node)s',
                      {'node': _utils.log_res(node),
                       'exp': self.conductor_group,
                       'real': node.conductor_group})
            return False

        return True

    def fail(self):
        raise exceptions.NodesNotFound(self.resource_class,
                                       self.conductor_group)


class CapabilitiesFilter(Filter):
    """Filter that checks capabilities."""

    def __init__(self, capabilities):
        self._capabilities = capabilities
        self._counter = collections.Counter()

    def __call__(self, node):
        if not self._capabilities:
            return True

        try:
            caps = _utils.get_capabilities(node)
        except Exception:
            LOG.exception('Malformed capabilities on node %(node)s: %(caps)s',
                          {'node': _utils.log_res(node),
                           'caps': node.properties.get('capabilities')})
            return False

        LOG.debug('Capabilities for node %(node)s: %(caps)s',
                  {'node': _utils.log_res(node), 'caps': caps})
        for key, value in self._capabilities.items():
            try:
                node_value = caps[key]
            except KeyError:
                LOG.debug('Node %(node)s does not have capability %(cap)s',
                          {'node': _utils.log_res(node), 'cap': key})
                return False
            else:
                self._counter["%s=%s" % (key, node_value)] += 1
                if value != node_value:
                    LOG.debug('Node %(node)s has capability %(cap)s of '
                              'value "%(node_val)s" instead of "%(expected)s"',
                              {'node': _utils.log_res(node), 'cap': key,
                               'node_val': node_value, 'expected': value})
                    return False

        return True

    def fail(self):
        existing = ", ".join("%s (%d node(s))" % item
                             for item in self._counter.items())
        requested = ', '.join("%s=%s" % item
                              for item in self._capabilities.items())
        message = ("No available nodes found with capabilities %(req)s, "
                   "existing capabilities: %(exist)s" %
                   {'req': requested, 'exist': existing or 'none'})
        raise exceptions.CapabilitiesNotFound(message, self._capabilities)


class CustomPredicateFilter(Filter):

    def __init__(self, predicate):
        self.predicate = predicate
        self._failed_nodes = []

    def __call__(self, node):
        if not self.predicate(node):
            self._failed_nodes.append(node)
            return False

        return True

    def fail(self):
        message = 'No nodes satisfied the custom predicate %s' % self.predicate
        raise exceptions.CustomPredicateFailed(message, self._failed_nodes)
