Role - metalsmith_deployment
============================

This role deploys instances using **metalsmith** CLI.

Variables
---------

The only required variable is:

``metalsmith_instances``
    list of instances to provision, see Instance_ for instance description.

The following optional variables provide the defaults for Instance_ attributes:

``metalsmith_candidates``
    the default for ``candidates``.
``metalsmith_capabilities``
    the default for ``capabilities``.
``metalsmith_conductor_group``
    the default for ``conductor_group``.
``metalsmith_debug``
    Show extra debug information, defaults to ``false``.
``metalsmith_extra_args``
    the default for ``extra_args``.
``metalsmith_image``
    the default for ``image``.
``metalsmith_image_checksum``
    the default for ``image_checksum``.
``metalsmith_image_kernel``
    the default for ``image_kernel``.
``metalsmith_image_ramdisk``
    the default for ``image_ramdisk``.
``metalsmith_netboot``
    the default for ``netboot``
``metalsmith_nics``
    the default for ``nics``.
``metalsmith_resource_class``
    the default for ``resource_class``.
``metalsmith_root_size``
    the default for ``root_size``.
``metalsmith_ssh_public_keys``
    the default for ``ssh_public_keys``.
``metalsmith_state``
    the default state for instances, valid values are ``reserved``, ``absent``
    or the default value ``present``.
``metalsmith_swap_size``
    the default for ``swap_size``.
``metalsmith_traits``
    the default for ``traits``.
``metalsmith_user_name``
    the default for ``user_name``, the default value is ``metalsmith``.

Instance
--------

Each instances has the following attributes:

``candidates`` (defaults to ``metalsmith_candidates``)
    list of nodes (UUIDs or names) to be considered for deployment.
``capabilities`` (defaults to ``metalsmith_capabilities``)
    node capabilities to request when scheduling.
``config_drive``
    extra data to add to the config-drive generated for this instance:

    ``cloud_config``
        Dict of cloud-init cloud-config tasks to run on node
        boot. The 'users' directive can be used to configure extra
        users other than the 'user_name' admin user.
    ``meta_data``
        Extra metadata to include with the config-drive metadata.
        This will be added to the generated metadata
        ``public_keys``, ``uuid``, ``name``, and ``hostname``.

``conductor_group`` (defaults to ``metalsmith_conductor_group``)
    conductor group to pick nodes from.

    .. note:: Currently it's not possible to specify the default group.

``extra_args`` (defaults to ``metalsmith_extra_args``)
    additional arguments to pass to the ``metalsmith`` CLI on all calls.
    (No longer supported, will raise an error if used)
``image`` (defaults to ``metalsmith_image``)
    UUID, name or HTTP(s) URL of the image to use for deployment. Mandatory.
``image_checksum`` (defaults to ``metalsmith_image_checksum``)
    MD5 checksum or checksum file URL for an HTTP(s) image.
``image_kernel`` (defaults to ``metalsmith_image_kernel``)
    URL of the kernel image if and only if the ``image`` is a URL of
    a partition image.
``image_ramdisk`` (defaults to ``metalsmith_image_ramdisk``)
    URL of the ramdisk image if and only if the ``image`` is a URL of
    a partition image.
``netboot``
    whether to boot the deployed instance from network (PXE, iPXE, etc).
    The default is to use local boot (requires a bootloader on the image).
``nics`` (defaults to ``metalsmith_nics``)
    list of virtual NICs to attach to node's physical NICs. Each is an object
    with exactly one attribute:

    ``network``
        creates a port on the given network, for example:

        .. code-block:: yaml

            nics:
              - network: private
              - network: ctlplane

        can optionally take a fixed IP to assign:

        .. code-block:: yaml

            nics:
              - network: private
                fixed_ip: 10.0.0.2
              - network: ctlplane
                fixed_ip: 192.168.42.30

    ``port``
        uses the provided pre-created port:

        .. code-block:: yaml

            nics:
              - port: b2254316-7867-4615-9fb7-911b3f38ca2a

    ``subnet``
        creates a port on the given subnet, for example:

        .. code-block:: yaml

            nics:
              - subnet: private-subnet1

``resource_class`` (defaults to ``metalsmith_resource_class``)
    requested node's resource class. Mandatory.
``root_size`` (defaults to ``metalsmith_root_size``)
    size of the root partition (in GiB), if partition images are used.

    .. note::
        Also required for whole-disk images due to how the Bare Metal service
        currently works.

``ssh_public_keys`` (defaults to ``metalsmith_ssh_public_keys``)
    list of file names with SSH public keys to put to the node.
``swap_size`` (defaults to ``metalsmith_swap_size``)
    size of the swap partition (in MiB), if partition images are used
    (it's an error to set it for a whole disk image).
``traits``
    list of traits the node should have.
``user_name`` (defaults to ``metalsmith_user_name``)
    name of the user to create on the instance via configdrive. Requires
    cloud-init_ on the image.

.. _cloud-init: https://cloudinit.readthedocs.io/

Example
-------

.. code-block:: yaml

    ---
    - hosts: all
      tasks:
        - include_role:
            name: metalsmith_deployment
          vars:
            metalsmith_image: centos7
            metalsmith_nics:
              - network: ctlplane
            metalsmith_ssh_public_keys:
              - /home/user/.ssh/id_rsa.pub
            metalsmith_instances:
              - hostname: compute-0
                resource_class: compute
                root_size: 100
                swap_size: 4096
                capabilities:
                  boot_mode: uefi
                traits:
                  - CUSTOM_GPU
              - hostname: compute-1
                resource_class: compute
                root_size: 100
                swap_size: 4096
                capabilities:
                  boot_mode: uefi
                user_name: heat-admin
              - hostname: compute-2
                resource_class: compute
                candidates:
                  - e63650f2-4e7d-40b2-8932-f5b0e54698c7
                  - f19d00dd-60e1-46c8-b83c-782b4d291d9e
              - hostname: control-0
                resource_class: control
                capabilities:
                  boot_mode: uefi
                nics:
                  - network: ctlplane
                  - port: 1899af15-149d-47dc-b0dc-a68614eeb5c4
              - hostname: custom-partition-image
                resource_class: custom
                image: https://example.com/images/custom-1.0.root.img
                image_kernel: https://example.com/images/custom-1.0.vmlinuz
                image_ramdisk: https://example.com/images/custom-1.0.initrd
                image_checksum: https://example.com/images/MD5SUMS
              - hostname: custom-whole-disk-image
                resource_class: custom
                image: https://example.com/images/custom-1.0.qcow2
                image_checksum: https://example.com/images/MD5SUMS
