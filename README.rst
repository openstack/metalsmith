Deployment and Scheduling tool for Bare Metal
=============================================

Overview
--------

This is a simple tool to provision bare metal machines using `OpenStack Bare
Metal Service (ironic) <https://docs.openstack.org/ironic/latest/>`_,
`OpenStack Image Service (glance) <https://docs.openstack.org/glance/latest/>`_
and `OpenStack Networking Service (neutron)
<https://docs.openstack.org/neutron/latest/>`_.

* License: Apache License, Version 2.0
* Documentation: https://metalsmith.readthedocs.io
* Source: https://git.openstack.org/cgit/openstack/metalsmith
* Bugs: https://storyboard.openstack.org/#!/project/1000

Installation
------------

::

    pip install --user metalsmith

CLI Usage
---------

Generic usage is as follows::

    metalsmith --os-cloud <CLOUD NAME> deploy --image <GLANCE IMAGE> \
        --network <NEUTRON NET> --ssh-public-key <PATH TO SSH PUBLIC KEY> \
        --resource-class <RESOURCE CLASS>

This is an example suitable for TripleO (replace ``compute`` with the profile
you want to deploy)::

    source ~/stackrc
    metalsmith deploy --image overcloud-full --network ctlplane \
        --capability profile=compute --ssh-public-key ~/.ssh/id_rsa.pub \
        --resource-class baremetal

To remove the deployed instance::

    metalsmith --os-cloud <CLOUD NAME> undeploy <NODE UUID>

For all possible options see the built-in help::

    metalsmith --help

Contributing
------------

* Pull requests: `Gerrit
  <https://review.openstack.org/#/q/project:openstack/metalsmith>`_
  (see `developer's guide
  <https://docs.openstack.org/infra/manual/developers.html>`_)
* Bugs and RFEs:  `StoryBoard
  <https://storyboard.openstack.org/#!/project/1000>`_
  (please do NOT report bugs to Github)
