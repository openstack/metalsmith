# Copyright 2019 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import unittest
from unittest import mock

from metalsmith_ansible.ansible_plugins.modules \
    import metalsmith_instances as mi
from openstack import exceptions as os_exc

from metalsmith import exceptions as exc


class TestMetalsmithInstances(unittest.TestCase):

    @mock.patch('metalsmith.sources.detect', autospec=True)
    def test_get_source(self, mock_detect):
        mi._get_source({
            'image': {'href': 'overcloud-full'}
        })
        mi._get_source({
            'image': {
                'href': 'file://overcloud-full.qcow2',
                'checksum': 'asdf',
                'kernel': 'file://overcloud-full.vmlinuz',
                'ramdisk': 'file://overcloud-full.initrd'
            }
        })
        mock_detect.assert_has_calls([
            mock.call(
                image='overcloud-full',
                checksum=None,
                kernel=None,
                ramdisk=None
            ),
            mock.call(
                image='file://overcloud-full.qcow2',
                checksum='asdf',
                kernel='file://overcloud-full.vmlinuz',
                ramdisk='file://overcloud-full.initrd'
            )
        ])

    def test_reserve(self):
        provisioner = mock.Mock()
        instances = [{
            'hostname': 'node-1',
            'name': 'node',
            'candidates': ['other_node'],
            'resource_class': 'boxen',
            'capabilities': {'foo': 'bar'},
            'traits': ['this', 'that'],
            'conductor_group': 'group'
        }, {}]
        reserved = [
            mock.Mock(id=1),
            mock.Mock(id=2),
        ]

        # test reserve success
        provisioner.reserve_node.side_effect = reserved

        result = mi.reserve(provisioner, instances, True)
        provisioner.reserve_node.assert_has_calls([
            mock.call(
                hostname='node-1',
                candidates=['other_node', 'node'],
                capabilities={'foo': 'bar'},
                conductor_group='group',
                resource_class='boxen',
                traits=['this', 'that']
            ),
            mock.call(
                hostname=None,
                candidates=None,
                capabilities=None,
                conductor_group=None,
                resource_class='baremetal',
                traits=None
            )
        ])
        self.assertTrue(result[0])
        self.assertEqual(reserved, result[1])

        # test reserve failure with cleanup
        instances = [{}, {}, {}]
        reserved = [
            mock.Mock(id=1),
            mock.Mock(id=2),
            exc.ReservationFailed('ouch')
        ]
        provisioner.reserve_node.side_effect = reserved
        self.assertRaises(exc.ReservationFailed, mi.reserve,
                          provisioner, instances, True)
        provisioner.unprovision_node.assert_has_calls([
            mock.call(1),
            mock.call(2)
        ])

    @mock.patch('metalsmith.sources.detect', autospec=True)
    @mock.patch('metalsmith.instance_config.CloudInitConfig', autospec=True)
    def test_provision(self, mock_config, mock_detect):
        config = mock_config.return_value
        image = mock_detect.return_value

        provisioner = mock.Mock()
        instances = [{
            'name': 'node-1',
            'hostname': 'overcloud-controller-1',
            'image': {'href': 'overcloud-full'}
        }, {
            'name': 'node-2',
            'hostname': 'overcloud-controller-2',
            'image': {'href': 'overcloud-full'},
            'nics': {'network': 'ctlplane'},
            'root_size_gb': 200,
            'swap_size_mb': 16,
            'netboot': True,
            'ssh_public_keys': 'abcd',
            'user_name': 'centos',
            'passwordless_sudo': False,
            'config_drive': {
                'meta_data': {'foo': 'bar'},
                'cloud_config': {'bootcmd': ['echo henlo world']}
            }
        }, {
            'name': 'node-3',
            'hostname': 'overcloud-controller-3',
            'image': {'href': 'overcloud-full'}
        }, {
            'name': 'node-4',
            'hostname': 'overcloud-compute-0',
            'image': {'href': 'overcloud-full'}
        }]
        provisioned = [
            mock.Mock(uuid=1),
            mock.Mock(uuid=2),
            mock.Mock(uuid=3),
            mock.Mock(uuid=4),
        ]

        # test provision success
        provisioner.provision_node.side_effect = provisioned

        # provision 4 nodes with concurrency of 2
        result = mi.provision(provisioner, instances, 3600, 2, True, True)
        provisioner.provision_node.assert_has_calls([
            mock.call(
                'node-1',
                config=config,
                hostname='overcloud-controller-1',
                image=image,
                netboot=False,
                nics=None,
                root_size_gb=None,
                swap_size_mb=None
            ),
            mock.call(
                'node-2',
                config=config,
                hostname='overcloud-controller-2',
                image=image,
                netboot=True,
                nics={'network': 'ctlplane'},
                root_size_gb=200,
                swap_size_mb=16
            ),
            mock.call(
                'node-3',
                config=config,
                hostname='overcloud-controller-3',
                image=image,
                netboot=False,
                nics=None,
                root_size_gb=None,
                swap_size_mb=None
            ),
            mock.call(
                'node-4',
                config=config,
                hostname='overcloud-compute-0',
                image=image,
                netboot=False,
                nics=None,
                root_size_gb=None,
                swap_size_mb=None
            ),
        ], any_order=True)
        mock_config.assert_has_calls([
            mock.call(ssh_keys=None, user_data=None, meta_data=None),
            mock.call(ssh_keys='abcd',
                      user_data={'bootcmd': ['echo henlo world']},
                      meta_data={'foo': 'bar'})
        ])
        config.add_user.assert_called_once_with(
            'centos', admin=True, sudo=False)
        mock_detect.assert_has_calls([
            mock.call(
                image='overcloud-full',
                checksum=None,
                kernel=None,
                ramdisk=None
            ),
            mock.call(
                image='overcloud-full',
                checksum=None,
                kernel=None,
                ramdisk=None
            ),
            mock.call(
                image='overcloud-full',
                checksum=None,
                kernel=None,
                ramdisk=None
            ),
            mock.call(
                image='overcloud-full',
                checksum=None,
                kernel=None,
                ramdisk=None
            ),
        ])
        self.assertTrue(result[0])
        self.assertEqual(provisioned, result[1])

        # test provision failure with cleanup
        instances = [{
            'name': 'node-1',
            'hostname': 'overcloud-controller-1',
            'image': {'href': 'overcloud-full'}
        }, {
            'name': 'node-2',
            'hostname': 'overcloud-controller-2',
            'image': {'href': 'overcloud-full'},
        }, {
            'name': 'node-3',
            'hostname': 'overcloud-controller-3',
            'image': {'href': 'overcloud-full'},
        }]
        provisioned = [
            mock.Mock(uuid=1),
            mock.Mock(uuid=2),
            exc.Error('ouch')
        ]
        provisioner.provision_node.side_effect = provisioned
        self.assertRaises(exc.Error, mi.provision,
                          provisioner, instances, 3600, 20, True, True)
        provisioner.unprovision_node.assert_has_calls([
            mock.call(1),
            mock.call(2)
        ])

    @mock.patch('metalsmith.sources.detect', autospec=True)
    @mock.patch('metalsmith.instance_config.CloudInitConfig', autospec=True)
    def test_unprovision(self, mock_config, mock_detect):

        mock_node1 = mock.Mock(name='node-1')
        mock_node2 = mock.Mock(name='node-2')
        mock_allocation1 = mock.Mock(name='overcloud-controller-1',
                                     node_id='aaaa')
        connection = mock.Mock()
        provisioner = mock.Mock(connection=connection)

        connection.baremetal.get_allocation.side_effect = [
            mock_allocation1, os_exc.ResourceNotFound()]
        connection.baremetal.get_node.side_effect = [
            mock_node1, mock_node2, os_exc.ResourceNotFound()]
        instances = [{
            'name': 'node-1',
            'hostname': 'overcloud-controller-1',
            'image': {'href': 'overcloud-full'},
            'state': 'absent'
        }, {
            'name': 'node-2',
            'image': {'href': 'overcloud-full'},
            'state': 'absent'
        }, {
            'name': 'node-3',
            'hostname': 'overcloud-controller-3',
            'image': {'href': 'overcloud-full'},
            'state': 'absent'
        }]
        self.assertTrue(mi.unprovision(provisioner, instances))
        provisioner.unprovision_node.assert_has_calls([
            mock.call(mock_node1),
            mock.call(mock_node2)
        ])
        connection.baremetal.get_allocation.assert_has_calls([
            mock.call('overcloud-controller-1'),
            mock.call('overcloud-controller-3')
        ])
        connection.baremetal.get_node.assert_has_calls([
            mock.call('aaaa'),
            mock.call('node-2'),
            mock.call('node-3')
        ])
