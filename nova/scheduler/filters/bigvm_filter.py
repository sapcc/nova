from oslo_log import log as logging

import nova.conf
from nova.scheduler import filters

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF


class BigVmFilter(filters.BaseHostFilter):

    RUN_ON_REBUILD = False

    def host_passes(self, host_state, spec_obj):
        requested_ram_mb = spec_obj.memory_mb
        if requested_ram_mb < CONF.bigvm_mb:
            return True

        free_ram_mb = host_state.free_ram_mb
        total_usable_ram_mb = host_state.total_usable_ram_mb
        used_ram_mb = total_usable_ram_mb - free_ram_mb
        used_ram_percent = used_ram_mb / total_usable_ram_mb * 100
        max_ram_percent = CONF.scheduler.bigvm_memory_utilization_max

        if used_ram_percent > max_ram_percent:
            LOG.debug("%(host_state)s does not have less than "
                      "%(max_ram_percent)s % RAM utilization (has "
                      "%(used_ram_percent)s %) and is thus not suitable "
                      "for big VMs.",
                      {'host_state': host_state,
                       'max_ram_percent': max_ram_percent,
                       'used_ram_percent': used_ram_percent})
            return False

        return True
