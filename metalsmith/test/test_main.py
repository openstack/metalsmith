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
import unittest

import mock

from metalsmith import main


@mock.patch.object(main.deploy, 'deploy', autospec=True)
@mock.patch.object(main.generic, 'Password', autospec=True)
class TestMain(unittest.TestCase):
    def test_args_ok(self, mock_auth, mock_deploy):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg', 'compute']
        main.main(args)
        mock_deploy.assert_called_once_with(mock.ANY,
                                            resource_class='compute',
                                            image_id='myimg',
                                            network_id='mynet',
                                            root_disk_size=None,
                                            ssh_keys=[],
                                            capabilities={},
                                            netboot=False,
                                            wait=1800,
                                            dry_run=False)

    def test_args_debug(self, mock_auth, mock_deploy):
        args = ['--debug', 'deploy', '--network', 'mynet', '--image', 'myimg',
                'compute']
        main.main(args)
        mock_deploy.assert_called_once_with(mock.ANY,
                                            resource_class='compute',
                                            image_id='myimg',
                                            network_id='mynet',
                                            root_disk_size=None,
                                            ssh_keys=[],
                                            capabilities={},
                                            netboot=False,
                                            wait=1800,
                                            dry_run=False)

    def test_args_quiet(self, mock_auth, mock_deploy):
        args = ['--quiet', 'deploy', '--network', 'mynet', '--image', 'myimg',
                'compute']
        main.main(args)
        mock_deploy.assert_called_once_with(mock.ANY,
                                            resource_class='compute',
                                            image_id='myimg',
                                            network_id='mynet',
                                            root_disk_size=None,
                                            ssh_keys=[],
                                            capabilities={},
                                            netboot=False,
                                            wait=1800,
                                            dry_run=False)

    @mock.patch.object(main.LOG, 'critical', autospec=True)
    def test_deploy_failure(self, mock_log, mock_auth, mock_deploy):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg', 'compute']
        mock_deploy.side_effect = RuntimeError('boom')
        self.assertRaises(SystemExit, main.main, args)
        mock_log.assert_called_once_with('%s', mock_deploy.side_effect,
                                         exc_info=False)

    def test_args_capabilities(self, mock_auth, mock_deploy):
        args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                '--capability', 'foo=bar', '--capability', 'answer=42',
                'compute']
        main.main(args)
        mock_deploy.assert_called_once_with(mock.ANY,
                                            resource_class='compute',
                                            image_id='myimg',
                                            network_id='mynet',
                                            root_disk_size=None,
                                            ssh_keys=[],
                                            capabilities={'foo': 'bar',
                                                          'answer': '42'},
                                            netboot=False,
                                            wait=1800,
                                            dry_run=False)

    def test_args_configdrive(self, mock_auth, mock_deploy):
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(b'foo\n')
            fp.flush()

            args = ['deploy', '--network', 'mynet', '--image', 'myimg',
                    '--ssh-public-key', fp.name, 'compute']
            main.main(args)
            mock_deploy.assert_called_once_with(mock.ANY,
                                                resource_class='compute',
                                                image_id='myimg',
                                                network_id='mynet',
                                                root_disk_size=None,
                                                ssh_keys=['foo'],
                                                capabilities={},
                                                netboot=False,
                                                wait=1800,
                                                dry_run=False)
