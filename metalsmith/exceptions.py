# Copyright 2018 Red Hat, Inc.
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


class Error(Exception):
    """Base class for Metalsmith errors."""


class ReservationFailed(Error):
    """Failed to reserve a suitable node.

    This is the base class for all reservation failures.
    """


class NodesNotFound(ReservationFailed):
    """Initial nodes lookup returned an empty list.

    :ivar requested_resource_class: Requested resource class.
    :ivar requested_conductor_group: Requested conductor group to pick nodes
        from.
    """

    def __init__(self, resource_class, conductor_group):
        message = "No available nodes%(rc)s found%(cg)s" % {
            'rc': ' with resource class %s' % resource_class
            if resource_class else '',
            'cg': ' in conductor group %s' % (conductor_group or '<default>')
            if conductor_group is not None else ''
        }
        self.requested_resource_class = resource_class
        self.requested_conductor_group = conductor_group
        super(NodesNotFound, self).__init__(message)


class CustomPredicateFailed(ReservationFailed):
    """Custom predicate yielded no nodes.

    :ivar nodes: List of nodes that were checked.
    """

    def __init__(self, message, nodes):
        self.nodes = nodes
        super(CustomPredicateFailed, self).__init__(message)


class CapabilitiesNotFound(ReservationFailed):
    """Requested capabilities do not match any nodes.

    :ivar requested_capabilities: Requested node's capabilities.
    """

    def __init__(self, message, capabilities):
        self.requested_capabilities = capabilities
        super(CapabilitiesNotFound, self).__init__(message)


class InvalidImage(Error):
    """Requested image is invalid and cannot be used."""


class InvalidNIC(Error):
    """Requested NIC is invalid and cannot be used."""


class UnknownRootDiskSize(Error):
    """Cannot determine the root disk size."""


class InvalidNode(Error):
    """This node cannot be deployed onto."""


class DeploymentFailed(Error):
    """Deployment failed."""


class DeploymentTimeout(DeploymentFailed):
    """Timeout during deployment."""


class InstanceNotFound(Error):
    """Instance not found or node doesn't have an instance associated."""


class NetworkResourceNotFound(Error):
    """Network resource, port, network, subnet not found"""


# Deprecated aliases
DeploymentFailure = DeploymentFailed
InvalidInstance = InstanceNotFound
