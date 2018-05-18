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
import json
import os
import shutil
import tempfile

from metalsmith import exceptions


def log_node(node):
    if node.name:
        return '%s (UUID %s)' % (node.name, node.uuid)
    else:
        return node.uuid


def get_capabilities(node):
    caps = node.properties.get('capabilities') or {}
    if not isinstance(caps, dict):
        caps = dict(x.split(':', 1) for x in caps.split(',') if x)
    return caps


@contextlib.contextmanager
def config_drive_dir(node, ssh_keys):
    d = tempfile.mkdtemp()
    try:
        metadata = {'public_keys': ssh_keys,
                    'uuid': node.uuid,
                    'name': node.name,
                    'hostname': node.name or node.uuid,
                    'launch_index': 0,
                    'availability_zone': '',
                    'files': [],
                    'meta': {}}
        for version in ('2012-08-10', 'latest'):
            subdir = os.path.join(d, 'openstack', version)
            if not os.path.exists(subdir):
                os.makedirs(subdir)

            with open(os.path.join(subdir, 'meta_data.json'), 'w') as fp:
                json.dump(metadata, fp)

        yield d
    finally:
        shutil.rmtree(d)


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
