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
import os

import mock
import testtools

from metalsmith import _config


class TestInstanceConfig(testtools.TestCase):
    def setUp(self):
        super(TestInstanceConfig, self).setUp()
        self.node = mock.Mock(uuid='1234')
        self.node.name = 'node name'

    def _check(self, config, expected_metadata, expected_userdata=None):
        expected_m = {'public_keys': [],
                      'uuid': '1234',
                      'name': 'node name',
                      'hostname': 'example.com',
                      'launch_index': 0,
                      'availability_zone': '',
                      'files': [],
                      'meta': {}}
        expected_m.update(expected_metadata)

        with config.build_configdrive_directory(self.node, 'example.com') as d:
            for version in ('2012-08-10', 'latest'):
                with open(os.path.join(d, 'openstack', version,
                                       'meta_data.json')) as fp:
                    metadata = json.load(fp)

                self.assertEqual(expected_m, metadata)
                user_data = os.path.join(d, 'openstack', version, 'user_data')
                if expected_userdata is None:
                    self.assertFalse(os.path.exists(user_data))
                else:
                    with open(user_data) as fp:
                        lines = list(fp)
                    self.assertEqual('#cloud-config\n', lines[0])
                    user_data = json.loads(''.join(lines[1:]))
                    self.assertEqual(expected_userdata, user_data)

        self.assertFalse(os.path.exists(d))

    def test_default(self):
        config = _config.InstanceConfig()
        self._check(config, {})

    def test_ssh_keys(self):
        config = _config.InstanceConfig(ssh_keys=['abc', 'def'])
        self._check(config, {'public_keys': ['abc', 'def']})

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
        self._check(config, {'public_keys': ['abc', 'def']},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'ssh_authorized_keys': ['abc', 'def']}]})