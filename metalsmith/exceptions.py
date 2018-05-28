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
    """Failed to reserve a suitable node."""

    def __init__(self, message, requested_resource_class,
                 requested_capabilities):
        super(ReservationFailed, self).__init__(message)
        self.requested_resource_class = requested_resource_class
        self.requested_capabilities = requested_capabilities


class ResourceClassNotFound(ReservationFailed):
    """No nodes match the given resource class."""

    def __init__(self, requested_resource_class, requested_capabilities):
        message = ("No available nodes found with resource class %s" %
                   requested_resource_class)
        super(ResourceClassNotFound, self).__init__(message,
                                                    requested_resource_class,
                                                    requested_capabilities)


class CapabilitiesNotFound(ReservationFailed):
    """Requested capabilities do not match any nodes."""


class ValidationFailed(ReservationFailed):
    """Validation failed for all requested nodes."""


class AllNodesReserved(ReservationFailed):
    """All nodes are already reserved."""

    def __init__(self, requested_resource_class, requested_capabilities):
        message = 'All the candidate nodes are already reserved'
        super(AllNodesReserved, self).__init__(message,
                                               requested_resource_class,
                                               requested_capabilities)


class InvalidImage(Error):
    """Requested image is invalid and cannot be used."""


class InvalidNIC(Error):
    """Requested NIC is invalid and cannot be used."""


class UnknownRootDiskSize(Error):
    """Cannot determine the root disk size."""


class InvalidNode(Error):
    """This node cannot be deployed onto."""


class DeploymentFailure(Error):
    """One or more nodes have failed the deployment.

    :ivar nodes: List of failed nodes.
    """

    def __init__(self, message, nodes):
        self.nodes = nodes
        super(DeploymentFailure, self).__init__(message)
