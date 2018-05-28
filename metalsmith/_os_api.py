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

import contextlib
import logging

from ironicclient import client as ir_client
from openstack import connection
import six

from metalsmith import _utils


LOG = logging.getLogger(__name__)
NODE_FIELDS = ['name', 'uuid', 'instance_info', 'instance_uuid', 'maintenance',
               'maintenance_reason', 'properties', 'provision_state', 'extra',
               'last_error']
HOSTNAME_FIELD = 'metalsmith_hostname'


class _Remove(object):
    """Indicator that a field should be removed."""

    __slots__ = ()

    def __repr__(self):
        """Allow nicer logging."""
        return '<REMOVE>'


REMOVE = _Remove()


class DictWithAttrs(dict):
    __slots__ = ()

    def __getattr__(self, attr):
        try:
            return self[attr]
        except KeyError:
            super(DictWithAttrs, self).__getattr__(attr)


class API(object):
    """Various OpenStack API's."""

    IRONIC_VERSION = '1'
    IRONIC_MICRO_VERSION = '1.28'

    _node_list = None

    def __init__(self, session=None, cloud_region=None):
        if cloud_region is None:
            if session is None:
                raise TypeError('Either session or cloud_region must '
                                'be provided')
            self.session = session
            self.connection = connection.Connection(session=session)
        elif session is not None:
            raise TypeError('Either session or cloud_region must be provided, '
                            'but not both')
        else:
            self.session = cloud_region.get_session()
            self.connection = connection.Connection(config=cloud_region)

        LOG.debug('Creating service clients')
        self.ironic = ir_client.get_client(
            self.IRONIC_VERSION, session=self.session,
            os_ironic_api_version=self.IRONIC_MICRO_VERSION)

    def _nodes_for_lookup(self):
        return self.list_nodes(maintenance=None,
                               associated=None,
                               provision_state=None,
                               fields=['uuid', 'name', 'instance_info'])

    def attach_port_to_node(self, node, port_id):
        self.ironic.node.vif_attach(_node_id(node), port_id)

    @contextlib.contextmanager
    def cache_node_list_for_lookup(self):
        if self._node_list is None:
            self._node_list = self._nodes_for_lookup()
        yield self._node_list
        self._node_list = None

    def create_port(self, network_id, **kwargs):
        return self.connection.network.create_port(network_id=network_id,
                                                   admin_state_up=True,
                                                   **kwargs)

    def delete_port(self, port_id):
        self.connection.network.delete_port(port_id,
                                            ignore_missing=False)

    def detach_port_from_node(self, node, port_id):
        self.ironic.node.vif_detach(_node_id(node), port_id)

    def find_node_by_hostname(self, hostname):
        nodes = self._node_list or self._nodes_for_lookup()
        existing = [n for n in nodes
                    if n.instance_info.get(HOSTNAME_FIELD) == hostname]
        if len(existing) > 1:
            raise RuntimeError("More than one node found with hostname "
                               "%(host)s: %(nodes)s" %
                               {'host': hostname,
                                'nodes': ', '.join(_utils.log_node(n)
                                                   for n in existing)})
        elif not existing:
            return None
        else:
            # Fetch the complete node record
            return self.get_node(existing[0].uuid, accept_hostname=False)

    def get_image(self, image_id):
        return self.connection.image.find_image(image_id,
                                                ignore_missing=False)

    def get_network(self, network_id):
        return self.connection.network.find_network(network_id,
                                                    ignore_missing=False)

    def get_node(self, node, refresh=False, accept_hostname=False):
        if isinstance(node, six.string_types):
            if accept_hostname and _utils.is_hostname_safe(node):
                by_hostname = self.find_node_by_hostname(node)
                if by_hostname is not None:
                    return by_hostname

            return self.ironic.node.get(node, fields=NODE_FIELDS)
        elif hasattr(node, 'node'):
            # Instance object
            node = node.node
        else:
            node = node

        if refresh:
            return self.ironic.node.get(node.uuid, fields=NODE_FIELDS)
        else:
            return node

    def get_port(self, port_id):
        return self.connection.network.find_port(port_id,
                                                 ignore_missing=False)

    def list_node_attached_ports(self, node):
        return self.ironic.node.vif_list(_node_id(node))

    def list_node_ports(self, node):
        return self.ironic.node.list_ports(_node_id(node), limit=0)

    def list_nodes(self, resource_class=None, maintenance=False,
                   associated=False, provision_state='available',
                   fields=None):
        return self.ironic.node.list(limit=0, resource_class=resource_class,
                                     maintenance=maintenance,
                                     associated=associated,
                                     provision_state=provision_state,
                                     fields=fields or NODE_FIELDS)

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
