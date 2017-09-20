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
import os
import sys

from keystoneauth1.identity import generic

from metalsmith import deploy
from metalsmith import os_api


LOG = logging.getLogger(__name__)


def _do_deploy(api, args, wait=None):
    capabilities = dict(item.split('=', 1) for item in args.capability)
    if args.ssh_public_key:
        with open(args.ssh_public_key) as fp:
            ssh_keys = [fp.read().strip()]
    else:
        ssh_keys = []

    deploy.deploy(api, args.resource_class,
                  image_id=args.image,
                  network_id=args.network,
                  root_disk_size=args.root_disk_size,
                  ssh_keys=ssh_keys,
                  capabilities=capabilities,
                  netboot=args.netboot,
                  wait=wait,
                  dry_run=args.dry_run)


def _do_undeploy(api, args, wait=None):
    deploy.undeploy(api, args.node, wait=wait)


def _parse_args(args):
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
    wait.add_argument('--timeout', type=int, default=1800,
                      help='action timeout (in seconds)')
    wait.add_argument('--no-wait', action='store_true',
                      help='disable waiting for action to finish')
    parser.add_argument('--os-username', default=os.environ.get('OS_USERNAME'))
    parser.add_argument('--os-password', default=os.environ.get('OS_PASSWORD'))
    parser.add_argument('--os-project-name',
                        default=os.environ.get('OS_PROJECT_NAME'))
    parser.add_argument('--os-auth-url', default=os.environ.get('OS_AUTH_URL'))
    parser.add_argument('--os-user-domain-name',
                        default=os.environ.get('OS_USER_DOMAIN_NAME'))
    parser.add_argument('--os-project-domain-name',
                        default=os.environ.get('OS_PROJECT_DOMAIN_NAME'))

    subparsers = parser.add_subparsers()

    deploy = subparsers.add_parser('deploy')
    deploy.set_defaults(func=_do_deploy)
    deploy.add_argument('--image', help='image to use (name or UUID)',
                        required=True)
    deploy.add_argument('--network', help='network to use (name or UUID)',
                        required=True),
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
    args = _parse_args(args)
    _configure_logging(args)
    if args.no_wait:
        wait = None
    else:
        wait = args.timeout

    auth = generic.Password(auth_url=args.os_auth_url,
                            username=args.os_username,
                            project_name=args.os_project_name,
                            password=args.os_password,
                            user_domain_name=args.os_user_domain_name,
                            project_domain_name=args.os_project_domain_name)
    api = os_api.API(auth)

    try:
        args.func(api, args, wait=wait)
    except Exception as exc:
        LOG.critical('%s', exc, exc_info=args.debug)
        sys.exit(1)
