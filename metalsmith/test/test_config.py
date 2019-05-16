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

import mock
from openstack.baremetal import configdrive
import testtools

from metalsmith import _config
from metalsmith import _utils


class TestInstanceConfig(testtools.TestCase):
    def setUp(self):
        super(TestInstanceConfig, self).setUp()
        self.node = mock.Mock(id='1234')
        self.node.name = 'node name'

    def _check(self, config, expected_metadata, expected_userdata=None):
        expected_m = {'public_keys': {},
                      'uuid': '1234',
                      'name': 'node name',
                      'hostname': 'example.com',
                      'launch_index': 0,
                      'availability_zone': '',
                      'files': [],
                      'meta': {}}
        expected_m.update(expected_metadata)
        self.node.instance_info = {_utils.HOSTNAME_FIELD:
                                   expected_m.get('hostname')}

        with mock.patch.object(configdrive, 'build', autospec=True) as mb:
            result = config.build_configdrive(self.node)
            mb.assert_called_once_with(expected_m, mock.ANY)
            self.assertIs(result, mb.return_value)
            user_data = mb.call_args[1].get('user_data')

        if expected_userdata:
            self.assertIsNotNone(user_data)
            user_data = user_data.decode('utf-8')
            header, user_data = user_data.split('\n', 1)
            self.assertEqual('#cloud-config', header)
            user_data = json.loads(user_data)
        self.assertEqual(expected_userdata, user_data)

    def test_default(self):
        config = _config.InstanceConfig()
        self._check(config, {})

    def test_ssh_keys(self):
        config = _config.InstanceConfig(ssh_keys=['abc', 'def'])
        self._check(config, {'public_keys': {'0': 'abc', '1': 'def'}})

    def test_ssh_keys_as_dict(self):
        config = _config.InstanceConfig(ssh_keys={'default': 'abc'})
        self._check(config, {'public_keys': {'default': 'abc'}})

    def test_add_user(self):
        config = _config.InstanceConfig()
        config.add_user('admin')
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel']}]})

    def test_add_user_admin(self):
        config = _config.InstanceConfig()
        config.add_user('admin', admin=False)
        self._check(config, {},
                    {'users': [{'name': 'admin'}]})

    def test_add_user_sudo(self):
        config = _config.InstanceConfig()
        config.add_user('admin', sudo=True)
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'sudo': 'ALL=(ALL) NOPASSWD:ALL'}]})

    def test_add_user_passwd(self):
        config = _config.InstanceConfig()
        config.add_user('admin', password_hash='123')
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'passwd': '123'}]})

    def test_add_user_with_keys(self):
        config = _config.InstanceConfig(ssh_keys=['abc', 'def'])
        config.add_user('admin')
        self._check(config, {'public_keys': {'0': 'abc', '1': 'def'}},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'ssh_authorized_keys': ['abc', 'def']}]})
