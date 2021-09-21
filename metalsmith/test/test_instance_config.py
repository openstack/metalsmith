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
import unittest
from unittest import mock

from metalsmith import instance_config


class TestGenericConfig(unittest.TestCase):
    CLASS = instance_config.GenericConfig

    def setUp(self):
        super(TestGenericConfig, self).setUp()
        self.node = mock.Mock(id='1234')
        self.node.name = 'node name'

    def _check(self, config, expected_metadata, expected_userdata=None,
               cloud_init=True, hostname=None, network_data=None,
               expected_network_data=None):
        expected_m = {'public_keys': {},
                      'uuid': self.node.id,
                      'name': self.node.name,
                      'hostname': self.node.id,
                      'launch_index': 0,
                      'availability_zone': '',
                      'files': [],
                      'meta': {}}
        expected_m.update(expected_metadata)

        result = config.generate(self.node, hostname, network_data)
        self.assertEqual(expected_m, result['meta_data'])

        user_data = result['user_data']
        if expected_userdata:
            self.assertIsNotNone(user_data)
            if cloud_init:
                header, user_data = user_data.split('\n', 1)
                self.assertEqual('#cloud-config', header)
            user_data = json.loads(user_data)
        self.assertEqual(expected_userdata, user_data)

        network_data = result.get('network_data')
        if expected_network_data:
            self.assertIsNotNone(network_data)
            self.assertEqual(expected_network_data, network_data)

    def test_default(self):
        config = self.CLASS()
        self._check(config, {})

    def test_name_as_hostname(self):
        self.node.name = 'example.com'
        config = self.CLASS()
        self._check(config, {'hostname': 'example.com'})

    def test_explicit_hostname(self):
        config = self.CLASS()
        self._check(config, {'hostname': 'example.com'},
                    hostname='example.com')

    def test_ssh_keys(self):
        config = self.CLASS(ssh_keys=['abc', 'def'])
        self._check(config, {'public_keys': {'0': 'abc', '1': 'def'}})

    def test_ssh_keys_as_dict(self):
        config = self.CLASS(ssh_keys={'default': 'abc'})
        self._check(config, {'public_keys': {'default': 'abc'}})

    def test_custom_user_data(self):
        config = self.CLASS(user_data='{"answer": 42}')
        self._check(config, {}, {"answer": 42}, cloud_init=False)

    def test_custom_metadata(self):
        config = self.CLASS(meta_data={"foo": "bar"})
        self._check(config, {"foo": "bar"}, cloud_init=False)

    def test_custom_metadata_not_dict(self):
        self.assertRaises(TypeError, self.CLASS, meta_data="foobar")

    def test_custom_network_data(self):
        config = self.CLASS()
        data = {'net': 'data'}
        self._check(config, {}, network_data=data, expected_network_data=data)


class TestCloudInitConfig(TestGenericConfig):
    CLASS = instance_config.CloudInitConfig

    def test_add_user(self):
        config = self.CLASS()
        config.add_user('admin')
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel']}]})

    def test_add_user_admin(self):
        config = self.CLASS()
        config.add_user('admin', admin=False)
        self._check(config, {},
                    {'users': [{'name': 'admin'}]})

    def test_add_user_sudo(self):
        config = self.CLASS()
        config.add_user('admin', sudo=True)
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'sudo': 'ALL=(ALL) NOPASSWD:ALL'}]})

    def test_add_user_passwd(self):
        config = self.CLASS()
        config.add_user('admin', password_hash='123')
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'passwd': '123'}]})

    def test_add_user_with_keys(self):
        config = self.CLASS(ssh_keys=['abc', 'def'])
        config.add_user('admin')
        self._check(config, {'public_keys': {'0': 'abc', '1': 'def'}},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel'],
                                'ssh_authorized_keys': ['abc', 'def']}]})

    # Overriding tests since CloudInitConfig does not support plain strings
    # for user_data, only dictionaries.
    def test_custom_user_data(self):
        config = self.CLASS(user_data={'answer': 42})
        self._check(config, {}, {'answer': 42})

    def test_custom_user_data_with_users(self):
        config = self.CLASS(user_data={'answer': 42})
        config.add_user('admin')
        self._check(config, {},
                    {'users': [{'name': 'admin',
                                'groups': ['wheel']}],
                     'answer': 42})

    def test_user_data_not_dict(self):
        self.assertRaises(TypeError, self.CLASS, user_data="string")
        config = self.CLASS()
        config.user_data = "string"
        self.assertRaises(TypeError, config.populate_user_data)
