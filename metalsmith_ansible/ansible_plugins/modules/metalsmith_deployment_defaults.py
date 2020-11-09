# Copyright 2020 Red Hat, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import os

from ansible.module_utils.basic import AnsibleModule

import yaml


DOCUMENTATION = '''
---
module: metalsmith_deployment_defaults
short_description: Transform instances list data for metalsmith_instances
author: "Steve Baker (@stevebaker)"
description:
  - Takes a list of instances from the metalsmith_deployment role
    and a dict of defaults and transforms that to the format required
    by the metalsmith_instances module.
options:
  instances:
    description:
      - List of node description dicts to perform operations on (in the
        metalsmith_deployment instances format)
    type: list
    default: []
    elements: dict
  defaults:
    description:
      - Dict of defaults to use for missing values. Keys correspond to the
        metalsmith_deployment instances format.
    type: dict
'''


def transform(module, instances, defaults):
    mi = []

    def value(src, key, dest, to_key=None):
        if not to_key:
            to_key = key
        value = src.get(key, defaults.get(key))
        if value:
            dest[to_key] = value

    for src in instances:
        dest = {'image': {}}
        value(src, 'hostname', dest)
        value(src, 'candidates', dest)
        value(src, 'nics', dest)
        value(src, 'netboot', dest)
        value(src, 'root_size', dest, 'root_size_gb')
        value(src, 'swap_size', dest, 'swap_size_mb')
        value(src, 'capabilities', dest)
        value(src, 'traits', dest)
        value(src, 'resource_class', dest)
        value(src, 'conductor_group', dest)
        value(src, 'user_name', dest)
        image = dest['image']
        value(src, 'image', image, 'href')
        value(src, 'image_checksum', image, 'checksum')
        value(src, 'image_kernel', image, 'kernel')
        value(src, 'image_ramdisk', image, 'ramdisk')
        value(src, 'config_drive', dest)

        # keys in metalsmith_instances not currently in metalsmith_deployment:
        # passwordless_sudo

        # keys in metalsmith_deployment not currently in metalsmith_instances:
        # extra_args (CLI args cannot translate to the python lib,
        #             but they are mainly for auth and output formatting apart
        #             from --dry-run)
        if 'extra_args' in src:
            module.fail_json(
                changed=False,
                msg="extra_args is no longer supported"
            )

        # state (metalsmith_instances has a single state attribute for every
        #        instance)
        if 'state' in src:
            module.fail_json(
                changed=False,
                msg="Per-instance state is no longer supported, "
                    "use variable metalsmith_state"
            )

        # source keys could be a string or a list of strings
        # and the strings could be a path to a public key or the key contents.
        # Normalize this to a list of key contents
        keys = []
        source_keys = src.get('ssh_public_keys')
        if source_keys:
            if isinstance(source_keys, str):
                source_keys = [source_keys]
            for source_key in source_keys:
                if os.path.isfile(source_key):
                    with open(source_key) as f:
                        source_key = f.read()
                keys.append(source_key)
        if keys:
            dest['ssh_public_keys'] = keys

        mi.append(dest)

    module.exit_json(
        changed=False,
        msg="{} instances transformed".format(len(mi)),
        instances=mi
    )
    return mi


def main():
    module = AnsibleModule(
        argument_spec=yaml.safe_load(DOCUMENTATION)['options'],
        supports_check_mode=False,
    )

    instances = module.params['instances']
    defaults = module.params['defaults']
    transform(module, instances, defaults)


if __name__ == '__main__':
    main()
