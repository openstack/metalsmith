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

import copy
import json
import logging

from metalsmith import _utils


LOG = logging.getLogger(__name__)


class GenericConfig(object):
    """Configuration of the target instance.

    The information attached to this object will be passed via a configdrive
    to the instance's first boot script (e.g. cloud-init).

    This class represents generic configuration compatible with most first-boot
    implementations. Use :py:class:`CloudInitConfig` for features specific to
    `cloud-init <https://cloudinit.readthedocs.io/>`_.

    :ivar ssh_keys: List of SSH public keys.
    :ivar user_data: User data as a string.
    :ivar meta_data: Dict of data to add to the generated ``meta_data``
    """

    def __init__(self, ssh_keys=None, user_data=None, meta_data=None):
        self.ssh_keys = ssh_keys or []
        self.user_data = user_data
        if meta_data and not isinstance(meta_data, dict):
            raise TypeError('Custom meta_data must be a dictionary, '
                            'got %r' % meta_data)
        self.meta_data = meta_data or {}

    def generate(self, node, hostname=None, network_data=None):
        """Generate the config drive information.

        :param node: `Node` object.
        :param hostname: Desired hostname (defaults to node's name or ID).
        :param network_data: Network metadata as dictionary
        :return: configdrive contents as a dictionary with keys:

            ``meta_data``
                meta data dictionary
            ``network_data``
                network data as dictionary
            ``user_data``
                user data as a string
        """
        if not hostname:
            hostname = _utils.default_hostname(node)

        # NOTE(dtantsur): CirrOS does not understand lists
        if isinstance(self.ssh_keys, list):
            ssh_keys = {str(i): v for i, v in enumerate(self.ssh_keys)}
        else:
            ssh_keys = self.ssh_keys

        meta_data = self.meta_data.copy()
        meta_data.update({
            'public_keys': ssh_keys,
            'uuid': node.id,
            'name': node.name,
            'hostname': hostname
        })
        meta_data.setdefault('launch_index', 0)
        meta_data.setdefault('availability_zone', '')
        meta_data.setdefault('files', [])
        meta_data.setdefault('meta', {})

        user_data = self.populate_user_data()

        data = {'meta_data': meta_data, 'user_data': user_data}

        if network_data:
            data['network_data'] = network_data

        return data

    def populate_user_data(self):
        """Get user data for this configuration.

        Can be overridden to provide additional features.

        :return: user data as a string.
        """
        return self.user_data


class CloudInitConfig(GenericConfig):
    """Configuration of the target instance using cloud-init.

    Compared to :class:`GenericConfig`, this adds support for managing users.

    :ivar ssh_keys: List of SSH public keys.
    :ivar user_data: Cloud-init cloud-config data as a dictionary.
    :ivar meta_data: Dict of data to add to the generated ``meta_data``
    """

    def __init__(self, ssh_keys=None, user_data=None, meta_data=None):
        if user_data is not None and not isinstance(user_data, dict):
            raise TypeError('Custom user data must be a dictionary for '
                            'CloudInitConfig, got %r' % user_data)
        super(CloudInitConfig, self).__init__(ssh_keys, user_data or {},
                                              meta_data=meta_data)
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

    def populate_user_data(self):
        """Get user data for this configuration.

        Takes the custom user data and appends requested users to it.

        :return: user data as a string.
        """
        if not isinstance(self.user_data, dict):
            raise TypeError('Custom user data must be a dictionary for '
                            'CloudInitConfig, got %r' % self.user_data)

        if self.users:
            user_data = copy.deepcopy(self.user_data)
            user_data.setdefault('users', []).extend(self.users)
        else:
            user_data = self.user_data

        if user_data:
            return "#cloud-config\n" + json.dumps(user_data)
