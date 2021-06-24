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
from urllib import parse as urlparse
import warnings

import openstack.exceptions
import requests


from metalsmith import _utils
from metalsmith import exceptions


LOG = logging.getLogger(__name__)


class _Source(object, metaclass=abc.ABCMeta):

    def _validate(self, connection, root_size_gb):
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
        self.image = image
        self._image_obj = None

    def _validate(self, connection, root_size_gb):
        if self._image_obj is not None:
            return
        try:
            self._image_obj = connection.image.find_image(self.image,
                                                          ignore_missing=False)
        except openstack.exceptions.SDKException as exc:
            raise exceptions.InvalidImage(
                'Cannot find image %(image)s: %(error)s' %
                {'image': self.image, 'error': exc})

        if (root_size_gb is None
                and any(getattr(self._image_obj, x, None) is not None
                        for x in ('kernel_id', 'ramdisk_id'))):
            raise exceptions.UnknownRootDiskSize(
                'Partition images require root partition size')

    def _node_updates(self, connection):
        LOG.debug('Image: %s', self._image_obj)

        updates = {
            'image_source': self._image_obj.id
        }
        for prop in ('kernel', 'ramdisk'):
            value = getattr(self._image_obj, '%s_id' % prop, None)
            if value:
                updates[prop] = value

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
                 disk_format=None):
        """Create an HTTP source.

        :param url: URL of the image.
        :param checksum: MD5 checksum of the image. Mutually exclusive with
            ``checksum_url``.
        :param checksum_url: URL of the checksum file for the image. Has to
            be in the standard format of the ``md5sum`` tool. Mutually
            exclusive with ``checksum``.
        :param disk_format: Optional value to set for ``instance_info``
            ``image_disk_format``.
        """
        if (checksum and checksum_url) or (not checksum and not checksum_url):
            raise TypeError('Exactly one of checksum and checksum_url has '
                            'to be specified')

        self.url = url
        self.checksum = checksum
        self.checksum_url = checksum_url
        self.disk_format = disk_format

    def _validate(self, connection, root_size_gb):
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
        LOG.debug('Image: %(image)s, checksum %(checksum)s',
                  {'image': self.url, 'checksum': self.checksum})
        updates = {
            'image_source': self.url,
            'image_checksum': self.checksum,
        }
        if self.disk_format:
            updates['image_disk_format'] = self.disk_format
        return updates


class HttpPartitionImage(HttpWholeDiskImage):
    """A partition image from an HTTP(s) location."""

    def __init__(self, url, kernel_url, ramdisk_url, checksum=None,
                 checksum_url=None, disk_format=None):
        """Create an HTTP source.

        :param url: URL of the root disk image.
        :param kernel_url: URL of the kernel image.
        :param ramdisk_url: URL of the initramfs image.
        :param checksum: MD5 checksum of the root disk image. Mutually
            exclusive with ``checksum_url``.
        :param checksum_url: URL of the checksum file for the root disk image.
            Has to be in the standard format of the ``md5sum`` tool. Mutually
            exclusive with ``checksum``.
        :param disk_format: Optional value to set for ``instance_info``
            ``image_disk_format``.
        """
        super(HttpPartitionImage, self).__init__(url, checksum=checksum,
                                                 checksum_url=checksum_url,
                                                 disk_format=disk_format)
        self.kernel_url = kernel_url
        self.ramdisk_url = ramdisk_url

    def _validate(self, connection, root_size_gb):
        super(HttpPartitionImage, self)._validate(connection, root_size_gb)
        if root_size_gb is None:
            raise exceptions.UnknownRootDiskSize(
                'Partition images require root partition size')

    def _node_updates(self, connection):
        updates = super(HttpPartitionImage, self)._node_updates(connection)
        updates['kernel'] = self.kernel_url
        updates['ramdisk'] = self.ramdisk_url
        return updates


class FileWholeDiskImage(_Source):
    """A whole-disk image from a local file location.

    .. warning::
        The location must be local to the **ironic-conductor** process handling
        the node, not to metalsmith itself! Since there is no easy way to
        determine which conductor handles a node, the same file must be
        available at the same location to all conductors in the same group.
    """

    def __init__(self, location, checksum=None):
        """Create a local file source.

        :param location: Location of the image, optionally starting with
            ``file://``.
        :param checksum: MD5 checksum of the image. DEPRECATED: checksums do
            not actually work with file images.
        """
        if not location.startswith('file://'):
            location = 'file://' + location
        self.location = location
        self.checksum = checksum
        if self.checksum:
            warnings.warn("Checksums cannot be used with file images",
                          DeprecationWarning)

    def _node_updates(self, connection):
        LOG.debug('Image: %s', self.location)
        return {
            'image_source': self.location,
        }


class FilePartitionImage(FileWholeDiskImage):
    """A partition image from a local file location.

    .. warning::
        The location must be local to the **ironic-conductor** process handling
        the node, not to metalsmith itself! Since there is no easy way to
        determine which conductor handles a node, the same file must be
        available at the same location to all conductors in the same group.
    """

    def __init__(self, location, kernel_location, ramdisk_location,
                 checksum=None):
        """Create a local file source.

        :param location: Location of the image, optionally starting with
            ``file://``.
        :param kernel_location: Location of the kernel of the image,
            optionally starting with ``file://``.
        :param ramdisk_location: Location of the ramdisk of the image,
            optionally starting with ``file://``.
        :param checksum: MD5 checksum of the image. DEPRECATED: checksums do
            not actually work with file images.
        """
        super(FilePartitionImage, self).__init__(location, checksum)
        if not kernel_location.startswith('file://'):
            kernel_location = 'file://' + kernel_location
        if not ramdisk_location.startswith('file://'):
            ramdisk_location = 'file://' + ramdisk_location
        self.kernel_location = kernel_location
        self.ramdisk_location = ramdisk_location

    def _validate(self, connection, root_size_gb):
        super(FilePartitionImage, self)._validate(connection, root_size_gb)
        if root_size_gb is None:
            raise exceptions.UnknownRootDiskSize(
                'Partition images require root partition size')

    def _node_updates(self, connection):
        updates = super(FilePartitionImage, self)._node_updates(connection)
        updates['kernel'] = self.kernel_location
        updates['ramdisk'] = self.ramdisk_location
        return updates


def detect(image, kernel=None, ramdisk=None, checksum=None):
    """Try detecting the correct source type from the provided information.

    .. note::
        Images without a schema are assumed to be Glance images.

    :param image: Location of the image: ``file://``, ``http://``, ``https://``
        link or a Glance image name or UUID.
    :param kernel: Location of the kernel (if present): ``file://``,
        ``http://``, ``https://`` link or a Glance image name or UUID.
    :param ramdisk: Location of the ramdisk (if present): ``file://``,
        ``http://``, ``https://`` link or a Glance image name or UUID.
    :param checksum: MD5 checksum of the image: ``http://`` or ``https://``
        link or a string.
    :return: A valid source object.
    :raises: ValueError if the given parameters do not correspond to any
        valid source.
    """
    image_type = _link_type(image)
    checksum_type = _link_type(checksum)

    if image_type == 'glance':
        if kernel or ramdisk or checksum:
            raise ValueError('kernel, image and checksum cannot be provided '
                             'for Glance images')
        else:
            return GlanceImage(image)

    kernel_type = _link_type(kernel)
    ramdisk_type = _link_type(ramdisk)
    if image_type == 'http' and not checksum:
        raise ValueError('checksum is required for HTTP images')

    if image_type == 'file':
        if (kernel_type not in (None, 'file')
                or ramdisk_type not in (None, 'file')):
            raise ValueError('kernel and ramdisk can only be files '
                             'for file images')

        if kernel or ramdisk:
            return FilePartitionImage(image,
                                      kernel_location=kernel,
                                      ramdisk_location=ramdisk,
                                      checksum=checksum)
        else:
            return FileWholeDiskImage(image, checksum=checksum)
    else:
        if (kernel_type not in (None, 'http')
                or ramdisk_type not in (None, 'http')
                or checksum_type == 'file'):
            raise ValueError('kernal, ramdisk and checksum can only be HTTP '
                             'links for HTTP images')

        if checksum_type == 'http':
            kwargs = {'checksum_url': checksum}
        else:
            kwargs = {'checksum': checksum}

        # Assume raw image based on file extension
        if image.endswith('.raw'):
            kwargs['disk_format'] = 'raw'

        if kernel or ramdisk:
            return HttpPartitionImage(image,
                                      kernel_url=kernel,
                                      ramdisk_url=ramdisk,
                                      **kwargs)
        else:
            return HttpWholeDiskImage(image, **kwargs)


def _link_type(link):
    if link is None:
        return None
    elif link.startswith('http://') or link.startswith('https://'):
        return 'http'
    elif link.startswith('file://'):
        return 'file'
    else:
        return 'glance'
