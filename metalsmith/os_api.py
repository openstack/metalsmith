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
from ironicclient import client as ir_client
from ironicclient import exc as ir_exc  # noqa
from keystoneclient.v2_0 import client as ks_client
from neutronclient.neutron import client as neu_client


LOG = logging.getLogger(__name__)
DEFAULT_ENDPOINTS = {
    'image': 'http://127.0.0.1:9292/',
    'network': 'http://127.0.0.1:9696/',
    'baremetal': 'http://127.0.0.1:6385/',
}
REMOVE = object()


class DictWithAttrs(dict):
    __slots__ = ()

    def __getattr__(self, attr):
        try:
            return self[attr]
        except KeyError:
            super(DictWithAttrs, self).__getattr__(attr)


class API(object):
    """Various OpenStack API's."""

    GLANCE_VERSION = '1'
    NEUTRON_VERSION = '2.0'
    IRONIC_VERSION = 1

    def __init__(self, **kwargs):
        LOG.debug('Creating Keystone client')
        self.keystone = ks_client.Client(**kwargs)
        self.auth_token = self.keystone.auth_token
        LOG.debug('Creating service clients')
        self.glance = glanceclient.Client(
            self.GLANCE_VERSION, endpoint=self.get_endpoint('image'),
            token=self.auth_token)
        self.neutron = neu_client.Client(
            self.NEUTRON_VERSION, endpoint_url=self.get_endpoint('network'),
            token=self.auth_token)
        self.ironic = ir_client.get_client(
            self.IRONIC_VERSION, ironic_url=self.get_endpoint('baremetal'),
            os_auth_token=self.auth_token)

    def get_endpoint(self, service_type, endpoint_type='internalurl'):
        service_id = self.keystone.services.find(type=service_type).id
        try:
            endpoint = self.keystone.endpoints.find(service_id=service_id)
        except Exception as exc:
            default = DEFAULT_ENDPOINTS.get(service_type)
            LOG.warn('Failed to detect %(srv)s service endpoint, using '
                     'the default of %(def)s: %(err)s',
                     {'srv': service_type, 'def': default, 'err': exc})
            return default
        return getattr(endpoint, endpoint_type)

    def get_image_info(self, image_id):
        for img in self.glance.images.list():
            if img.name == image_id or img.id == image_id:
                return img

    def get_network(self, network_id):
        for net in self.neutron.list_networks()['networks']:
            if net['name'] == network_id or net['id'] == network_id:
                return DictWithAttrs(net)

    def list_nodes(self, maintenance=False, associated=False,
                   provision_state='available', detail=True):
        nodes = self.ironic.node.list(limit=0, maintenance=maintenance,
                                      associated=associated, detail=detail)
        if provision_state:
            # TODO(dtantsur): use Liberty API for filtring by state
            nodes = [n for n in nodes
                     if n.provision_state.lower() == provision_state.lower()]

        return nodes

    def update_node(self, node_id, **attrs):
        patches = []
        for key, value in attrs.items():
            if not key.startswith('/'):
                key = '/' + key

            if value is REMOVE:
                patches.append({'op': 'remove', 'path': key})
            else:
                patches.append({'op': 'add', 'path': key, 'value': value})

        return self.ironic.node.update(node_id, patches)
