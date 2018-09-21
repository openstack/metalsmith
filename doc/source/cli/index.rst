metalsmith CLI
==============

Deploy Command
--------------

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

Undeploy Command
----------------

To remove the deployed instance::

    metalsmith --os-cloud <CLOUD NAME> undeploy <NODE UUID>

See Also
--------

For all possible options see the built-in help::

    metalsmith --help
