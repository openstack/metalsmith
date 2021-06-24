# Copyright 2019 Red Hat, Inc.
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
from unittest import mock

from metalsmith import exceptions
from metalsmith import sources


class TestDetect(unittest.TestCase):

    def test_glance_whole_disk(self):
        source = sources.detect('foobar')
        self.assertIsInstance(source, sources.GlanceImage)
        self.assertEqual(source.image, 'foobar')

        conn = mock.Mock(spec=['image'])
        conn.image.find_image.return_value = mock.Mock(
            id=42, kernel_id=None, ramdisk_id=None)
        source._validate(conn, None)
        self.assertEqual({'image_source': 42}, source._node_updates(conn))

    def test_glance_partition(self):
        source = sources.detect('foobar')
        self.assertIsInstance(source, sources.GlanceImage)
        self.assertEqual(source.image, 'foobar')

        conn = mock.Mock(spec=['image'])
        conn.image.find_image.return_value = mock.Mock(
            id=42, kernel_id=1, ramdisk_id=2)
        source._validate(conn, 9)
        self.assertEqual({'image_source': 42, 'kernel': 1, 'ramdisk': 2},
                         source._node_updates(conn))

    def test_glance_partition_missing_root(self):
        source = sources.detect('foobar')
        self.assertIsInstance(source, sources.GlanceImage)
        self.assertEqual(source.image, 'foobar')

        conn = mock.Mock(spec=['image'])
        conn.image.find_image.return_value = mock.Mock(
            id=42, kernel_id=1, ramdisk_id=2)
        self.assertRaises(exceptions.UnknownRootDiskSize,
                          source._validate, conn, None)

    def test_glance_invalid_arguments(self):
        for kwargs in [{'kernel': 'foo'},
                       {'ramdisk': 'foo'},
                       {'checksum': 'foo'}]:
            self.assertRaisesRegex(ValueError, 'cannot be provided',
                                   sources.detect, 'foobar', **kwargs)

    def test_checksum_required(self):
        for tp in ('http', 'https'):
            self.assertRaisesRegex(ValueError, 'checksum is required',
                                   sources.detect, '%s://foo' % tp)

    def test_file_whole_disk(self):
        source = sources.detect('file:///image')
        self.assertIs(source.__class__, sources.FileWholeDiskImage)
        self.assertEqual(source.location, 'file:///image')
        self.assertIsNone(source.checksum)

        source._validate(mock.Mock(), None)

    def test_file_partition_disk(self):
        source = sources.detect('file:///image',
                                kernel='file:///kernel',
                                ramdisk='file:///ramdisk')
        self.assertIs(source.__class__, sources.FilePartitionImage)
        self.assertEqual(source.location, 'file:///image')
        self.assertIsNone(source.checksum)
        self.assertEqual(source.kernel_location, 'file:///kernel')
        self.assertEqual(source.ramdisk_location, 'file:///ramdisk')

        source._validate(mock.Mock(), 9)

    def test_file_partition_disk_missing_root(self):
        source = sources.detect('file:///image', checksum='abcd',
                                kernel='file:///kernel',
                                ramdisk='file:///ramdisk')
        self.assertRaises(exceptions.UnknownRootDiskSize,
                          source._validate, mock.Mock(), None)

    def test_file_partition_inconsistency(self):
        for kwargs in [{'kernel': 'foo'},
                       {'ramdisk': 'foo'},
                       {'kernel': 'http://foo'},
                       {'ramdisk': 'http://foo'}]:
            kwargs.setdefault('checksum', 'abcd')
            self.assertRaisesRegex(ValueError, 'can only be files',
                                   sources.detect, 'file:///image', **kwargs)

    def test_http_whole_disk(self):
        source = sources.detect('http:///image', checksum='abcd')
        self.assertIs(source.__class__, sources.HttpWholeDiskImage)
        self.assertEqual(source.url, 'http:///image')
        self.assertEqual(source.checksum, 'abcd')

        source._validate(mock.Mock(), None)
        self.assertEqual({
            'image_checksum': 'abcd',
            'image_source': 'http:///image'
        }, source._node_updates(None))

    def test_http_whole_disk_raw(self):
        source = sources.detect('http:///image.raw', checksum='abcd')
        self.assertIs(source.__class__, sources.HttpWholeDiskImage)
        self.assertEqual(source.url, 'http:///image.raw')
        self.assertEqual(source.checksum, 'abcd')

        source._validate(mock.Mock(), None)
        self.assertEqual({
            'image_checksum': 'abcd',
            'image_source': 'http:///image.raw',
            'image_disk_format': 'raw'
        }, source._node_updates(None))

    def test_https_whole_disk(self):
        source = sources.detect('https:///image', checksum='abcd')
        self.assertIs(source.__class__, sources.HttpWholeDiskImage)
        self.assertEqual(source.url, 'https:///image')
        self.assertEqual(source.checksum, 'abcd')

        source._validate(mock.Mock(), None)

    def test_https_whole_disk_checksum(self):
        source = sources.detect('https:///image',
                                checksum='https://checksum')
        self.assertIs(source.__class__, sources.HttpWholeDiskImage)
        self.assertEqual(source.url, 'https:///image')
        self.assertEqual(source.checksum_url, 'https://checksum')

    def test_http_partition_disk(self):
        source = sources.detect('http:///image', checksum='abcd',
                                kernel='http:///kernel',
                                ramdisk='http:///ramdisk')
        self.assertIs(source.__class__, sources.HttpPartitionImage)
        self.assertEqual(source.url, 'http:///image')
        self.assertEqual(source.checksum, 'abcd')
        self.assertEqual(source.kernel_url, 'http:///kernel')
        self.assertEqual(source.ramdisk_url, 'http:///ramdisk')

        source._validate(mock.Mock(), 9)
        self.assertEqual({
            'image_checksum': 'abcd',
            'image_source': 'http:///image',
            'kernel': 'http:///kernel',
            'ramdisk': 'http:///ramdisk'
        }, source._node_updates(None))

    def test_http_partition_disk_raw(self):
        source = sources.detect('http:///image.raw', checksum='abcd',
                                kernel='http:///kernel',
                                ramdisk='http:///ramdisk')
        self.assertIs(source.__class__, sources.HttpPartitionImage)
        self.assertEqual(source.url, 'http:///image.raw')
        self.assertEqual(source.checksum, 'abcd')
        self.assertEqual(source.kernel_url, 'http:///kernel')
        self.assertEqual(source.ramdisk_url, 'http:///ramdisk')

        source._validate(mock.Mock(), 9)
        self.assertEqual({
            'image_checksum': 'abcd',
            'image_source': 'http:///image.raw',
            'kernel': 'http:///kernel',
            'ramdisk': 'http:///ramdisk',
            'image_disk_format': 'raw'
        }, source._node_updates(None))

    def test_http_partition_disk_missing_root(self):
        source = sources.detect('http:///image', checksum='abcd',
                                kernel='http:///kernel',
                                ramdisk='http:///ramdisk')
        self.assertRaises(exceptions.UnknownRootDiskSize,
                          source._validate, mock.Mock(), None)

    def test_https_partition_disk(self):
        source = sources.detect('https:///image', checksum='abcd',
                                # Can mix HTTP and HTTPs
                                kernel='http:///kernel',
                                ramdisk='https:///ramdisk')
        self.assertIs(source.__class__, sources.HttpPartitionImage)
        self.assertEqual(source.url, 'https:///image')
        self.assertEqual(source.checksum, 'abcd')
        self.assertEqual(source.kernel_url, 'http:///kernel')
        self.assertEqual(source.ramdisk_url, 'https:///ramdisk')

    def test_https_partition_disk_checksum(self):
        source = sources.detect('https:///image',
                                # Can mix HTTP and HTTPs
                                checksum='http://checksum',
                                kernel='http:///kernel',
                                ramdisk='https:///ramdisk')
        self.assertIs(source.__class__, sources.HttpPartitionImage)
        self.assertEqual(source.url, 'https:///image')
        self.assertEqual(source.checksum_url, 'http://checksum')
        self.assertEqual(source.kernel_url, 'http:///kernel')
        self.assertEqual(source.ramdisk_url, 'https:///ramdisk')

    def test_http_partition_inconsistency(self):
        for kwargs in [{'kernel': 'foo'},
                       {'ramdisk': 'foo'},
                       {'kernel': 'file://foo'},
                       {'ramdisk': 'file://foo'},
                       {'checksum': 'file://foo'}]:
            kwargs.setdefault('checksum', 'abcd')
            self.assertRaisesRegex(ValueError, 'can only be HTTP',
                                   sources.detect, 'http:///image', **kwargs)
