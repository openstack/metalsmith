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

import collections
import re

import six

from metalsmith import exceptions


def log_node(node):
    if node.name:
        return '%s (UUID %s)' % (node.name, node.uuid)
    else:
        return node.uuid


def log_res(res):
    if getattr(res, 'name', None):
        return '%s (UUID %s)' % (res.name, res.id)
    else:
        return res.id


def get_capabilities(node):
    caps = node.properties.get('capabilities') or {}
    if not isinstance(caps, dict):
        caps = dict(x.split(':', 1) for x in caps.split(',') if x)
    return caps


def get_root_disk(root_disk_size, node):
    """Validate and calculate the root disk size."""
    if root_disk_size is not None:
        if not isinstance(root_disk_size, int):
            raise TypeError("The root_disk_size argument must be "
                            "a positive integer, got %r" % root_disk_size)
        elif root_disk_size <= 0:
            raise ValueError("The root_disk_size argument must be "
                             "a positive integer, got %d" % root_disk_size)
    else:
        try:
            assert int(node.properties['local_gb']) > 0
        except KeyError:
            raise exceptions.UnknownRootDiskSize(
                'No local_gb for node %s and no root disk size requested' %
                log_node(node))
        except (TypeError, ValueError, AssertionError):
            raise exceptions.UnknownRootDiskSize(
                'The local_gb for node %(node)s is invalid: '
                'expected positive integer, got %(value)s' %
                {'node': log_node(node),
                 'value': node.properties['local_gb']})

        # allow for partitioning and config drive
        root_disk_size = int(node.properties['local_gb']) - 1

    return root_disk_size


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


def validate_nics(nics):
    """Validate NICs."""
    if not isinstance(nics, collections.Sequence):
        raise TypeError("NICs must be a list of dicts")

    unknown_nic_types = set()
    for nic in nics:
        if not isinstance(nic, collections.Mapping) or len(nic) != 1:
            raise TypeError("Each NIC must be a dict with one item, "
                            "got %s" % nic)

        nic_type = next(iter(nic))
        if nic_type not in ('port', 'network'):
            unknown_nic_types.add(nic_type)

    if unknown_nic_types:
        raise ValueError("Unexpected NIC type(s) %s, supported values are "
                         "'port' and 'network'" % ', '.join(unknown_nic_types))
