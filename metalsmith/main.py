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


def main(args=sys.argv):
    parser = argparse.ArgumentParser(
        description='Deployment and Scheduling tool for Bare Metal')
    parser.add_argument('--debug', action='store_true',
                        help='output more logging')
    parser.add_argument('-i', '--image', help='image to use (name or UUID)',
                        required=True)
    parser.add_argument('-n', '--network',
                        help='network to use (name or UUID)', required=True),
    parser.add_argument('--os-username', default=os.environ.get('OS_USERNAME'))
    parser.add_argument('--os-password', default=os.environ.get('OS_PASSWORD'))
    parser.add_argument('--os-project-name',
                        default=os.environ.get('OS_PROJECT_NAME'))
    parser.add_argument('--os-auth-url', default=os.environ.get('OS_AUTH_URL'))
    parser.add_argument('--os-user-domain-name',
                        default=os.environ.get('OS_USER_DOMAIN_NAME'))
    parser.add_argument('--os-project-domain-name',
                        default=os.environ.get('OS_PROJECT_DOMAIN_NAME'))
    parser.add_argument('profile', help='node profile to deploy')
    args = parser.parse_args(args)

    log_fmt = ('%(asctime)s %(levelname)s %(name)s: %(message)s' if args.debug
               else '%(asctime)s %(message)s')
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format=log_fmt)
    if not args.debug:
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(
            logging.CRITICAL)

    auth = generic.Password(auth_url=args.os_auth_url,
                            username=args.os_username,
                            project_name=args.os_project_name,
                            password=args.os_password,
                            user_domain_name=args.os_user_domain_name,
                            project_domain_name=args.os_project_domain_name)
    api = os_api.API(auth)

    try:
        deploy.deploy(api, profile=args.profile,
                      image_id=args.image,
                      network_id=args.network)
    except Exception as exc:
        LOG.critical('%s', exc, exc_info=args.debug)
        sys.exit(1)
