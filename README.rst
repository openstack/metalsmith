Deployment and Scheduling tool for Bare Metal
=============================================

.. image:: https://governance.openstack.org/badges/metalsmith.svg
    :target: https://governance.openstack.org/reference/tags/index.html

Overview
--------

This is a simple tool to provision bare metal machines using `OpenStack Bare
Metal Service (ironic) <https://docs.openstack.org/ironic/latest/>`_ and,
optionally, `OpenStack Image Service (glance)
<https://docs.openstack.org/glance/latest/>`_ and `OpenStack Networking
Service (neutron) <https://docs.openstack.org/neutron/latest/>`_.

* License: Apache License, Version 2.0
* Documentation: https://docs.openstack.org/metalsmith/
* Source: https://opendev.org/openstack/metalsmith
* Bugs: https://storyboard.openstack.org/#!/project/openstack/metalsmith

Installation
------------

::

    pip install --user metalsmith

.. note::
    The current versions of *metalsmith* require Bare Metal API from the Stein
    release or newer. Use the 0.11 release series for older versions.

Contributing
------------

* Pull requests: `Gerrit
  <https://review.openstack.org/#/q/project:openstack/metalsmith>`_
  (see `developer's guide
  <https://docs.openstack.org/infra/manual/developers.html>`_)
* Bugs and RFEs:  `StoryBoard
  <https://storyboard.openstack.org/#!/project/openstack/metalsmith>`_
  (please do NOT report bugs to Github)
