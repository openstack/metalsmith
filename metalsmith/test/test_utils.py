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

import unittest

from metalsmith import _utils


class TestIsHostnameSafe(unittest.TestCase):

    def test_valid(self):
        self.assertTrue(_utils.is_hostname_safe('spam'))
        self.assertTrue(_utils.is_hostname_safe('spAm'))
        self.assertTrue(_utils.is_hostname_safe('SPAM'))
        self.assertTrue(_utils.is_hostname_safe('spam-eggs'))
        self.assertTrue(_utils.is_hostname_safe('spam.eggs'))
        self.assertTrue(_utils.is_hostname_safe('9spam'))
        self.assertTrue(_utils.is_hostname_safe('spam7'))
        self.assertTrue(_utils.is_hostname_safe('br34kf4st'))
        self.assertTrue(_utils.is_hostname_safe('s' * 63))
        self.assertTrue(_utils.is_hostname_safe('www.example.com'))
        long_str = 'a' * 63 + '.' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 63
        self.assertTrue(_utils.is_hostname_safe(long_str))

    def test_invalid(self):
        self.assertFalse(_utils.is_hostname_safe('-spam'))
        self.assertFalse(_utils.is_hostname_safe('spam-'))
        self.assertFalse(_utils.is_hostname_safe('spam_eggs'))
        self.assertFalse(_utils.is_hostname_safe('spam eggs'))
        self.assertFalse(_utils.is_hostname_safe('$pam'))
        self.assertFalse(_utils.is_hostname_safe('egg$'))
        self.assertFalse(_utils.is_hostname_safe('spam#eggs'))
        self.assertFalse(_utils.is_hostname_safe(' eggs'))
        self.assertFalse(_utils.is_hostname_safe('spam '))
        self.assertFalse(_utils.is_hostname_safe('s' * 64))
        self.assertFalse(_utils.is_hostname_safe(''))
        self.assertFalse(_utils.is_hostname_safe(None))
        self.assertFalse(_utils.is_hostname_safe('www.nothere.com_'))
        self.assertFalse(_utils.is_hostname_safe('www.nothere_.com'))
        self.assertFalse(_utils.is_hostname_safe('www..nothere.com'))
        self.assertFalse(_utils.is_hostname_safe('www.-nothere.com'))
        long_str = 'a' * 63 + '.' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 63
        self.assertFalse(_utils.is_hostname_safe(long_str + '.'))
        self.assertFalse(_utils.is_hostname_safe('a' * 255))
        # These are valid domain names, but not hostnames (RFC 1123)
        self.assertFalse(_utils.is_hostname_safe('www.example.com.'))
        self.assertFalse(_utils.is_hostname_safe('http._sctp.www.example.com'))
        self.assertFalse(_utils.is_hostname_safe('mail.pets_r_us.net'))
        self.assertFalse(_utils.is_hostname_safe('mail-server-15.my_host.org'))
        # RFC 952 forbids single-character hostnames
        self.assertFalse(_utils.is_hostname_safe('s'))

    def test_not_none(self):
        # Need to ensure a binary response for success or fail
        self.assertIsNotNone(_utils.is_hostname_safe('spam'))
        self.assertIsNotNone(_utils.is_hostname_safe('-spam'))
