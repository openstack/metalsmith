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

import contextlib
import re

from openstack import exceptions as sdk_exc
import six

from metalsmith import exceptions


def log_res(res):
    if res is None:
        return None
    elif getattr(res, 'name', None):
        return '%s (UUID %s)' % (res.name, res.id)
    else:
        return res.id


def get_capabilities(node):
    caps = node.properties.get('capabilities') or {}
    if not isinstance(caps, dict):
        caps = dict(x.split(':', 1) for x in caps.split(',') if x)
    return caps


def get_root_disk(root_size_gb, node):
    """Validate and calculate the root disk size."""
    if root_size_gb is not None:
        if not isinstance(root_size_gb, int):
            raise TypeError("The root_size_gb argument must be "
                            "a positive integer, got %r" % root_size_gb)
        elif root_size_gb <= 0:
            raise ValueError("The root_size_gb argument must be "
                             "a positive integer, got %d" % root_size_gb)
    else:
        try:
            assert int(node.properties['local_gb']) > 0
        except KeyError:
            raise exceptions.UnknownRootDiskSize(
                'No local_gb for node %s and no root partition size '
                'specified' % log_res(node))
        except (TypeError, ValueError, AssertionError):
            raise exceptions.UnknownRootDiskSize(
                'The local_gb for node %(node)s is invalid: '
                'expected positive integer, got %(value)s' %
                {'node': log_res(node),
                 'value': node.properties['local_gb']})

        # allow for partitioning and config drive
        root_size_gb = int(node.properties['local_gb']) - 1

    return root_size_gb


_HOSTNAME_RE = re.compile(r"""^
[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]      # host
(\.[a-z0-9][a-z0-9\-]{0,61}[a-z0-9])* # domain
$""", re.IGNORECASE | re.VERBOSE)


def is_hostname_safe(hostname):
    """Check for valid host name.

    Nominally, checks that the supplied hostname conforms to:
        * http://en.wikipedia.org/wiki/Hostname
        * http://tools.ietf.org/html/rfc952
        * http://tools.ietf.org/html/rfc1123

    :param hostname: The hostname to be validated.
    :returns: True if valid. False if not.
    """
    if not isinstance(hostname, six.string_types) or len(hostname) > 255:
        return False

    return _HOSTNAME_RE.match(hostname) is not None


def parse_checksums(checksums):
    """Parse standard checksums file."""
    result = {}
    for line in checksums.split('\n'):
        if not line.strip():
            continue

        checksum, fname = line.strip().split(None, 1)
        result[fname.strip().lstrip('*')] = checksum.strip()

    return result


# NOTE(dtantsur): make this private since it will no longer be possible with
# transition to allocation API.
class DuplicateHostname(sdk_exc.SDKException, exceptions.Error):
    pass


HOSTNAME_FIELD = 'metalsmith_hostname'


def default_hostname(node):
    if node.name and is_hostname_safe(node.name):
        return node.name
    else:
        return node.id


class GetNodeMixin(object):
    """A helper mixin for getting nodes with hostnames."""

    _node_list = None

    def _available_nodes(self):
        return self.connection.baremetal.nodes(details=True,
                                               associated=False,
                                               provision_state='available',
                                               is_maintenance=False)

    def _nodes_for_lookup(self):
        return self.connection.baremetal.nodes(
            fields=['uuid', 'name', 'instance_info'])

    def _find_node_by_hostname(self, hostname):
        """A helper to find a node by metalsmith hostname."""
        nodes = self._node_list or self._nodes_for_lookup()
        existing = [n for n in nodes
                    if n.instance_info.get(HOSTNAME_FIELD) == hostname]
        if len(existing) > 1:
            raise DuplicateHostname(
                "More than one node found with hostname %(host)s: %(nodes)s" %
                {'host': hostname,
                 'nodes': ', '.join(log_res(n) for n in existing)})
        elif not existing:
            return None
        else:
            # Fetch the complete node information before returning
            return self.connection.baremetal.get_node(existing[0].id)

    def _get_node(self, node, refresh=False, accept_hostname=False):
        """A helper to find and return a node."""
        if isinstance(node, six.string_types):
            if accept_hostname and is_hostname_safe(node):
                by_hostname = self._find_node_by_hostname(node)
                if by_hostname is not None:
                    return by_hostname

            return self.connection.baremetal.get_node(node)
        elif hasattr(node, 'node'):
            # Instance object
            node = node.node
        else:
            node = node

        if refresh:
            return self.connection.baremetal.get_node(node.id)
        else:
            return node

    @contextlib.contextmanager
    def _cache_node_list_for_lookup(self):
        if self._node_list is None:
            self._node_list = list(self._nodes_for_lookup())
        yield self._node_list
        self._node_list = None
