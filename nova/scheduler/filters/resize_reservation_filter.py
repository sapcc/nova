# Copyright (c) 2020 SAP SE
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

from oslo_log import log as logging

import nova.conf
from nova.scheduler import filters
from nova.scheduler import utils

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF


class ResizeReservedHostRAMFilter(filters.BaseHostFilter):
    """Only return hosts for resize with sufficient available RAM."""

    RUN_ON_REBUILD = False

    def reserved_percent_ram(self):
        return CONF.filter_scheduler.resize_threshold_reserved_ram_percent

    def host_passes(self, host_state, request_spec):
        if not utils.request_is_resize(request_spec):
            return True

        reserved_ram_percent = self.reserved_percent_ram() / 100.0
        reserved_ram = host_state.total_usable_ram_mb * reserved_ram_percent

        # Apply reserved memory to total and free memory
        host_state.total_usable_ram_mb -= reserved_ram
        free_ram_mb = host_state.free_ram_mb - reserved_ram

        memory_mb_limit = (
            host_state.total_usable_ram_mb * host_state.ram_allocation_ratio
        )
        used_ram_mb = host_state.total_usable_ram_mb - free_ram_mb
        usable_ram = memory_mb_limit - used_ram_mb

        # Do not allow an instance to overcommit against itself, only against
        # other instances.
        if request_spec.memory_mb >= host_state.total_usable_ram_mb:
            LOG.debug("%(host_state)s does not have %(requested_ram_mb)s MB "
                      "usable ram before overcommit, it only has "
                      "%(usable_ram)s MB.",
                      {'host_state': host_state,
                       'requested_ram_mb': request_spec.memory_mb,
                       'usable_ram': host_state.total_usable_ram_mb})
            return False

        if request_spec.memory_mb >= usable_ram:
            LOG.info("%(host_state)s does not have %(requested_ram_mb)s MB "
                     "usable ram, it only has %(usable_ram)s MB usable ram.",
                     {'host_state': host_state,
                      'requested_ram_mb': request_spec.memory_mb,
                      'usable_ram': usable_ram})
            return False

        # save oversubscription limit for compute node to test against:
        host_state.limits['memory_mb'] = memory_mb_limit
        return True
