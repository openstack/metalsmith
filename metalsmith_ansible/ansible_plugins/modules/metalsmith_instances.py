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

from concurrent import futures
import io
import logging

from ansible.module_utils.basic import AnsibleModule
try:
    from ansible.module_utils.openstack import openstack_cloud_from_module
    from ansible.module_utils.openstack import openstack_full_argument_spec
    from ansible.module_utils.openstack import openstack_module_kwargs
except ImportError:
    openstack_cloud_from_module = None
    openstack_full_argument_spec = None
    openstack_module_kwargs = None

import metalsmith
from metalsmith import instance_config
from metalsmith import sources
from openstack import exceptions as os_exc

import yaml


DOCUMENTATION = '''
---
module: metalsmith_instances
short_description: Manage baremetal instances with metalsmith
author: "Steve Baker (@stevebaker)"
description:
  - Provision and unprovision ironic baremetal instances using metalsmith,
    which is a a simple tool to provision bare metal machines using
    OpenStack Bare Metal Service (ironic) and, optionally, OpenStack
    Image Service (glance) and OpenStack Networking Service (neutron).
options:
  instances:
    description:
      - List of node description dicts to perform operations on
    type: list
    default: []
    elements: dict
    suboptions:
      hostname:
        description:
          - Host name to use, defaults to Node's name or UUID
        type: str
      name:
        description:
          - The name of an existing node to provision, this name is appended
            to the candidates list
        type: str
      candidates:
        description:
          - List of nodes (UUIDs or names) to be considered for deployment
        type: list
        elements: str
      image:
        description:
          - Details of the image you want to provision onto the node
        type: dict
        required: True
        suboptions:
          href:
            description:
              - Image to use (name, UUID or URL)
            type: str
            required: True
          checksum :
            description:
              - Image MD5 checksum or URL with checksums
            type: str
          kernel:
            description:
              - URL of the image's kernel
            type: str
          ramdisk:
            description:
              - URL of the image's ramdisk
            type: str
      nics:
        description:
          - List of requested NICs
        type: list
        elements: dict
        suboptions:
          network:
            description:
              - Network to create a port on (name or UUID)
          subnet:
            description:
              - Subnet to create a port on (name or UUID)
          port:
            description:
              - Port to attach (name or UUID)
          fixed_ip:
            description:
              - Attach IP from the network

      netboot:
        description:
          - Boot from network instead of local disk
        default: no
        type: bool
      root_size_gb:
        description:
          - Root partition size (in GiB), defaults to (local_gb - 1)
        type: int
      swap_size_mb:
        description:
          - Swap partition size (in MiB), defaults to no swap
        type: int
      capabilities:
        description:
          - Selection criteria to match the node capabilities
        type: dict
      traits:
        description:
          - Traits the node should have
        type: list
        elements: str
      ssh_public_keys:
        description:
          - SSH public keys to load
      resource_class:
        description:
          - Node resource class to provision
        type: str
        default: baremetal
      conductor_group:
        description:
          - Conductor group to pick the node from
        type: str
      user_name:
        description:
          - Name of the admin user to create
        type: str
      passwordless_sudo:
        description:
          - Allow password-less sudo for the user
        default: yes
        type: bool
      config_drive:
        description:
          - Extra data to add to the config-drive generated for this instance
        type: dict
        suboptions:
          cloud_config:
            description:
              - Dict of cloud-init cloud-config tasks to run on node
                boot. The 'users' directive can be used to configure extra
                users other than the 'user_name' admin user.
            type: dict
          meta_data:
            description:
              - Extra metadata to include with the config-drive metadata.
                This will be added to the generated metadata
                'public_keys', 'uuid', 'name', and 'hostname'.
            type: dict
  clean_up:
    description:
      - Clean up resources on failure
    default: yes
    type: bool
  state:
    description:
      - Desired provision state, "present" to provision,
        "absent" to unprovision, "reserved" to create an allocation
        record without changing the node state
    default: present
    choices:
    - present
    - absent
    - reserved
  wait:
    description:
      - A boolean value instructing the module to wait for node provision
        to complete before returning.  A 'yes' is implied if the number of
        instances is more than the concurrency.
    type: bool
    default: no
  timeout:
    description:
      - An integer value representing the number of seconds to wait for the
        node provision to complete.
    type: int
    default: 3600
  concurrency:
    description:
      - Maximum number of instances to provision at once. Set to 0 to have no
        concurrency limit
    type: int
    default: 0
  log_level:
    description:
      - Set the logging level for the log which is available in the
        returned 'logging' result.
    default: info
    choices:
    - debug
    - info
    - warning
    - error
'''

EXAMPLES = '''
- name: Provision instances
  metalsmith_instances:
    instances:
    - name: node-0
      hostname: compute-0
      image: overcloud-full
    state: present
    wait: true
    clean_up: false
    timeout: 1200
    concurrency: 20
    log_level: info
  register: baremetal_provisioned

- name: Metalsmith log for provision instances
  debug:
    var: baremetal_provisioned.logging
'''


METALSMITH_LOG_MAP = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR
}

BASE_LOG_MAP = {
    'debug': logging.INFO,
    'info': logging.WARNING,
    'warning': logging.WARNING,
    'error': logging.ERROR
}


def _get_source(instance):
    image = instance.get('image')
    return sources.detect(image=image.get('href'),
                          kernel=image.get('kernel'),
                          ramdisk=image.get('ramdisk'),
                          checksum=image.get('checksum'))


def reserve(provisioner, instances, clean_up):
    nodes = []
    for instance in instances:
        candidates = instance.get('candidates', [])
        if instance.get('name') is not None:
            candidates.append(instance['name'])
        if not candidates:
            candidates = None
        try:
            node = provisioner.reserve_node(
                hostname=instance.get('hostname'),
                resource_class=instance.get('resource_class', 'baremetal'),
                capabilities=instance.get('capabilities'),
                candidates=candidates,
                traits=instance.get('traits'),
                conductor_group=instance.get('conductor_group')),
            if isinstance(node, tuple):
                node = node[0]
            nodes.append(node)
            # side-effect of populating the instance name, which is passed to
            # a later provision step
            instance['name'] = node.id
        except Exception as exc:
            if clean_up:
                # Remove all reservations on failure
                _release_nodes(provisioner, [i.id for i in nodes])
            raise exc
    return len(nodes) > 0, nodes


def _release_nodes(provisioner, node_ids):
    for node in node_ids:
        try:
            provisioner.unprovision_node(node)
        except Exception:
            pass


def provision(provisioner, instances, timeout, concurrency, clean_up, wait):
    if not instances:
        return False, []

    # first, ensure all instances are reserved
    reserve(provisioner, [i for i in instances if not i.get('name')], clean_up)

    nodes = []

    # no limit on concurrency, create a worker for every instance
    if concurrency < 1:
        concurrency = len(instances)

    # if concurrency is less than instances, need to wait for
    # instance completion
    if concurrency < len(instances):
        wait = True

    provision_jobs = []
    exceptions = []
    with futures.ThreadPoolExecutor(max_workers=concurrency) as p:
        for i in instances:
            provision_jobs.append(p.submit(
                _provision_instance, provisioner, i, nodes, timeout, wait
            ))
    for job in futures.as_completed(provision_jobs):
        e = job.exception()
        if e:
            exceptions.append(e)

            if clean_up:
                # first, cancel all jobs
                for job in provision_jobs:
                    job.cancel()
                # Unprovision all provisioned so far.
                # This is best-effort as some provision calls may have
                # started but not yet appended to nodes.
                _release_nodes(provisioner, [i.uuid for i in nodes])
                nodes = []
    if exceptions:
        # TODO(sbaker) future enhancement to tolerate a proportion of failures
        # so that provisioning and deployment can continue
        raise exceptions[0]

    return len(nodes) > 0, nodes


def _provision_instance(provisioner, instance, nodes, timeout, wait):
    name = instance.get('name')

    image = _get_source(instance)
    ssh_keys = instance.get('ssh_public_keys')
    config_drive = instance.get('config_drive', {})
    cloud_config = config_drive.get('cloud_config')
    meta_data = config_drive.get('meta_data')
    config = instance_config.CloudInitConfig(ssh_keys=ssh_keys,
                                             user_data=cloud_config,
                                             meta_data=meta_data)
    if instance.get('user_name'):
        config.add_user(instance.get('user_name'), admin=True,
                        sudo=instance.get('passwordless_sudo', True))
    node = provisioner.provision_node(
        name,
        config=config,
        hostname=instance.get('hostname'),
        image=image,
        nics=instance.get('nics'),
        root_size_gb=instance.get('root_size_gb'),
        swap_size_mb=instance.get('swap_size_mb'),
        netboot=instance.get('netboot', False)
    )
    nodes.append(node)
    if wait:
        provisioner.wait_for_provisioning(
            [node.uuid], timeout=timeout)


def unprovision(provisioner, instances):
    connection = provisioner.connection
    for instance in instances:
        hostname = instance.get('hostname')
        node = None
        if hostname:
            try:
                allocation = connection.baremetal.get_allocation(hostname)
                node = connection.baremetal.get_node(allocation.node_id)
            except os_exc.ResourceNotFound:
                # Allocation for this hostname doesn't exist, so attempt
                # to lookup by node name
                pass

        name = instance.get('name')
        if not node and name:
            try:
                node = connection.baremetal.get_node(name)
            except os_exc.ResourceNotFound:
                # Node with this name doesn't exist, so there is no
                # node to unprovision
                pass

        if node:
            provisioner.unprovision_node(node)
    return True


def _configure_logging(log_level):
    log_fmt = ('%(asctime)s %(levelname)s %(name)s: %(message)s')
    urllib_level = logging.CRITICAL

    base_level = BASE_LOG_MAP[log_level]
    metalsmith_level = METALSMITH_LOG_MAP[log_level]

    logging.basicConfig(level=base_level, format=log_fmt)
    logging.getLogger('urllib3.connectionpool').setLevel(urllib_level)
    logger = logging.getLogger('metalsmith')
    logger.setLevel(metalsmith_level)
    log_stream = io.StringIO()
    logger.addHandler(logging.StreamHandler(log_stream))
    return log_stream


def main():
    if not openstack_full_argument_spec:
        raise RuntimeError(
            'This module requires ansible-collections-openstack')

    # Modules in Ansible OpenStack Collection prior to 2.0.0 are not compatible
    # with openstacksdk >=0.99.0, but the functions used here ARE compatible
    # and will most likely not be removed in collection release 2.0.0, so we
    # can safely remove the MAXIMUM_SDK_VERSION and thus use this module with
    # releases of openstacksdk.
    # TODO: Remove once ansible-collections-openstack 2.0.0 has been released
    from ansible.module_utils import openstack as aoc
    aoc.MAXIMUM_SDK_VERSION = None

    argument_spec = openstack_full_argument_spec(
        **yaml.safe_load(DOCUMENTATION)['options']
    )
    module_kwargs = openstack_module_kwargs()
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=False,
        **module_kwargs
    )

    log_stream = _configure_logging(module.params['log_level'])

    try:
        sdk, cloud = openstack_cloud_from_module(module)
        provisioner = metalsmith.Provisioner(cloud_region=cloud.config)
        instances = module.params['instances']
        state = module.params['state']
        concurrency = module.params['concurrency']
        timeout = module.params['timeout']
        wait = module.params['wait']
        clean_up = module.params['clean_up']

        if state == 'present':
            changed, nodes = provision(provisioner, instances,
                                       timeout, concurrency, clean_up,
                                       wait)
            instances = [{
                'name': i.node.name or i.uuid,
                'hostname': i.hostname,
                'id': i.uuid,
            } for i in nodes]
            module.exit_json(
                changed=changed,
                msg="{} instances provisioned".format(len(nodes)),
                instances=instances,
                logging=log_stream.getvalue()
            )

        if state == 'reserved':
            changed, nodes = reserve(provisioner, instances, clean_up)
            module.exit_json(
                changed=changed,
                msg="{} instances reserved".format(len(nodes)),
                ids=[node.id for node in nodes],
                instances=instances,
                logging=log_stream.getvalue()
            )

        if state == 'absent':
            changed = unprovision(provisioner, instances)
            module.exit_json(
                changed=changed,
                msg="{} nodes unprovisioned".format(len(instances)),
                logging=log_stream.getvalue()
            )
    except Exception as e:
        module.fail_json(
            msg=str(e),
            logging=log_stream.getvalue()
        )


if __name__ == '__main__':
    main()
