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


class InstanceConfig(object):
    """Configuration of the target instance.

    The information attached to this object will be passed via a configdrive
    to the instance's first boot script (e.g. cloud-init).

    :ivar ssh_keys: List of SSH public keys.
    """

    def __init__(self, ssh_keys=None):
        self.ssh_keys = ssh_keys or []

    @contextlib.contextmanager
    def build_configdrive_directory(self, node, hostname):
        """Build a configdrive from the provided information.

        :param node: `Node` object.
        :param hostname: instance hostname.
        :return: a context manager yielding a directory with files
        """
        d = tempfile.mkdtemp()
        try:
            metadata = {'public_keys': self.ssh_keys,
                        'uuid': node.uuid,
                        'name': node.name,
                        'hostname': hostname,
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
