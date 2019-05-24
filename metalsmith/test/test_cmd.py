# Copyright 2015-2017 Red Hat, Inc.
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
import tempfile

import fixtures
import mock
import six
import testtools

from metalsmith import _cmd
from metalsmith import _instance
from metalsmith import _provisioner
from metalsmith import instance_config
from metalsmith import sources


@mock.patch.object(_provisioner, 'Provisioner', autospec=True)
class TestDeploy(testtools.TestCase):
    def setUp(self):
        super(TestDeploy, self).setUp()
        self.print_fixture = self.useFixture(fixtures.MockPatch(
            'metalsmith._format._print', autospec=True))
        self.mock_print = self.print_fixture.mock

        self.os_conf_fixture = self.useFixture(fixtures.MockPatchObject(
            _cmd.os_config, 'OpenStackConfig', autospec=True))
        self.mock_os_conf = self.os_conf_fixture.mock

    def _check(self, mock_pr, args, reserve_args, provision_args,
               dry_run=False):
        reserve_defaults = dict(resource_class='compute',
                                conductor_group=None,
                                capabilities={},
                                traits=[],
                                candidates=None,
                                hostname=None)
        reserve_defaults.update(reserve_args)

        provision_defaults = dict(image=mock.ANY,
                                  nics=[{'network': 'mynet'}],
                                  root_size_gb=None,
                                  swap_size_mb=None,
                                  config=mock.ANY,
                                  netboot=False,
                                  wait=1800,
                                  clean_up_on_failure=True)
        provision_defaults.update(provision_args)

        _cmd.main(args)

        mock_pr.assert_called_once_with(
            cloud_region=self.mock_os_conf.return_value.get_one.return_value,
            dry_run=dry_run)
        mock_pr.return_value.reserve_node.assert_called_once_with(
            **reserve_defaults)
        mock_pr.return_value.provision_node.assert_called_once_with(
            mock_pr.return_value.reserve_node.return_value,
            **provision_defaults)

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_ok(self, mock_log, mock_pr):
        instance = mock_pr.return_value.provision_node.return_value
        instance.create_autospec(_instance.Instance)
        instance.node.name = None
        instance.node.id = '123'
        instance.state = _instance.InstanceState.ACTIVE
        instance.is_deployed = True
        instance.ip_addresses.return_value = {'private': ['1.2.3.4']}
        instance.hostname = None

        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        config = mock_pr.return_value.provision_node.call_args[1]['config']
        self.assertEqual([], config.ssh_keys)
        mock_log.basicConfig.assert_called_once_with(level=mock_log.WARNING,
                                                     format=mock.ANY)

        source = mock_pr.return_value.provision_node.call_args[1]['image']
        self.assertIsInstance(source, sources.GlanceImage)
        self.assertEqual("myimg", source.image)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.WARNING).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.CRITICAL).call_list(),
            mock_log.getLogger.mock_calls)

        self.mock_print.assert_has_calls([
            mock.call(mock.ANY, node='123', state='ACTIVE'),
            mock.call(mock.ANY, ips='private=1.2.3.4')
        ])

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_json_format(self, mock_log, mock_pr):
        instance = mock_pr.return_value.provision_node.return_value
        instance.create_autospec(_instance.Instance)
        instance.to_dict.return_value = {'node': 'dict'}

        args = ['--format', 'json', 'deploy', '--network', 'mynet',
                '--image', 'myimg', '--resource-class', 'compute']
        fake_io = six.StringIO()
        with mock.patch('sys.stdout', fake_io):
            self._check(mock_pr, args, {}, {})
            self.assertEqual(json.loads(fake_io.getvalue()),
                             {'node': 'dict'})

        mock_log.basicConfig.assert_called_once_with(level=mock_log.WARNING,
                                                     format=mock.ANY)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.WARNING).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.CRITICAL).call_list(),
            mock_log.getLogger.mock_calls)

    def test_no_ips(self, mock_pr):
        instance = mock_pr.return_value.provision_node.return_value
        instance.create_autospec(_instance.Instance)
        instance.is_deployed = True
        instance.ip_addresses.return_value = {}
        instance.node.name = None
        instance.node.id = '123'
        instance.state = _instance.InstanceState.ACTIVE
        instance.hostname = None

        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        self.mock_print.assert_called_once_with(mock.ANY, node='123',
                                                state='ACTIVE'),

    def test_not_deployed_no_ips(self, mock_pr):
        instance = mock_pr.return_value.provision_node.return_value
        instance.create_autospec(_instance.Instance)
        instance.is_deployed = False
        instance.node.name = None
        instance.node.id = '123'
        instance.state = _instance.InstanceState.DEPLOYING
        instance.hostname = None

        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        self.mock_print.assert_called_once_with(mock.ANY, node='123',
                                                state='DEPLOYING'),

    @mock.patch.object(_cmd.LOG, 'info', autospec=True)
    def test_no_logs_not_deployed(self, mock_log, mock_pr):
        instance = mock_pr.return_value.provision_node.return_value
        instance.create_autospec(_instance.Instance)
        instance.is_deployed = False

        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        self.assertFalse(mock_log.called)
        self.assertFalse(instance.ip_addresses.called)

    def test_args_dry_run(self, mock_pr):
        args = ['--dry-run', 'deploy', '--network', 'mynet',
                '--image', 'myimg', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {}, dry_run=True)

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_debug(self, mock_log, mock_pr):
        args = ['--debug', 'deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        mock_log.basicConfig.assert_called_once_with(level=mock_log.DEBUG,
                                                     format=mock.ANY)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.DEBUG).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.INFO).call_list(),
            mock_log.getLogger.mock_calls)

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_quiet(self, mock_log, mock_pr):
        args = ['--quiet', 'deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        mock_log.basicConfig.assert_called_once_with(level=mock_log.CRITICAL,
                                                     format=mock.ANY)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.CRITICAL).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.CRITICAL).call_list(),
            mock_log.getLogger.mock_calls)

        self.assertFalse(self.mock_print.called)

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_verbose_1(self, mock_log, mock_pr):
        args = ['-v', 'deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        mock_log.basicConfig.assert_called_once_with(level=mock_log.WARNING,
                                                     format=mock.ANY)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.INFO).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.CRITICAL).call_list(),
            mock_log.getLogger.mock_calls)

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_verbose_2(self, mock_log, mock_pr):
        args = ['-vv', 'deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        mock_log.basicConfig.assert_called_once_with(level=mock_log.INFO,
                                                     format=mock.ANY)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.DEBUG).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.CRITICAL).call_list(),
            mock_log.getLogger.mock_calls)

    @mock.patch.object(_cmd, 'logging', autospec=True)
    def test_args_verbose_3(self, mock_log, mock_pr):
        args = ['-vvv', 'deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        mock_log.basicConfig.assert_called_once_with(level=mock_log.DEBUG,
                                                     format=mock.ANY)
        self.assertEqual(
            mock.call('metalsmith').setLevel(mock_log.DEBUG).call_list() +
            mock.call(_cmd._URLLIB3_LOGGER).setLevel(
                mock_log.INFO).call_list(),
            mock_log.getLogger.mock_calls)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_reservation_failure(self, mock_log, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        failure = RuntimeError('boom')
        mock_pr.return_value.reserve_node.side_effect = failure
        self.assertRaises(SystemExit, _cmd.main, args)
        mock_log.assert_called_once_with('%s', failure, exc_info=False)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_deploy_failure(self, mock_log, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute']
        failure = RuntimeError('boom')
        mock_pr.return_value.provision_node.side_effect = failure
        self.assertRaises(SystemExit, _cmd.main, args)
        mock_log.assert_called_once_with('%s', failure, exc_info=False)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_invalid_hostname(self, mock_log, mock_pr):
        args = ['deploy', '--hostname', 'n_1', '--image', 'myimg',
                '--resource-class', 'compute']
        self.assertRaises(SystemExit, _cmd.main, args)
        self.assertTrue(mock_log.called)

    def test_args_capabilities(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--capability', 'foo=bar', '--capability', 'answer=42',
                '--resource-class', 'compute']
        self._check(mock_pr, args,
                    {'capabilities': {'foo': 'bar', 'answer': '42'}}, {})

    def test_args_traits(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--trait', 'foo:bar', '--trait', 'answer:42',
                '--resource-class', 'compute']
        self._check(mock_pr, args,
                    {'traits': ['foo:bar', 'answer:42']}, {})

    def test_args_configdrive(self, mock_pr):
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(b'foo\n')
            fp.flush()

            args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                    '--ssh-public-key', fp.name, '--resource-class', 'compute']
            self._check(mock_pr, args, {}, {})

        config = mock_pr.return_value.provision_node.call_args[1]['config']
        self.assertEqual(['foo'], config.ssh_keys)

    @mock.patch.object(instance_config.CloudInitConfig, 'add_user',
                       autospec=True)
    def test_args_user_name(self, mock_add_user, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--user-name', 'banana', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {})

        config = mock_pr.return_value.provision_node.call_args[1]['config']
        self.assertEqual([], config.ssh_keys)
        mock_add_user.assert_called_once_with(config, 'banana', sudo=False)

    @mock.patch.object(instance_config.CloudInitConfig, 'add_user',
                       autospec=True)
    def test_args_user_name_with_sudo(self, mock_add_user, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--user-name', 'banana', '--resource-class', 'compute',
                '--passwordless-sudo']
        self._check(mock_pr, args, {}, {})

        config = mock_pr.return_value.provision_node.call_args[1]['config']
        self.assertEqual([], config.ssh_keys)
        mock_add_user.assert_called_once_with(config, 'banana', sudo=True)

    def test_args_port(self, mock_pr):
        args = ['deploy', '--port', 'myport', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'nics': [{'port': 'myport'}]})

    def test_args_no_nics(self, mock_pr):
        args = ['deploy', '--image', 'myimg', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'nics': None})

    def test_args_networks_and_ports(self, mock_pr):
        args = ['deploy', '--network', 'net1', '--port', 'port1',
                '--port', 'port2', '--network', 'net2',
                '--image', 'myimg', '--resource-class', 'compute']
        self._check(mock_pr, args, {},
                    {'nics': [{'network': 'net1'}, {'port': 'port1'},
                              {'port': 'port2'}, {'network': 'net2'}]})

    def test_args_ips(self, mock_pr):
        args = ['deploy', '--image', 'myimg', '--resource-class', 'compute',
                '--ip', 'private:10.0.0.2', '--ip', 'public:8.0.8.0']
        self._check(mock_pr, args, {},
                    {'nics': [{'network': 'private', 'fixed_ip': '10.0.0.2'},
                              {'network': 'public', 'fixed_ip': '8.0.8.0'}]})

    def test_args_subnet(self, mock_pr):
        args = ['deploy', '--subnet', 'mysubnet', '--image', 'myimg',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'nics': [{'subnet': 'mysubnet'}]})

    def test_args_bad_ip(self, mock_pr):
        args = ['deploy', '--image', 'myimg', '--resource-class', 'compute',
                '--ip', 'private:10.0.0.2', '--ip', 'public']
        self.assertRaises(SystemExit, _cmd.main, args)
        self.assertFalse(mock_pr.return_value.reserve_node.called)
        self.assertFalse(mock_pr.return_value.provision_node.called)

    def test_args_hostname(self, mock_pr):
        instance = mock_pr.return_value.provision_node.return_value
        instance.create_autospec(_instance.Instance)
        instance.is_deployed = True
        instance.node.name = None
        instance.node.id = '123'
        instance.state = _instance.InstanceState.ACTIVE
        instance.hostname = 'host'
        instance.ip_addresses.return_value = {'private': ['1.2.3.4']}

        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--hostname', 'host', '--resource-class', 'compute']
        self._check(mock_pr, args, {'hostname': 'host'}, {})

        self.mock_print.assert_has_calls([
            mock.call(mock.ANY, node='123', state='ACTIVE'),
            mock.call(mock.ANY, hostname='host'),
            mock.call(mock.ANY, ips='private=1.2.3.4')
        ])

    def test_args_with_candidates(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--candidate', 'node1', '--candidate', 'node2',
                '--resource-class', 'compute']
        self._check(mock_pr, args, {'candidates': ['node1', 'node2']}, {})

    def test_args_conductor_group(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--conductor-group', 'loc1', '--resource-class', 'compute']
        self._check(mock_pr, args, {'conductor_group': 'loc1'}, {})

    def test_args_http_image_with_checksum(self, mock_pr):
        args = ['deploy', '--image', 'https://example.com/image.img',
                '--image-checksum', '95e750180c7921ea0d545c7165db66b8',
                '--network', 'mynet', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'image': mock.ANY})

        source = mock_pr.return_value.provision_node.call_args[1]['image']
        self.assertIsInstance(source, sources.HttpWholeDiskImage)
        self.assertEqual('https://example.com/image.img', source.url)
        self.assertEqual('95e750180c7921ea0d545c7165db66b8', source.checksum)

    def test_args_http_image_with_checksum_url(self, mock_pr):
        args = ['deploy', '--image', 'http://example.com/image.img',
                '--image-checksum', 'http://example.com/CHECKSUMS',
                '--network', 'mynet', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'image': mock.ANY})

        source = mock_pr.return_value.provision_node.call_args[1]['image']
        self.assertIsInstance(source, sources.HttpWholeDiskImage)
        self.assertEqual('http://example.com/image.img', source.url)
        self.assertEqual('http://example.com/CHECKSUMS', source.checksum_url)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_args_http_image_without_checksum(self, mock_log, mock_pr):
        args = ['deploy', '--image', 'http://example.com/image.img',
                '--resource-class', 'compute']
        self.assertRaises(SystemExit, _cmd.main, args)
        self.assertTrue(mock_log.called)
        self.assertFalse(mock_pr.return_value.reserve_node.called)
        self.assertFalse(mock_pr.return_value.provision_node.called)

    def test_args_http_partition_image(self, mock_pr):
        args = ['deploy', '--image', 'https://example.com/image.img',
                '--image-kernel', 'https://example.com/kernel',
                '--image-ramdisk', 'https://example.com/ramdisk',
                '--image-checksum', '95e750180c7921ea0d545c7165db66b8',
                '--network', 'mynet', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'image': mock.ANY})

        source = mock_pr.return_value.provision_node.call_args[1]['image']
        self.assertIsInstance(source, sources.HttpPartitionImage)
        self.assertEqual('https://example.com/image.img', source.url)
        self.assertEqual('https://example.com/kernel', source.kernel_url)
        self.assertEqual('https://example.com/ramdisk', source.ramdisk_url)
        self.assertEqual('95e750180c7921ea0d545c7165db66b8', source.checksum)

    def test_args_file_whole_disk_image(self, mock_pr):
        args = ['deploy', '--image', 'file:///var/lib/ironic/image.img',
                '--image-checksum', '95e750180c7921ea0d545c7165db66b8',
                '--network', 'mynet', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'image': mock.ANY})

        source = mock_pr.return_value.provision_node.call_args[1]['image']
        self.assertIsInstance(source, sources.FileWholeDiskImage)
        self.assertEqual('file:///var/lib/ironic/image.img', source.location)
        self.assertEqual('95e750180c7921ea0d545c7165db66b8', source.checksum)

    def test_args_file_partition_disk_image(self, mock_pr):
        args = ['deploy', '--image', 'file:///var/lib/ironic/image.img',
                '--image-kernel', 'file:///var/lib/ironic/image.vmlinuz',
                '--image-ramdisk', 'file:///var/lib/ironic/image.initrd',
                '--image-checksum', '95e750180c7921ea0d545c7165db66b8',
                '--network', 'mynet', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'image': mock.ANY})

        source = mock_pr.return_value.provision_node.call_args[1]['image']
        self.assertIsInstance(source, sources.FilePartitionImage)
        self.assertEqual('file:///var/lib/ironic/image.img', source.location)
        self.assertEqual('file:///var/lib/ironic/image.vmlinuz',
                         source.kernel_location)
        self.assertEqual('file:///var/lib/ironic/image.initrd',
                         source.ramdisk_location)
        self.assertEqual('95e750180c7921ea0d545c7165db66b8', source.checksum)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_args_file_image_without_checksum(self, mock_log, mock_pr):
        args = ['deploy', '--image', 'file:///var/lib/ironic/image.img',
                '--resource-class', 'compute']
        self.assertRaises(SystemExit, _cmd.main, args)
        self.assertTrue(mock_log.called)
        self.assertFalse(mock_pr.return_value.reserve_node.called)
        self.assertFalse(mock_pr.return_value.provision_node.called)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_args_file_image_with_incorrect_kernel(self, mock_log, mock_pr):
        args = ['deploy', '--image', 'file:///var/lib/ironic/image.img',
                '--image-kernel', 'http://example.com/image.vmlinuz',
                '--image-checksum', '95e750180c7921ea0d545c7165db66b8',
                '--resource-class', 'compute']
        self.assertRaises(SystemExit, _cmd.main, args)
        self.assertTrue(mock_log.called)
        self.assertFalse(mock_pr.return_value.reserve_node.called)
        self.assertFalse(mock_pr.return_value.provision_node.called)

    def test_args_custom_wait(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--wait', '3600', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'wait': 3600})

    def test_args_no_wait(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--no-wait', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'wait': None})

    def test_with_root_size(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--root-size', '100', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'root_size_gb': 100})

    def test_with_swap_size(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--swap-size', '4096', '--resource-class', 'compute']
        self._check(mock_pr, args, {}, {'swap_size_mb': 4096})

    def test_no_clean_up(self, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--resource-class', 'compute', '--no-clean-up']
        self._check(mock_pr, args, {}, {'clean_up_on_failure': False})


@mock.patch.object(_provisioner, 'Provisioner', autospec=True)
@mock.patch.object(_cmd.os_config, 'OpenStackConfig', autospec=True)
class TestUndeploy(testtools.TestCase):
    def setUp(self):
        super(TestUndeploy, self).setUp()
        self.print_fixture = self.useFixture(fixtures.MockPatch(
            'metalsmith._format._print', autospec=True))
        self.mock_print = self.print_fixture.mock

    def test_ok(self, mock_os_conf, mock_pr):
        node = mock_pr.return_value.unprovision_node.return_value
        node.id = '123'
        node.name = None
        node.provision_state = 'cleaning'

        args = ['undeploy', '123456']
        _cmd.main(args)

        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.unprovision_node.assert_called_once_with(
            '123456', wait=None
        )
        self.mock_print.assert_called_once_with(
            'Unprovisioning started for node %(node)s',
            node='123')

    def test_custom_wait(self, mock_os_conf, mock_pr):
        node = mock_pr.return_value.unprovision_node.return_value
        node.id = '123'
        node.name = None
        node.provision_state = 'available'

        args = ['undeploy', '--wait', '1800', '123456']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.unprovision_node.assert_called_once_with(
            '123456', wait=1800
        )
        self.mock_print.assert_called_once_with(
            'Successfully unprovisioned node %(node)s',
            node='123')

    def test_dry_run(self, mock_os_conf, mock_pr):
        args = ['--dry-run', 'undeploy', '123456']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=True)
        mock_pr.return_value.unprovision_node.assert_called_once_with(
            '123456', wait=None
        )

    def test_quiet(self, mock_os_conf, mock_pr):
        args = ['--quiet', 'undeploy', '123456']
        _cmd.main(args)

        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.unprovision_node.assert_called_once_with(
            '123456', wait=None
        )
        self.assertFalse(self.mock_print.called)

    def test_json(self, mock_os_conf, mock_pr):
        node = mock_pr.return_value.unprovision_node.return_value
        node.to_dict.return_value = {'node': 'dict'}

        args = ['--format', 'json', 'undeploy', '123456']
        fake_io = six.StringIO()
        with mock.patch('sys.stdout', fake_io):
            _cmd.main(args)
            self.assertEqual(json.loads(fake_io.getvalue()),
                             {'node': {'node': 'dict'}})

        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.unprovision_node.assert_called_once_with(
            '123456', wait=None
        )


@mock.patch.object(_provisioner, 'Provisioner', autospec=True)
@mock.patch.object(_cmd.os_config, 'OpenStackConfig', autospec=True)
class TestShowWait(testtools.TestCase):
    def setUp(self):
        super(TestShowWait, self).setUp()
        self.print_fixture = self.useFixture(fixtures.MockPatch(
            'metalsmith._format._print', autospec=True))
        self.mock_print = self.print_fixture.mock
        self.instances = [
            mock.Mock(
                spec=_instance.Instance, hostname=hostname,
                uuid=hostname[-1], is_deployed=(hostname[-1] == '1'),
                state=_instance.InstanceState.ACTIVE
                if hostname[-1] == '1' else _instance.InstanceState.DEPLOYING,
                **{'ip_addresses.return_value': {'private': ['1.2.3.4']}})
            for hostname in ['hostname1', 'hostname2']
        ]
        for inst in self.instances:
            inst.node.id = inst.uuid
            inst.node.name = 'name-%s' % inst.uuid
            inst.to_dict.return_value = {inst.node.id: inst.node.name}

    def test_show(self, mock_os_conf, mock_pr):
        mock_pr.return_value.show_instances.return_value = self.instances
        args = ['show', 'uuid1', 'hostname2']
        _cmd.main(args)

        self.mock_print.assert_has_calls([
            mock.call(mock.ANY, node='name-1 (UUID 1)', state='ACTIVE'),
            mock.call(mock.ANY, hostname='hostname1'),
            mock.call(mock.ANY, ips='private=1.2.3.4'),
            mock.call(mock.ANY, node='name-2 (UUID 2)', state='DEPLOYING'),
            mock.call(mock.ANY, hostname='hostname2'),
        ])
        mock_pr.return_value.show_instances.assert_called_once_with(
            ['uuid1', 'hostname2'])

    def test_list(self, mock_os_conf, mock_pr):
        mock_pr.return_value.list_instances.return_value = self.instances
        args = ['list']
        _cmd.main(args)

        self.mock_print.assert_has_calls([
            mock.call(mock.ANY, node='name-1 (UUID 1)', state='ACTIVE'),
            mock.call(mock.ANY, hostname='hostname1'),
            mock.call(mock.ANY, ips='private=1.2.3.4'),
            mock.call(mock.ANY, node='name-2 (UUID 2)', state='DEPLOYING'),
            mock.call(mock.ANY, hostname='hostname2'),
        ])
        mock_pr.return_value.list_instances.assert_called_once_with()

    def test_wait(self, mock_os_conf, mock_pr):
        mock_pr.return_value.wait_for_provisioning.return_value = (
            self.instances)
        args = ['wait', 'uuid1', 'hostname2']
        _cmd.main(args)

        self.mock_print.assert_has_calls([
            mock.call(mock.ANY, node='name-1 (UUID 1)', state='ACTIVE'),
            mock.call(mock.ANY, hostname='hostname1'),
            mock.call(mock.ANY, ips='private=1.2.3.4'),
            mock.call(mock.ANY, node='name-2 (UUID 2)', state='DEPLOYING'),
            mock.call(mock.ANY, hostname='hostname2'),
        ])
        mock_pr.return_value.wait_for_provisioning.assert_called_once_with(
            ['uuid1', 'hostname2'], timeout=None)

    def test_wait_custom_timeout(self, mock_os_conf, mock_pr):
        mock_pr.return_value.wait_for_provisioning.return_value = (
            self.instances)
        args = ['wait', '--timeout', '42', 'uuid1', 'hostname2']
        _cmd.main(args)

        self.mock_print.assert_has_calls([
            mock.call(mock.ANY, node='name-1 (UUID 1)', state='ACTIVE'),
            mock.call(mock.ANY, hostname='hostname1'),
            mock.call(mock.ANY, ips='private=1.2.3.4'),
            mock.call(mock.ANY, node='name-2 (UUID 2)', state='DEPLOYING'),
            mock.call(mock.ANY, hostname='hostname2'),
        ])
        mock_pr.return_value.wait_for_provisioning.assert_called_once_with(
            ['uuid1', 'hostname2'], timeout=42)

    def test_show_json(self, mock_os_conf, mock_pr):
        mock_pr.return_value.show_instances.return_value = self.instances
        args = ['--format', 'json', 'show', 'uuid1', 'hostname2']

        fake_io = six.StringIO()
        with mock.patch('sys.stdout', fake_io):
            _cmd.main(args)
            self.assertEqual(json.loads(fake_io.getvalue()),
                             {'hostname1': {'1': 'name-1'},
                              'hostname2': {'2': 'name-2'}})

    def test_list_json(self, mock_os_conf, mock_pr):
        mock_pr.return_value.list_instances.return_value = self.instances
        args = ['--format', 'json', 'list']

        fake_io = six.StringIO()
        with mock.patch('sys.stdout', fake_io):
            _cmd.main(args)
            self.assertEqual(json.loads(fake_io.getvalue()),
                             {'hostname1': {'1': 'name-1'},
                              'hostname2': {'2': 'name-2'}})
        mock_pr.return_value.list_instances.assert_called_once_with()

    def test_wait_json(self, mock_os_conf, mock_pr):
        mock_pr.return_value.wait_for_provisioning.return_value = (
            self.instances)
        args = ['--format', 'json', 'wait', 'uuid1', 'hostname2']

        fake_io = six.StringIO()
        with mock.patch('sys.stdout', fake_io):
            _cmd.main(args)
            self.assertEqual(json.loads(fake_io.getvalue()),
                             {'hostname1': {'1': 'name-1'},
                              'hostname2': {'2': 'name-2'}})
