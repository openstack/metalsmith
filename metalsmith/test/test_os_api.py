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

import fixtures
import mock
import testtools

from metalsmith import _os_api
from metalsmith import _provisioner


class TestInit(testtools.TestCase):
    def test_missing_auth(self):
        self.assertRaisesRegex(TypeError, 'must be provided', _os_api.API)

    def test_both_provided(self):
        self.assertRaisesRegex(TypeError, 'not both', _os_api.API,
                               session=mock.Mock(), cloud_region=mock.Mock())

    def test_session_only(self):
        session = mock.Mock()
        api = _os_api.API(session=session)
        self.assertIs(api.session, session)

    @mock.patch.object(_os_api.connection, 'Connection', autospec=True)
    def test_cloud_region_only(self, mock_conn):
        region = mock.Mock()
        api = _os_api.API(cloud_region=region)
        self.assertIs(api.session, region.get_session.return_value)
        mock_conn.assert_called_once_with(config=region)


class TestNodes(testtools.TestCase):
    def setUp(self):
        super(TestNodes, self).setUp()
        self.session = mock.Mock()
        self.ironic_fixture = self.useFixture(
            fixtures.MockPatchObject(_os_api.ir_client, 'get_client',
                                     autospec=True))
        self.cli = self.ironic_fixture.mock.return_value
        self.api = _os_api.API(session=self.session)

    def test_get_node_by_uuid(self):
        res = self.api.get_node('uuid1')
        self.cli.node.get.assert_called_once_with('uuid1',
                                                  fields=_os_api.NODE_FIELDS)
        self.assertIs(res, self.cli.node.get.return_value)

    def test_get_node_by_node(self):
        res = self.api.get_node(mock.sentinel.node)
        self.assertIs(res, mock.sentinel.node)
        self.assertFalse(self.cli.node.get.called)

    def test_get_node_by_instance(self):
        inst = _provisioner.Instance(mock.Mock(), mock.Mock())
        res = self.api.get_node(inst)
        self.assertIs(res, inst.node)
        self.assertFalse(self.cli.node.get.called)
