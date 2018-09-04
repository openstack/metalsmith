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

"""Image sources to use when provisioning nodes."""

import abc
import logging
import os

import openstack.exceptions
import requests
import six
from six.moves.urllib import parse as urlparse

from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class _Source(object):

    def _validate(self, connection):
        """Validate the source."""

    @abc.abstractmethod
    def _node_updates(self, connection):
        """Updates required for a node to use this source."""


class GlanceImage(_Source):
    """Image from the OpenStack Image service."""

    def __init__(self, image):
        """Create a Glance source.

        :param image: `Image` object, ID or name.
        """
        self._image_id = image
        self._image_obj = None

    def _validate(self, connection):
        if self._image_obj is not None:
            return
        try:
            self._image_obj = connection.image.find_image(self._image_id,
                                                          ignore_missing=False)
        except openstack.exceptions.SDKException as exc:
            raise exceptions.InvalidImage(
                'Cannot find image %(image)s: %(error)s' %
                {'image': self._image_id, 'error': exc})

    def _node_updates(self, connection):
        self._validate(connection)
        LOG.debug('Image: %s', self._image_obj)

        updates = {
            '/instance_info/image_source': self._image_obj.id
        }
        for prop in ('kernel', 'ramdisk'):
            value = getattr(self._image_obj, '%s_id' % prop, None)
            if value:
                updates['/instance_info/%s' % prop] = value

        return updates


class HttpWholeDiskImage(_Source):
    """A whole-disk image from HTTP(s) location.

    Some deployment methods require a checksum of the image. It has to be
    provided via ``checksum`` or ``checksum_url``.

    Only ``checksum_url`` (if provided) has to be accessible from the current
    machine. Other URLs have to be accessible by the Bare Metal service (more
    specifically, by **ironic-conductor** processes).
    """

    def __init__(self, url, checksum=None, checksum_url=None,
                 kernel_url=None, ramdisk_url=None):
        """Create an HTTP source.

        :param url: URL of the image.
        :param checksum: MD5 checksum of the image. Mutually exclusive with
            ``checksum_url``.
        :param checksum_url: URL of the checksum file for the image. Has to
            be in the standard format of the ``md5sum`` tool. Mutually
            exclusive with ``checksum``.
        """
        if (checksum and checksum_url) or (not checksum and not checksum_url):
            raise TypeError('Exactly one of checksum and checksum_url has '
                            'to be specified')

        self.url = url
        self.checksum = checksum
        self.checksum_url = checksum_url
        self.kernel_url = kernel_url
        self.ramdisk_url = ramdisk_url

    def _validate(self, connection):
        # TODO(dtantsur): should we validate image URLs here? Ironic will do it
        # as well, and images do not have to be accessible from where
        # metalsmith is running.
        if self.checksum:
            return

        try:
            response = requests.get(self.checksum_url)
            response.raise_for_status()
            checksums = response.text
        except requests.RequestException as exc:
            raise exceptions.InvalidImage(
                'Cannot download checksum file %(url)s: %(err)s' %
                {'url': self.checksum_url, 'err': exc})

        try:
            checksums = _utils.parse_checksums(checksums)
        except (ValueError, TypeError) as exc:
            raise exceptions.InvalidImage(
                'Invalid checksum file %(url)s: %(err)s' %
                {'url': self.checksum_url, 'err': exc})

        fname = os.path.basename(urlparse.urlparse(self.url).path)
        try:
            self.checksum = checksums[fname]
        except KeyError:
            raise exceptions.InvalidImage(
                'There is no image checksum for %(fname)s in %(url)s' %
                {'fname': fname, 'url': self.checksum_url})

    def _node_updates(self, connection):
        self._validate(connection)
        LOG.debug('Image: %(image)s, checksum %(checksum)s',
                  {'image': self.url, 'checksum': self.checksum})
        return {
            '/instance_info/image_source': self.url,
            '/instance_info/image_checksum': self.checksum,
        }


class HttpPartitionImage(HttpWholeDiskImage):
    """A partition image from an HTTP(s) location."""

    def __init__(self, url, kernel_url, ramdisk_url, checksum=None,
                 checksum_url=None):
        """Create an HTTP source.

        :param url: URL of the root disk image.
        :param kernel_url: URL of the kernel image.
        :param ramdisk_url: URL of the initramfs image.
        :param checksum: MD5 checksum of the root disk image. Mutually
            exclusive with ``checksum_url``.
        :param checksum_url: URL of the checksum file for the root disk image.
            Has to be in the standard format of the ``md5sum`` tool. Mutually
            exclusive with ``checksum``.
        """
        super(HttpPartitionImage, self).__init__(url, checksum=checksum,
                                                 checksum_url=checksum_url)
        self.kernel_url = kernel_url
        self.ramdisk_url = ramdisk_url

    def _node_updates(self, connection):
        updates = super(HttpPartitionImage, self)._node_updates(connection)
        updates['/instance_info/kernel'] = self.kernel_url
        updates['/instance_info/ramdisk'] = self.ramdisk_url
        return updates
