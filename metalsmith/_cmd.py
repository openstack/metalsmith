# Copyright 2015-2017 Red Hat, Inc.
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

import argparse
import logging
import sys

from openstack import config as os_config

from metalsmith import _provisioner


LOG = logging.getLogger(__name__)


class NICAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        assert option_string in ('--port', '--network')
        nics = getattr(namespace, self.dest, None) or []
        if option_string == '--network':
            nics.append({'network': values})
        else:
            nics.append({'port': values})
        setattr(namespace, self.dest, nics)


def _do_deploy(api, args, wait=None):
    capabilities = dict(item.split('=', 1) for item in args.capability)
    if args.ssh_public_key:
        with open(args.ssh_public_key) as fp:
            ssh_keys = [fp.read().strip()]
    else:
        ssh_keys = []

    node = api.reserve_node(args.resource_class, capabilities=capabilities)
    api.provision_node(node,
                       image_ref=args.image,
                       nics=args.nics,
                       root_disk_size=args.root_disk_size,
                       ssh_keys=ssh_keys,
                       netboot=args.netboot,
                       wait=wait)


def _do_undeploy(api, args, wait=None):
    api.unprovision_node(args.node, wait=wait)


def _parse_args(args, config):
    parser = argparse.ArgumentParser(
        description='Deployment and Scheduling tool for Bare Metal')
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument('-q', '--quiet', action='store_true',
                           help='output only errors')
    verbosity.add_argument('--debug', action='store_true',
                           help='output more logging')
    parser.add_argument('--dry-run', action='store_true',
                        help='do not take any destructive actions')
    wait = parser.add_mutually_exclusive_group()
    wait.add_argument('--wait', type=int, default=1800,
                      help='action timeout (in seconds)')
    wait.add_argument('--no-wait', action='store_true',
                      help='disable waiting for action to finish')

    config.register_argparse_arguments(parser, sys.argv[1:])

    subparsers = parser.add_subparsers()

    deploy = subparsers.add_parser('deploy')
    deploy.set_defaults(func=_do_deploy)
    deploy.add_argument('--image', help='image to use (name or UUID)',
                        required=True)
    deploy.add_argument('--network', help='network to use (name or UUID)',
                        dest='nics', action=NICAction)
    deploy.add_argument('--port', help='port to attach (name or UUID)',
                        dest='nics', action=NICAction)
    deploy.add_argument('--netboot', action='store_true',
                        help='boot from network instead of local disk')
    deploy.add_argument('--root-disk-size', type=int,
                        help='root disk size (in GiB), defaults to (local_gb '
                        '- 2)')
    deploy.add_argument('--capability', action='append', metavar='NAME=VALUE',
                        default=[], help='capabilities the nodes should have')
    deploy.add_argument('--ssh-public-key', help='SSH public key to load')
    deploy.add_argument('resource_class', help='node resource class to deploy')

    undeploy = subparsers.add_parser('undeploy')
    undeploy.set_defaults(func=_do_undeploy)
    undeploy.add_argument('node', help='node UUID')
    return parser.parse_args(args)


def _configure_logging(args):
    log_fmt = ('%(asctime)s %(levelname)s %(name)s: %(message)s' if args.debug
               else '[%(asctime)s] %(message)s')
    if args.quiet:
        level = logging.ERROR
    elif args.debug:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(level=level, format=log_fmt)
    if args.debug:
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
    else:
        logging.getLogger('urllib3.connectionpool').setLevel(logging.CRITICAL)


def main(args=sys.argv[1:]):
    config = os_config.OpenStackConfig()
    args = _parse_args(args, config)
    _configure_logging(args)
    if args.no_wait:
        wait = None
    else:
        wait = args.wait

    region = config.get_one(argparse=args)
    api = _provisioner.Provisioner(cloud_region=region, dry_run=args.dry_run)

    try:
        args.func(api, args, wait=wait)
    except Exception as exc:
        LOG.critical('%s', exc, exc_info=args.debug)
        sys.exit(1)
