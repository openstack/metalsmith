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
    :ivar users: Users to add on first boot.
    """

    def __init__(self, ssh_keys=None):
        self.ssh_keys = ssh_keys or []
        self.users = []

    def add_user(self, name, admin=True, password_hash=None, sudo=False,
                 **kwargs):
        """Add a user to be created on first boot.

        :param name: user name.
        :param admin: whether to add the user to the admin group (wheel).
        :param password_hash: user password hash, if password authentication
            is expected.
        :param sudo: whether to allow the user sudo without password.
        :param kwargs: other arguments to pass.
        """
        kwargs['name'] = name
        if admin:
            kwargs.setdefault('groups', []).append('wheel')
        if password_hash:
            kwargs['passwd'] = password_hash
        if sudo:
            kwargs['sudo'] = 'ALL=(ALL) NOPASSWD:ALL'
        if self.ssh_keys:
            kwargs.setdefault('ssh_authorized_keys', self.ssh_keys)
        self.users.append(kwargs)

    @contextlib.contextmanager
    def build_configdrive_directory(self, node, hostname):
        """Build a configdrive from the provided information.

        :param node: `Node` object.
        :param hostname: instance hostname.
        :return: a context manager yielding a directory with files
        """
        # NOTE(dtantsur): CirrOS does not understand lists
        if isinstance(self.ssh_keys, list):
            ssh_keys = {str(i): v for i, v in enumerate(self.ssh_keys)}
        else:
            ssh_keys = self.ssh_keys

        d = tempfile.mkdtemp()
        try:
            metadata = {'public_keys': ssh_keys,
                        'uuid': node.uuid,
                        'name': node.name,
                        'hostname': hostname,
                        'launch_index': 0,
                        'availability_zone': '',
                        'files': [],
                        'meta': {}}
            user_data = {}
            if self.users:
                user_data['users'] = self.users

            for version in ('2012-08-10', 'latest'):
                subdir = os.path.join(d, 'openstack', version)
                if not os.path.exists(subdir):
                    os.makedirs(subdir)

                with open(os.path.join(subdir, 'meta_data.json'), 'w') as fp:
                    json.dump(metadata, fp)

                if user_data:
                    with open(os.path.join(subdir, 'user_data'), 'w') as fp:
                        fp.write("#cloud-config\n")
                        json.dump(user_data, fp)

            yield d
        finally:
            shutil.rmtree(d)
