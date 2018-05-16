# Copyright 2015-2018 Red Hat, Inc.
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
from neutronclient.v2_0 import client as neu_client
import six


LOG = logging.getLogger(__name__)
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

    GLANCE_VERSION = '2'
    IRONIC_VERSION = '1'
    IRONIC_MICRO_VERSION = '1.28'

    def __init__(self, session=None, cloud_region=None):
        if cloud_region is None:
            if session is None:
                raise TypeError('Either session or cloud_region must '
                                'be provided')
            self.session = session
        elif session is not None:
            raise TypeError('Either session or cloud_region must be provided, '
                            'but not both')
        else:
            self.session = cloud_region.get_session()

        LOG.debug('Creating service clients')
        self.glance = glanceclient.Client(self.GLANCE_VERSION,
                                          session=self.session)
        self.neutron = neu_client.Client(session=self.session)
        self.ironic = ir_client.get_client(
            self.IRONIC_VERSION, session=self.session,
            os_ironic_api_version=self.IRONIC_MICRO_VERSION)

    def attach_port_to_node(self, node, port_id):
        self.ironic.node.vif_attach(_node_id(node), port_id)

    def create_port(self, network_id, **kwargs):
        port_body = dict(network_id=network_id,
                         admin_state_up=True,
                         **kwargs)
        port = self.neutron.create_port({'port': port_body})
        return DictWithAttrs(port['port'])

    def delete_port(self, port_id):
        self.neutron.delete_port(port_id)

    def detach_port_from_node(self, node, port_id):
        self.ironic.node.vif_detach(_node_id(node), port_id)

    def get_image_info(self, image_id):
        for img in self.glance.images.list():
            if img.name == image_id or img.id == image_id:
                return img

    def get_network(self, network_id):
        for net in self.neutron.list_networks()['networks']:
            if net['name'] == network_id or net['id'] == network_id:
                return DictWithAttrs(net)

    def get_node(self, node):
        if isinstance(node, six.string_types):
            return self.ironic.node.get(node)
        else:
            return node

    def get_port(self, port_id):
        port = self.neutron.show_port(port_id)
        return DictWithAttrs(port['port'])

    def list_node_attached_ports(self, node):
        return self.ironic.node.vif_list(_node_id(node))

    def list_node_ports(self, node):
        return self.ironic.node.list_ports(_node_id(node), limit=0)

    def list_nodes(self, resource_class=None, maintenance=False,
                   associated=False, provision_state='available', detail=True):
        return self.ironic.node.list(limit=0, resource_class=resource_class,
                                     maintenance=maintenance,
                                     associated=associated, detail=detail,
                                     provision_state=provision_state)

    def node_action(self, node, action, **kwargs):
        self.ironic.node.set_provision_state(_node_id(node), action, **kwargs)

    def release_node(self, node):
        return self.update_node(_node_id(node), instance_uuid=REMOVE)

    def reserve_node(self, node, instance_uuid):
        return self.update_node(_node_id(node), instance_uuid=instance_uuid)

    def update_node(self, node, *args, **attrs):
        if args:
            attrs.update(args[0])
        patches = _convert_patches(attrs)
        return self.ironic.node.update(_node_id(node), patches)

    def validate_node(self, node, validate_deploy=False):
        ifaces = ['power', 'management']
        if validate_deploy:
            ifaces += ['deploy']

        validation = self.ironic.node.validate(_node_id(node))
        for iface in ifaces:
            result = getattr(validation, iface)
            if not result['result']:
                raise RuntimeError('%s: %s' % (iface, result['reason']))

    def wait_for_node_state(self, node, state, timeout):
        self.ironic.node.wait_for_provision_state(_node_id(node),
                                                  state,
                                                  timeout=timeout)


def _node_id(node):
    if isinstance(node, six.string_types):
        return node
    else:
        return node.uuid


def _convert_patches(attrs):
    patches = []
    for key, value in attrs.items():
        if not key.startswith('/'):
            key = '/' + key

        if value is REMOVE:
            patches.append({'op': 'remove', 'path': key})
        else:
            patches.append({'op': 'add', 'path': key, 'value': value})

    return patches
