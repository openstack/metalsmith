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

import argparse
import logging
import sys

from openstack import config as os_config

from metalsmith import _format
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


def _do_deploy(api, args, formatter, wait=None):
    capabilities = dict(item.split('=', 1) for item in args.capability)
    if args.ssh_public_key:
        with open(args.ssh_public_key) as fp:
            ssh_keys = [fp.read().strip()]
    else:
        ssh_keys = []

    node = api.reserve_node(args.resource_class, capabilities=capabilities)
    instance = api.provision_node(node,
                                  image_ref=args.image,
                                  nics=args.nics,
                                  root_disk_size=args.root_disk_size,
                                  ssh_keys=ssh_keys,
                                  netboot=args.netboot,
                                  wait=wait)
    formatter.deploy(instance)


def _do_undeploy(api, args, formatter, wait=None):
    node = api.unprovision_node(args.node, wait=wait)
    formatter.undeploy(node)


def _parse_args(args, config):
    parser = argparse.ArgumentParser(
        description='Deployment and Scheduling tool for Bare Metal')
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument('-q', '--quiet', action='store_true',
                           help='output only errors')
    verbosity.add_argument('--debug', action='store_true',
                           help='output extensive logging')
    verbosity.add_argument('-v', '--verbose', action='count', default=0,
                           dest='verbosity',
                           help='increase output verbosity, can be specified '
                           'up to three times')
    parser.add_argument('--dry-run', action='store_true',
                        help='do not take any destructive actions')
    parser.add_argument('--format', choices=list(_format.FORMATS),
                        default=_format.DEFAULT_FORMAT,
                        help='output format')

    config.register_argparse_arguments(parser, sys.argv[1:])

    subparsers = parser.add_subparsers()

    deploy = subparsers.add_parser('deploy')
    deploy.set_defaults(func=_do_deploy)
    wait = deploy.add_mutually_exclusive_group()
    wait.add_argument('--wait', type=int, default=1800,
                      help='time (in seconds) to wait for node to become '
                      'active')
    wait.add_argument('--no-wait', action='store_true',
                      help='disable waiting for deploy to finish')
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
    wait = undeploy.add_mutually_exclusive_group()
    wait.add_argument('--wait', type=int,
                      help='time (in seconds) to wait for node to become '
                      'available for deployment again')
    return parser.parse_args(args)


_URLLIB3_LOGGER = 'urllib3.connectionpool'


def _configure_logging(args):
    log_fmt = ('%(asctime)s %(levelname)s %(name)s: %(message)s'
               if args.debug or args.verbosity
               else '[%(asctime)s] %(message)s')

    # Verbosity:
    # 0 (the default) - warnings and errors
    # 1 - info from metalsmith, warnings and errors from everything else
    # 2 - debug from metalsmith, info from everything else
    # 3 - the same as --debug
    base_level = logging.WARNING
    metalsmith_level = base_level
    urllib_level = logging.CRITICAL

    if args.quiet:
        base_level = logging.CRITICAL
        metalsmith_level = base_level
    elif args.debug or args.verbosity > 2:
        base_level = logging.DEBUG
        metalsmith_level = base_level
        urllib_level = logging.INFO
    elif args.verbosity == 2:
        base_level = logging.INFO
        metalsmith_level = logging.DEBUG
    elif args.verbosity == 1:
        metalsmith_level = logging.INFO

    logging.basicConfig(level=base_level, format=log_fmt)
    logging.getLogger('metalsmith').setLevel(metalsmith_level)
    logging.getLogger(_URLLIB3_LOGGER).setLevel(urllib_level)


def main(args=sys.argv[1:]):
    config = os_config.OpenStackConfig()
    args = _parse_args(args, config)
    _configure_logging(args)
    if getattr(args, 'no_wait', None):
        wait = None
    else:
        wait = args.wait
    if args.quiet:
        formatter = _format.NULL_FORMAT
    else:
        formatter = _format.FORMATS[args.format]

    region = config.get_one(argparse=args)
    api = _provisioner.Provisioner(cloud_region=region, dry_run=args.dry_run)

    try:
        args.func(api, args, formatter, wait=wait)
    except Exception as exc:
        LOG.critical('%s', exc, exc_info=args.debug)
        sys.exit(1)
