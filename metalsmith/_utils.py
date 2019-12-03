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
import logging
import re
import sys

from openstack import exceptions as os_exc

from metalsmith import exceptions


LOG = logging.getLogger(__name__)


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
            LOG.debug('No local_gb for node %s and no root partition size '
                      'specified', log_res(node))
            return
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
    if not isinstance(hostname, str) or len(hostname) > 255:
        return False

    return _HOSTNAME_RE.match(hostname) is not None


def check_hostname(hostname):
    """Check the provided host name.

    :raises: ValueError on inappropriate value of ``hostname``
    """
    if hostname is not None and not is_hostname_safe(hostname):
        raise ValueError("%s cannot be used as a hostname" % hostname)


def parse_checksums(checksums):
    """Parse standard checksums file."""
    result = {}
    for line in checksums.split('\n'):
        if not line.strip():
            continue

        checksum, fname = line.strip().split(None, 1)
        result[fname.strip().lstrip('*')] = checksum.strip()

    return result


def default_hostname(node):
    if node.name and is_hostname_safe(node.name):
        return node.name
    else:
        return node.id


def hostname_for(node, allocation=None):
    if allocation is not None and allocation.name:
        return allocation.name
    else:
        return default_hostname(node)


@contextlib.contextmanager
def reraise_os_exc(reraise_as, failure_message='Clean up failed'):
    exc_info = sys.exc_info()
    is_expected = isinstance(exc_info[1], os_exc.SDKException)

    try:
        yield is_expected
    except Exception:
        LOG.exception(failure_message)

    if is_expected:
        raise reraise_as(str(exc_info[1]))
    else:
        raise exc_info[1]
