Metalsmith Deployment
=====================

This role deploys instances using **metalsmith** CLI.

Variables
---------

The only required variable is:

``metalsmith_instances``
    list of instances to provision, see Instance_ for instance description.

The following optional variables provide the defaults for Instance_ attributes:

``metalsmith_extra_args``
    the default for ``extra_args``.
``metalsmith_image``
    the default for ``image``.
``metalsmith_capabilities``
    the default for ``capabilities``.
``metalsmith_nics``
    the default for ``nics``.
``metalsmith_resource_class``
    the default for ``resource_class``.
``metalsmith_root_size``
    the default for ``root_size``.
``metalsmith_ssh_public_keys``
    the default for ``ssh_public_keys``.

Instance
--------

Each instances has the following attributes:

``extra_args`` (defaults to ``metalsmith_extra_args``)
    additional arguments to pass to the ``metalsmith`` CLI on all calls.
``image`` (defaults to ``metalsmith_image``)
    UUID or name of the image to use for deployment. Mandatory.
``capabilities`` (defaults to ``metalsmith_capabilities``)
    node capabilities to request when scheduling.
``nics`` (defaults to ``metalsmith_nics``)
    list of virtual NICs to attach to node's physical NICs. Each is an object
    with exactly one attribute:

    ``network``
        creates a port on the given network, for example:

        .. code-block:: yaml

            nics:
              - network: private
              - network: ctlplane

    ``port``
        uses the provided pre-created port:

        .. code-block:: yaml

            nics:
              - port: b2254316-7867-4615-9fb7-911b3f38ca2a

``resource_class`` (defaults to ``metalsmith_resource_class``)
    requested node's resource class. Mandatory.
``root_size`` (defaults to ``metalsmith_root_size``)
    size of the root partition, if partition images are used.

    .. note::
        Also required for whole-disk images due to how the Bare Metal service
        currently works.

``ssh_public_keys`` (defaults to ``metalsmith_ssh_public_keys``)
    list of file names with SSH public keys to put to the node.

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
                capabilities:
                  boot_mode: uefi
              - hostname: compute-1
                resource_class: compute
                root_size: 100
                capabilities:
                  boot_mode: uefi
              - hostname: control-0
                resource_class: control
                capabilities:
                  boot_mode: uefi
                nics:
                  - network: ctlplane
                  - port: 1899af15-149d-47dc-b0dc-a68614eeb5c4
