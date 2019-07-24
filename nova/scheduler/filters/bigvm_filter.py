from oslo_log import log as logging

import nova.conf
from nova.scheduler import filters
from nova.scheduler.filters import utils

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

_AGGREGATE_KEY = 'hv_size_mb'

class BigVmFilter(filters.BaseHostFilter):
    """Only schedule big VMs to a vSphere cluster (i.e. nova-compute host) if
    the memory-utilization of the cluster is below a threshold depending on the
    hypervisor size and the requested memory.
    """

    RUN_ON_REBUILD = False

    def _get_max_ram_percent(self, requested_ram_mb, hypervisor_ram_mb):
        """
        We want the hosts to have on average half the requested memory free.
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

        # get the hypervisor size from the host aggregate
        metadata = utils.aggregate_metadata_get_by_host(host_state)
        aggregate_vals = metadata.get(_AGGREGATE_KEY, None)
        if not aggregate_vals:
            LOG.error("%(host_state)s does not have %(aggregate_key)s set so "
                      "probably isn't assigned to a hypervisor-size host "
                      "aggregate, which prevents big VM scheduling.",
                      {'host_state': host_state,
                       'aggregate_key': _AGGREGATE_KEY})
            return False

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
