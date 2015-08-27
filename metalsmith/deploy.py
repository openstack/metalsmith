# Copyright 2015 Red Hat, Inc.
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

import logging

import glanceclient
from keystoneclient.v2_0 import client as ks_client
from neutronclient.neutron import client as neu_client


LOG = logging.getLogger(__name__)


class API(object):
    """Various OpenStack API's."""

    GLANCE_VERSION = '1'
    NEUTRON_VERSION = '2.0'

    def __init__(self, **kwargs):
        LOG.debug('creating Keystone client')
        self.keystone = ks_client.Client(**kwargs)
        self.auth_token = self.keystone.auth_token
        LOG.debug('creating service clients')
        self.glance = glanceclient.Client(
            self.GLANCE_VERSION, endpoint=self.get_endpoint('image'),
            token=self.auth_token)
        self.neutron = neu_client.Client(
            self.NEUTRON_VERSION, endpoint_url=self.get_endpoint('network'),
            token=self.auth_token)

    def get_endpoint(self, service_type, endpoint_type='internalurl'):
        service_id = self.keystone.services.find(type=service_type).id
        endpoint = self.keystone.endpoints.find(service_id=service_id)
        return getattr(endpoint, endpoint_type)


def deploy(profile, image):
    """Deploy an image on a given profile."""
    LOG.debug('deploying image %(image)s on node with profile %(profile)s',
              {'image': image, 'profile': profile})
    API()
