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

from oslo_utils import excutils

from metalsmith import os_api


LOG = logging.getLogger(__name__)


def _log_node(node):
    if node.name:
        return '%s (UUID %s)' % (node.name, node.uuid)
    else:
        return node.uuid


def _get_capabilities(node):
    return dict(x.split(':', 1) for x in
                node.properties.get('capabilities', '').split(',') if x)


def reserve(api, nodes, profile):
    suitable_nodes = []
    for node in nodes:
        caps = _get_capabilities(node)
        LOG.debug('Capabilities for node %(node)s: %(cap)s',
                  {'node': _log_node(node), 'cap': caps})
        if caps.get('profile') == profile:
            suitable_nodes.append(node)

    if not suitable_nodes:
        raise RuntimeError('No nodes found with profile %s' % profile)

    for node in suitable_nodes:
        try:
            api.update_node(node.uuid, instance_uuid=node.uuid)
        except os_api.ir_exc.Conflict:
            LOG.info('Node %s was occupied, proceeding with the next',
                     _log_node(node))
        else:
            return node

    raise RuntimeError('Unable to reserve any node')


def clean_up(api, node):
    try:
        api.update_node(node.uuid, instance_uuid=os_api.REMOVE)
    except Exception:
        LOG.debug('Failed to remove instance_uuid, assuming already removed')


def prepare(api, node, network, image):
    raise NotImplementedError('Not implemented')


def provision(api, node):
    raise NotImplementedError('Not implemented')


def deploy(profile, image_id, network_id, auth_args):
    """Deploy an image on a given profile."""
    LOG.debug('Deploying image %(image)s on node with profile %(profile)s '
              'on network %(net)s',
              {'image': image_id, 'profile': profile, 'net': network_id})
    api = os_api.API(**auth_args)

    image = api.get_image_info(image_id)
    if image is None:
        raise RuntimeError('Image %s does not exist' % image_id)
    LOG.debug('Image: %s', image)
    network = api.get_network(network_id)
    if network is None:
        raise RuntimeError('Network %s does not exist' % network_id)
    LOG.debug('Network: %s', network)

    nodes = api.list_nodes()
    LOG.debug('Ironic nodes: %s', nodes)
    if not len(nodes):
        raise RuntimeError('No available nodes found')
    LOG.info('Got list of %d available nodes from Ironic', len(nodes))

    node = reserve(api, nodes, profile)
    LOG.info('Reserved node %s', _log_node(node))

    try:
        prepare(api, node, network, image)
        provision(api, node)
    except Exception:
        with excutils.save_and_reraise_exception():
            LOG.error('Deploy failed, cleaning up')
            try:
                clean_up(api, node)
            except Exception:
                LOG.exception('Clean up also failed')
