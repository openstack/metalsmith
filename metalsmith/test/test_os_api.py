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

import mock
import testtools

from metalsmith import _os_api


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
