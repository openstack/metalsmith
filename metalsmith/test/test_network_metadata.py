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

import unittest
from unittest import mock

from metalsmith import _network_metadata


class TestMetadataAdd(unittest.TestCase):

    def test_metadata_add_links(self):
        port = mock.Mock()
        network = mock.Mock()
        port.id = 'port_id'
        port.mac_address = 'aa:bb:cc:dd:ee:ff'
        network.mtu = 1500
        links = []
        expected = [{'id': 'port_id',
                     'type': 'phy',
                     'mtu': 1500,
                     'ethernet_mac_address': 'aa:bb:cc:dd:ee:ff'}]
        _network_metadata.metadata_add_links(links, port, network)
        self.assertEqual(expected, links)

    def test_metadata_add_services(self):
        subnet_a = mock.Mock()
        subnet_b = mock.Mock()
        subnet_a.dns_nameservers = ['192.0.2.1', '192.0.2.2']
        subnet_b.dns_nameservers = ['192.0.2.11', '192.0.2.22']
        subnets = [subnet_a, subnet_b]
        services = []
        expected = [{'address': '192.0.2.1', 'type': 'dns'},
                    {'address': '192.0.2.2', 'type': 'dns'},
                    {'address': '192.0.2.11', 'type': 'dns'},
                    {'address': '192.0.2.22', 'type': 'dns'}]
        _network_metadata.metadata_add_services(services, subnets)
        self.assertEqual(expected, services)

    def test_metadata_add_network_ipv4_dhcp(self):
        idx = 1
        fixed_ip = {'ip_address': '192.0.2.100', 'subnet_id': 'subnet_id'}

        port = mock.Mock()
        port.id = 'port_id'

        subnet = mock.Mock()
        subnet.cidr = '192.0.2.0/26'
        subnet.ip_version = 4
        subnet.is_dhcp_enabled = True
        subnet.host_routes = [
            {'destination': '192.0.2.64/26', 'nexthop': '192.0.2.1'},
            {'destination': '192.0.2.128/26', 'nexthop': '192.0.2.1'}
        ]
        subnet.dns_nameservers = ['192.0.2.11', '192.0.2.22']

        network = mock.Mock()
        network.id = 'network_id'
        network.name = 'net_name'

        networks = []
        expected = [{'id': 'net_name1',
                     'ip_address': '192.0.2.100',
                     'link': 'port_id',
                     'netmask': '255.255.255.192',
                     'network_id': 'network_id',
                     'routes': [{'gateway': '192.0.2.1',
                                 'netmask': '255.255.255.192',
                                 'network': '192.0.2.64'},
                                {'gateway': '192.0.2.1',
                                 'netmask': '255.255.255.192',
                                 'network': '192.0.2.128'}],
                     'services': [{'address': '192.0.2.11', 'type': 'dns'},
                                  {'address': '192.0.2.22', 'type': 'dns'}],
                     'type': 'ipv4_dhcp'}]
        _network_metadata.metadata_add_network(networks, idx, fixed_ip, port,
                                               network, subnet)
        self.assertEqual(expected, networks)

    def test_metadata_add_network_ipv6_stateful(self):
        idx = 1
        fixed_ip = {'ip_address': '2001:db8:1::10', 'subnet_id': 'subnet_id'}
        port = mock.Mock()
        port.id = 'port_id'

        subnet = mock.Mock()
        subnet.cidr = '2001:db8:1::/64'
        subnet.ip_version = 6
        subnet.ipv6_address_mode = 'dhcpv6-stateful'
        subnet.host_routes = [
            {'destination': '2001:db8:2::/64', 'nexthop': '2001:db8:1::1'},
            {'destination': '2001:db8:3::/64', 'nexthop': '2001:db8:1::1'}
        ]
        subnet.dns_nameservers = ['2001:db8:1::ee', '2001:db8:2::ff']

        network = mock.Mock()
        network.id = 'network_id'
        network.name = 'net_name'

        networks = []
        expected = [
            {'id': 'net_name1',
             'ip_address': '2001:db8:1::10',
             'link': 'port_id',
             'netmask': 'ffff:ffff:ffff:ffff::',
             'network_id': 'network_id',
             'routes': [{'gateway': '2001:db8:1::1',
                         'netmask': 'ffff:ffff:ffff:ffff::',
                         'network': '2001:db8:2::'},
                        {'gateway': '2001:db8:1::1',
                         'netmask': 'ffff:ffff:ffff:ffff::',
                         'network': '2001:db8:3::'}],
             'services': [{'address': '2001:db8:1::ee', 'type': 'dns'},
                          {'address': '2001:db8:2::ff', 'type': 'dns'}],
             'type': 'ipv6_dhcpv6-stateful'}]
        _network_metadata.metadata_add_network(networks, idx, fixed_ip, port,
                                               network, subnet)
        self.assertEqual(expected, networks)
