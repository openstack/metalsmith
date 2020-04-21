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

import collections
import json
import sys

import prettytable

from metalsmith import _utils


def _print(msg, **kwargs):
    print(msg % kwargs)


class NullFormat(object):
    """Formatting outputting nothing.

    Used implicitly with --quiet.
    """

    def __init__(self, columns=None, sort_column=None):
        self.columns = columns
        self.sort_column = sort_column

    def deploy(self, instance):
        pass

    def undeploy(self, node):
        pass


FIELDS = ['UUID', 'Node Name', 'Allocation UUID', 'Hostname',
          'State', 'IP Addresses']


class ValueFormat(NullFormat):
    """"Simple value formatter."""

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

    def _iter_rows(self, instances):
        for instance in instances:
            if instance.is_deployed:
                ips = '\n'.join('%s=%s' % (net, ','.join(ips))
                                for net, ips in
                                instance.ip_addresses().items())
            else:
                ips = ''
            row = [instance.uuid, instance.node.name or '',
                   instance.allocation.id if instance.allocation else '',
                   instance.hostname or '', instance.state.name, ips]
            yield row

    def show(self, instances):
        allowed_columns = set(self.columns or FIELDS)
        rows = (collections.OrderedDict(zip(FIELDS, row))
                for row in self._iter_rows(instances))
        if self.sort_column:
            rows = sorted(rows, key=lambda row: row.get(self.sort_column))
        for row in rows:
            _print(' '.join(value if value is not None else ''
                            for key, value in row.items()
                            if key in allowed_columns))


class DefaultFormat(ValueFormat):
    """Human-readable formatter."""

    def show(self, instances):
        if not instances:
            _print('')  # Compatibility with openstackclient - one empty line
            return

        pt = prettytable.PrettyTable(field_names=FIELDS)
        pt.align = 'l'
        if self.sort_column:
            pt.sortby = self.sort_column

        for row in self._iter_rows(instances):
            pt.add_row(row)

        if self.columns:
            value = pt.get_string(fields=self.columns)
        else:
            value = pt.get_string()
        _print(value)


class JsonFormat(NullFormat):
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
    'default': DefaultFormat,
    'json': JsonFormat,
    'table': DefaultFormat,
    'value': ValueFormat,
}
"""Available formatters."""


DEFAULT_FORMAT = 'table'
"""Default formatter."""


NULL_FORMAT = NullFormat()
"""Formatter outputting nothing."""
