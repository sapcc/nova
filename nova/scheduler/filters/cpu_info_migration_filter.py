# Copyright (c) 2021 SAP SE
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import six

from oslo_log import log as logging
from oslo_serialization import jsonutils

from nova.context import get_admin_context
from nova.exception import ComputeHostNotFound
from nova.objects.compute_node import ComputeNode
from nova.scheduler import filters
from nova.scheduler.utils import request_is_rebuild
from nova.scheduler.utils import request_is_resize

LOG = logging.getLogger(__name__)


class CpuInfoMigrationFilter(filters.BaseHostFilter):
    """Limit for live-migrations the target hosts by having all the
    sources cpu flags.

    We cannot move a VM from a host with more cpu features to one with less
    while running. It does not apply when the VM is stopped
    (E.g. resize/rebuild)

    Implements `filter_all` directly instead of `host_passes`
    """
    # Instance type and host capabilities do not change within a request
    run_filter_once_per_request = True

    def filter_all(self, filter_obj_list, spec_obj):
        source_host = spec_obj.get_scheduler_hint('source_host')
        source_node = spec_obj.get_scheduler_hint('source_node')
        # This filter only applies to live-migration,
        # Not normal builds, resizes, and rebuilds
        if (not source_host or
                not source_node or
                request_is_resize(spec_obj) or
                request_is_rebuild(spec_obj)):
            return filter_obj_list

        try:
            ctx = get_admin_context()
            compute_node = ComputeNode.get_by_host_and_nodename(
                ctx, source_host, source_node)
            source_cpu_flags = self._parse_cpu_info(compute_node.cpu_info)
        except ComputeHostNotFound:
            LOG.warning("Cannot find source host/node %s/%s",
                        source_host, source_node)
            return []
        except (ValueError, KeyError):
            LOG.warning("Cannot parse cpu_info for source host/node %s/%s: %s",
                        source_host, source_node, compute_node.cpu_info)
            return []

        return [host_state
            for host_state in filter_obj_list
            if self._are_cpu_flags_supported(host_state, source_cpu_flags)]

    @staticmethod
    def _parse_cpu_info(cpu_info):
        if isinstance(cpu_info, six.string_types):
            cpu_info = jsonutils.loads(cpu_info)

        return set(cpu_info["features"])

    @staticmethod
    def _are_cpu_flags_supported(host_state, source_cpu_flags):
        """Return if a host supports the given cpu flags."""
        try:
            cpu_flags = CpuInfoMigrationFilter._parse_cpu_info(
                host_state.cpu_info)
            return source_cpu_flags.issubset(cpu_flags)
        except (ValueError, KeyError):
            LOG.warning(
                "Cannot parse cpu_info for target host/node (%s/%s) '%s'",
                host_state.host, host_state.nodename, host_state.cpu_info)
            return False
