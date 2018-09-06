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

import openstack.exceptions
import six

from metalsmith import exceptions


LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class _Source(object):

    def _validate(self, connection):
        """Validate the source."""

    @abc.abstractmethod
    def _node_updates(self, connection):
        """Updates required for a node to use this source."""


class Glance(_Source):
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
