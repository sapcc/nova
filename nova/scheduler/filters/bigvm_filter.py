# Copyright (c) 2019 OpenStack Foundation
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
from nova.scheduler.filters import utils

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

_AGGREGATE_KEY = 'hv_size_mb'


class BigVmBaseFilterException(Exception):
    pass


class BigVmBaseFilter(filters.BaseHostFilter):

    RUN_ON_REBUILD = False

    def _get_hv_size(self, host_state):
        # get the hypervisor size from the host aggregate
        metadata = utils.aggregate_metadata_get_by_host(host_state)
        aggregate_vals = metadata.get(_AGGREGATE_KEY, None)
        if not aggregate_vals:
            LOG.error("%(host_state)s does not have %(aggregate_key)s set so "
                      "probably isn't assigned to a hypervisor-size host "
                      "aggregate, which prevents big VM scheduling.",
                      {'host_state': host_state,
                       'aggregate_key': _AGGREGATE_KEY})
            raise BigVmBaseFilterException

        # there should be only one value anyways ...
        aggregate_val = list(aggregate_vals)[0]
        try:
            hypervisor_ram_mb = int(aggregate_val)
        except ValueError:
            LOG.error("%(host_state)s has an invalid value for "
                      "%(aggregate_key)s: %(aggregate_val)s. Only "
                      "integers are supported.",
                      {'host_state': host_state,
                       'aggregate_key': _AGGREGATE_KEY,
                       'aggregate_val': aggregate_val})
            raise BigVmBaseFilterException
        return hypervisor_ram_mb


class BigVmClusterUtilizationFilter(BigVmBaseFilter):
    """Only schedule big VMs to a vSphere cluster (i.e. nova-compute host) if
    the memory-utilization of the cluster is below a threshold depending on the
    hypervisor size and the requested memory.
    """

    def _get_max_ram_percent(self, requested_ram_mb, hypervisor_ram_mb):
        """We want the hosts to have on average half the requested memory free.
        """
        requested_ram_mb = float(requested_ram_mb)
        hypervisor_ram_mb = float(hypervisor_ram_mb)
        hypervisor_max_used_ram_mb = hypervisor_ram_mb - requested_ram_mb / 2
        return hypervisor_max_used_ram_mb / hypervisor_ram_mb * 100

    def host_passes(self, host_state, spec_obj):
        requested_ram_mb = spec_obj.memory_mb
        # not scheduling a big VM -> every host is fine
        if requested_ram_mb < CONF.bigvm_mb:
            return True

        free_ram_mb = host_state.free_ram_mb
        total_usable_ram_mb = host_state.total_usable_ram_mb
        used_ram_mb = total_usable_ram_mb - free_ram_mb
        used_ram_percent = float(used_ram_mb) / total_usable_ram_mb * 100.0

        try:
            hypervisor_ram_mb = self._get_hv_size(host_state)
        except BigVmBaseFilterException:
            return False

        max_ram_percent = self._get_max_ram_percent(requested_ram_mb,
                                                    hypervisor_ram_mb)

        if used_ram_percent > max_ram_percent:
            LOG.info("%(host_state)s does not have less than "
                      "%(max_ram_percent)s %% RAM utilization (has "
                      "%(used_ram_percent)s %%) and is thus not suitable "
                      "for big VMs.",
                      {'host_state': host_state,
                       'max_ram_percent': max_ram_percent,
                       'used_ram_percent': used_ram_percent})
            return False

        return True


class BigVmHypervisorRamFilter(BigVmBaseFilter):

    def host_passes(self, host_state, spec_obj):
        """Check if a big VM actually fits on the hypervisor"""
        requested_ram_mb = spec_obj.memory_mb

        # ignore normal VMs
        if requested_ram_mb < CONF.bigvm_mb:
            return True

        # get the aggregate
        try:
            hypervisor_ram_mb = self._get_hv_size(host_state)
        except BigVmBaseFilterException:
            return False

        # check the VM fits
        if requested_ram_mb > hypervisor_ram_mb:
            LOG.debug("%(host_state)s does not have the hypervisor size to "
                      "support %(requested_ram_mb)s MB VMs. It only supports "
                      "up to %(hypervisor_ram_mb)s MB.",
                      {'host_state': host_state,
                       'requested_ram_mb': requested_ram_mb,
                       'hypervisor_ram_mb': hypervisor_ram_mb})
            return False

        return True
