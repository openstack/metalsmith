# Copyright 2021 Red Hat, Inc.
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

import ipaddress
import logging

from openstack import exceptions as sdk_exc

from metalsmith import exceptions


LOG = logging.getLogger(__name__)


def create_network_metadata(connection, attached_ports, node_id):
    network_data = {}
    if not attached_ports:
        return network_data

    # NOTE(TheJulia): This method has no unit testing.... It should,
    # but given the state of maintenance of Metalsmith, it doesn't make
    # sense to add a bunch of tests here.
    links = network_data.setdefault('links', [])
    networks = network_data.setdefault('networks', [])
    services = network_data.setdefault('services', [])

    try:
        # Gets a list of Ironic ports
        ironic_ports = list(connection.baremetal.ports(node_id=node_id,
                                                       details=True))
    except sdk_exc.SDKException as exc:
        raise exceptions.NetworkResourceNotFound(
            'Failed to retrieve a list of baremetal ports: %s' % exc)

    for attached_port in attached_ports:
        try:
            port_mac = None
            for p in ironic_ports:
                if 'tenant_vif_port_id' in p.internal_info:
                    port_vif = p.internal_info.get('tenant_vif_port_id')
                    if port_vif == attached_port:
                        port_mac = p.address
            if port_mac is None:
                raise exceptions.NetworkResourceNotFound(
                    'Could not find the baremetal MAC address of '
                    'neutron port %s.', attached_port)
            port = connection.network.get_port(attached_port)
            net = connection.network.get_network(port.network_id)
            subnets = [connection.network.get_subnet(x['subnet_id'])
                       for x in port.fixed_ips]
            subnets_by_id = {x.id: x for x in subnets}
        except sdk_exc.SDKException as exc:
            raise exceptions.NetworkResourceNotFound(
                'Cannot find network resource: %s' % exc)

        metadata_add_links(links, port, net, port_mac)
        metadata_add_services(services, subnets)
        for idx, fixed_ip in enumerate(port.fixed_ips):
            subnet = subnets_by_id[fixed_ip['subnet_id']]
            metadata_add_network(networks, idx, fixed_ip, port, net, subnet)

    return network_data


def metadata_add_links(links, port, network, port_mac):
    links.append({'id': port.id,
                  'type': 'phy',
                  'mtu': network.mtu,
                  'ethernet_mac_address': port_mac})


def metadata_add_services(services, subnets):
    for subnet in subnets:
        for dns_nameserver in subnet.dns_nameservers:
            services.append({'type': 'dns',
                             'address': dns_nameserver})


def metadata_add_network(networks, idx, fixed_ip, port, network, subnet):
    ip_net = ipaddress.ip_network(subnet.cidr)

    net_data = {'id': network.name + str(idx),
                'network_id': network.id,
                'link': port.id,
                'ip_address': fixed_ip['ip_address'],
                'netmask': str(ip_net.netmask)}

    if subnet.ip_version == 4:
        net_data['type'] = 'ipv4_dhcp' if subnet.is_dhcp_enabled else 'ipv4'
    elif subnet.ip_version == 6:
        net_data['type'] = ('ipv6_{}'.format(subnet.ipv6_address_mode)
                            if subnet.ipv6_address_mode else 'ipv6')

    net_routes = net_data.setdefault('routes', [])
    for route in subnet.host_routes:
        ip_net = ipaddress.ip_network(route['destination'])
        net_routes.append({'network': str(ip_net.network_address),
                           'netmask': str(ip_net.netmask),
                           'gateway': route['nexthop']})

    # Services go in both "network" and toplevel.
    # Ref: https://docs.openstack.org/nova/latest/_downloads/9119ca7ac90aa2990e762c08baea3a36/network_data.json  # noqa
    net_services = net_data.setdefault('services', [])
    metadata_add_services(net_services, [subnet])

    networks.append(net_data)
