Deployment and Scheduling tool for Bare Metal
=============================================

.. image:: https://travis-ci.org/dtantsur/metalsmith.svg?branch=master
    :target: https://travis-ci.org/dtantsur/metalsmith

Overview
--------

This is a simple tool to provision bare metal machines using `OpenStack Bare
Metal Service (ironic) <https://docs.openstack.org/ironic/latest/>`_,
`OpenStack Image Service (glance) <https://docs.openstack.org/glance/latest/>`_
and `OpenStack Networking Service (neutron)
<https://docs.openstack.org/neutron/latest/>`_.

Usage
-----

Start with sourcing your OpenStack credentials, for example::

    . ~/stackrc

Generic usage is as follows::

    metalsmith deploy --image <GLANCE IMAGE> --network <NEUTRON NET> \
        --ssh-public-key <PATH TO SSH PUBLIC KEY> <RESOURCE CLASS>

This is an example suitable for TripleO (replace ``compute`` with the profile
you want to deploy)::

    metalsmith deploy --image overcloud-full --network ctlplane \
        --capability profile=compute --ssh-public-key ~/.ssh/id_rsa.pub baremetal

To remove the deployed instance::

    metalsmith undeploy <NODE UUID>

For all possible options see the built-in help::

    metalsmith --help
