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

import json
import logging
import warnings

from openstack.baremetal import configdrive

from metalsmith import _utils


LOG = logging.getLogger(__name__)


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

    def generate(self, node):
        """Generate the config drive information.

        :param node: `Node` object.
        :return: configdrive contents as a dictionary with keys:

            ``meta_data``
                meta data dictionary
            ``user_data``
                user data as a string
        """
        hostname = node.instance_info.get(_utils.HOSTNAME_FIELD)

        # NOTE(dtantsur): CirrOS does not understand lists
        if isinstance(self.ssh_keys, list):
            ssh_keys = {str(i): v for i, v in enumerate(self.ssh_keys)}
        else:
            ssh_keys = self.ssh_keys

        metadata = {'public_keys': ssh_keys,
                    'uuid': node.id,
                    'name': node.name,
                    'hostname': hostname,
                    'launch_index': 0,
                    'availability_zone': '',
                    'files': [],
                    'meta': {}}
        user_data = {}
        user_data_str = None

        if self.users:
            user_data['users'] = self.users

        if user_data:
            user_data_str = "#cloud-config\n" + json.dumps(user_data)

        return {'meta_data': metadata,
                'user_data': user_data_str}

    def build_configdrive(self, node):
        """Make the config drive ISO.

        Deprecated, use :py:meth:`generate` with openstacksdk's
        ``openstack.baremetal.configdrive.build`` instead.

        :param node: `Node` object.
        :return: configdrive contents as a base64-encoded string.
        """
        warnings.warn("build_configdrive is deprecated, use generate with "
                      "openstacksdk's openstack.baremetal.configdrive.build "
                      "instead", DeprecationWarning)
        cd = self.generate(node)
        metadata = cd.pop('meta_data')
        user_data = cd.pop('user_data')
        if user_data:
            user_data = user_data.encode('utf-8')

        LOG.debug('Generating configdrive tree for node %(node)s with '
                  'metadata %(meta)s', {'node': _utils.log_res(node),
                                        'meta': metadata})
        return configdrive.build(metadata, user_data=user_data, **cd)
