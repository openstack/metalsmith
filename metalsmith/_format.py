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

from __future__ import print_function

import json
import sys

from metalsmith import _utils


def _print(msg, **kwargs):
    print(msg % kwargs)


class NullFormat(object):
    """Formatting outputting nothing.

    Used implicitly with --quiet.
    """

    def deploy(self, instance):
        pass

    def undeploy(self, node):
        pass


class DefaultFormat(object):
    """Human-readable formatter."""

    def deploy(self, instance):
        """Output result of the deploy."""
        self.show([instance])

    def undeploy(self, node):
        """Output result of undeploy."""
        if node.provision_state == 'available':
            message = "Successfully unprovisioned node %(node)s"
        else:
            message = "Unprovisioning started for node %(node)s"

        _print(message, node=_utils.log_res(node))

    def show(self, instances):
        for instance in instances:
            _print("Node %(node)s, current state is %(state)s",
                   node=_utils.log_res(instance.node),
                   state=instance.state.name)

            if instance.hostname:
                _print('* Hostname: %(hostname)s', hostname=instance.hostname)

            if instance.is_deployed:
                ips = instance.ip_addresses()
                if ips:
                    ips = '; '.join('%s=%s' % (net, ','.join(ips))
                                    for net, ips in ips.items())
                    _print('* IP addresses: %(ips)s', ips=ips)


class JsonFormat(object):
    """JSON formatter."""

    def deploy(self, instance):
        """Output result of the deploy."""
        json.dump(instance.to_dict(), sys.stdout)

    def undeploy(self, node):
        """Output result of undeploy."""
        result = {
            'node': node.to_dict()
        }
        json.dump(result, sys.stdout)

    def show(self, instances):
        """Output instance statuses."""
        json.dump({instance.hostname: instance.to_dict()
                   for instance in instances}, sys.stdout)


FORMATS = {
    'default': DefaultFormat(),
    'json': JsonFormat()
}
"""Available formatters."""


DEFAULT_FORMAT = 'default'
"""Default formatter."""


NULL_FORMAT = NullFormat()
"""Formatter outputting nothing."""
