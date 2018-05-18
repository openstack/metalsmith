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

import six

from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class Filter(object):
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


@six.add_metaclass(abc.ABCMeta)
class Reserver(object):
    """Base class for reservers."""

    @abc.abstractmethod
    def __call__(self, node):
        """Reserve this node.

        :param node: Node object.
        :return: updated Node object if it was reserved
        :raises: any Exception to indicate that the next node should be tried
        """

    @abc.abstractmethod
    def fail(self):
        """Fail reservation because no nodes are left.

        Must raise an exception.
        """


def schedule_node(nodes, filters, reserver, dry_run=False):
    """Schedule one node.

    :param nodes: List of input nodes.
    :param filters: List of callable Filter objects to filter/validate nodes.
        They are called in passes. If a pass yields no nodes, an error is
        raised.
    :param reserver: A callable Reserver object. Must return the updated node
        or raise an exception.
    :param dry_run: If True, reserver is not actually called.
    :return: The resulting node
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

    if dry_run:
        LOG.debug('Dry run, not reserving any nodes')
        return nodes[0]

    for node in nodes:
        try:
            result = reserver(node)
        except Exception as exc:
            LOG.debug('Node %(node)s was not reserved (%(exc)s), moving on '
                      'to the next one',
                      {'node': _utils.log_node(node), 'exc': exc})
        else:
            LOG.info('Node %s reserved for deployment',
                     _utils.log_node(result))
            return result

    LOG.debug('No nodes could be reserved')
    reserver.fail()
    assert False, "BUG: %s.fail did not raise" % reserver.__class__.__name__


class CapabilitiesFilter(Filter):
    """Filter that checks capabilities."""

    def __init__(self, resource_class, capabilities):
        self._resource_class = resource_class
        self._capabilities = capabilities
        self._counter = collections.Counter()

    def __call__(self, node):
        try:
            caps = _utils.get_capabilities(node)
        except Exception:
            LOG.exception('Malformed capabilities on node %(node)s: %(caps)s',
                          {'node': _utils.log_node(node),
                           'caps': node.properties.get('capabilities')})
            return False

        LOG.debug('Capabilities for node %(node)s: %(caps)s',
                  {'node': _utils.log_node(node), 'caps': caps})
        for key, value in self._capabilities.items():
            try:
                node_value = caps[key]
            except KeyError:
                LOG.debug('Node %(node)s does not have capability %(cap)s',
                          {'node': _utils.log_node(node), 'cap': key})
                return False
            else:
                self._counter["%s=%s" % (key, node_value)] += 1
                if value != node_value:
                    LOG.debug('Node %(node)s has capability %(cap)s of '
                              'value "%(node_val)s" instead of "%(expected)s"',
                              {'node': _utils.log_node(node), 'cap': key,
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
        raise exceptions.CapabilitiesNotFound(message,
                                              self._resource_class,
                                              self._capabilities)


class ValidationFilter(Filter):
    """Filter that runs validation on nodes."""

    def __init__(self, api, resource_class, capabilities):
        self._api = api
        # These are only used for better exceptions
        self._resource_class = resource_class
        self._capabilities = capabilities
        self._failed_validation = []

    def __call__(self, node):
        try:
            self._api.validate_node(node.uuid)
        except RuntimeError as exc:
            message = ('Node %(node)s failed validation: %(err)s' %
                       {'node': _utils.log_node(node), 'err': exc})
            LOG.warning(message)
            self._failed_validation.append(message)
            return False

        return True

    def fail(self):
        errors = ", ".join(self._failed_validation)
        message = "All available nodes have failed validation: %s" % errors
        raise exceptions.ValidationFailed(message,
                                          self._resource_class,
                                          self._capabilities)


class IronicReserver(Reserver):

    def __init__(self, api, resource_class, capabilities):
        self._api = api
        # These are only used for better exceptions
        self._resource_class = resource_class
        self._capabilities = capabilities

    def __call__(self, node):
        result = self._api.reserve_node(node, instance_uuid=node.uuid)

        # Try validation again to be sure nothing has changed
        validator = ValidationFilter(self._api, self._resource_class,
                                     self._capabilities)
        if not validator(result):
            LOG.warning('Validation of node %s failed after reservation',
                        _utils.log_node(node))
            try:
                self._api.release_node(node)
            except Exception:
                LOG.exception('Failed to release the reserved node %s',
                              _utils.log_node(node))
            validator.fail()

        return result

    def fail(self):
        raise exceptions.AllNodesReserved(self._resource_class,
                                          self._capabilities)
