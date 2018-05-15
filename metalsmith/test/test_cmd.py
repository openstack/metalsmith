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

import tempfile

import mock
import testtools

from metalsmith import _cmd
from metalsmith import _provisioner


@mock.patch.object(_provisioner, 'Provisioner', autospec=True)
@mock.patch.object(_cmd.os_config, 'OpenStackConfig', autospec=True)
class TestMain(testtools.TestCase):
    def test_args_ok(self, mock_os_conf, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg', 'compute']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.reserve_node.assert_called_once_with(
            resource_class='compute',
            capabilities={}
        )
        mock_pr.return_value.provision_node.assert_called_once_with(
            mock_pr.return_value.reserve_node.return_value,
            image_ref='myimg',
            network_refs=['mynet'],
            root_disk_size=None,
            ssh_keys=[],
            netboot=False,
            wait=1800)

    def test_args_dry_run(self, mock_os_conf, mock_pr):
        args = ['--dry-run', 'deploy', '--network', 'mynet',
                '--image', 'myimg', 'compute']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=True)
        mock_pr.return_value.reserve_node.assert_called_once_with(
            resource_class='compute',
            capabilities={}
        )
        mock_pr.return_value.provision_node.assert_called_once_with(
            mock_pr.return_value.reserve_node.return_value,
            image_ref='myimg',
            network_refs=['mynet'],
            root_disk_size=None,
            ssh_keys=[],
            netboot=False,
            wait=1800)

    def test_args_debug(self, mock_os_conf, mock_pr):
        args = ['--debug', 'deploy', '--network', 'mynet', '--image', 'myimg',
                'compute']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.reserve_node.assert_called_once_with(
            resource_class='compute',
            capabilities={}
        )
        mock_pr.return_value.provision_node.assert_called_once_with(
            mock_pr.return_value.reserve_node.return_value,
            image_ref='myimg',
            network_refs=['mynet'],
            root_disk_size=None,
            ssh_keys=[],
            netboot=False,
            wait=1800)

    def test_args_quiet(self, mock_os_conf, mock_pr):
        args = ['--quiet', 'deploy', '--network', 'mynet', '--image', 'myimg',
                'compute']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.reserve_node.assert_called_once_with(
            resource_class='compute',
            capabilities={}
        )
        mock_pr.return_value.provision_node.assert_called_once_with(
            mock_pr.return_value.reserve_node.return_value,
            image_ref='myimg',
            network_refs=['mynet'],
            root_disk_size=None,
            ssh_keys=[],
            netboot=False,
            wait=1800)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_reservation_failure(self, mock_log, mock_os_conf, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg', 'compute']
        failure = RuntimeError('boom')
        mock_pr.return_value.reserve_node.side_effect = failure
        self.assertRaises(SystemExit, _cmd.main, args)
        mock_log.assert_called_once_with('%s', failure, exc_info=False)

    @mock.patch.object(_cmd.LOG, 'critical', autospec=True)
    def test_deploy_failure(self, mock_log, mock_os_conf, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg', 'compute']
        failure = RuntimeError('boom')
        mock_pr.return_value.provision_node.side_effect = failure
        self.assertRaises(SystemExit, _cmd.main, args)
        mock_log.assert_called_once_with('%s', failure, exc_info=False)

    def test_args_capabilities(self, mock_os_conf, mock_pr):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--capability', 'foo=bar', '--capability', 'answer=42',
                'compute']
        _cmd.main(args)
        mock_pr.assert_called_once_with(
            cloud_region=mock_os_conf.return_value.get_one.return_value,
            dry_run=False)
        mock_pr.return_value.reserve_node.assert_called_once_with(
            resource_class='compute',
            capabilities={'foo': 'bar', 'answer': '42'}
        )
        mock_pr.return_value.provision_node.assert_called_once_with(
            mock_pr.return_value.reserve_node.return_value,
            image_ref='myimg',
            network_refs=['mynet'],
            root_disk_size=None,
            ssh_keys=[],
            netboot=False,
            wait=1800)

    def test_args_configdrive(self, mock_os_conf, mock_pr):
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(b'foo\n')
            fp.flush()

            args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                    '--ssh-public-key', fp.name, 'compute']
            _cmd.main(args)
            mock_pr.assert_called_once_with(
                cloud_region=mock_os_conf.return_value.get_one.return_value,
                dry_run=False)
            mock_pr.return_value.reserve_node.assert_called_once_with(
                resource_class='compute',
                capabilities={}
            )
            mock_pr.return_value.provision_node.assert_called_once_with(
                mock_pr.return_value.reserve_node.return_value,
                image_ref='myimg',
                network_refs=['mynet'],
                root_disk_size=None,
                ssh_keys=['foo'],
                netboot=False,
                wait=1800)
