from oslo_log import log as logging
from oslo_utils import timeutils

from nova.compute.monitors import base
import nova.conf
from nova import exception
from nova.i18n import _LE

CONF = nova.conf.CONF
LOG = logging.getLogger(__name__)


class Monitor(base.LocalStorageMonitorBase):
    def __init__(self, resource_tracker):
        super(Monitor, self).__init__(resource_tracker)
        self.source = CONF.compute_driver
        self.driver = resource_tracker.driver
        self._data = {}
        self._storage_stats = {}

    def get_metrics(self):
        LOG.debug("GETTING LOCAL STORAGE METRICS")
        metrics = []
        self._update_data()
        for name in self.get_metric_names():
            metrics.append((name, self._data[name], self._data["timestamp"]))
        return metrics

    def _update_data(self):
        self._data = {}
        self._data["timestamp"] = timeutils.utcnow()

        # Extract node's CPU statistics.
        try:
            #stats = self.driver.get_host_stats()
            stats = self.driver.get_available_resource()
            self._data["storage.percent.usage"] = stats["user"]
            self._data["storage.total"] = stats["local_gb"]
            self._data["storage.used"] = stats["local_gb_used"]
        except (NotImplementedError, TypeError, KeyError):
            LOG.exception(_LE("Not all properties needed are implemented "
                              "in the compute driver"))
            raise exception.ResourceMonitorError(
                monitor=self.__class__.__name__)

        # The compute driver API returns the absolute values for CPU times.
        # We compute the utilization percentages for each specific CPU time
        # after calculating the delta between the current reading and the
        # previous reading.
        stats["total"] = (stats["user"] + stats["kernel"]
                          + stats["idle"] + stats["iowait"])
        cputime = float(stats["total"] - self._cpu_stats.get("total", 0))

        # NOTE(jwcroppe): Convert all the `perc` values to their integer forms
        # since pre-conversion their values are within the range [0, 1] and the
        # objects.MonitorMetric.value field requires an integer.
        perc = (stats["user"] - self._cpu_stats.get("user", 0)) / cputime
        self._data["cpu.user.percent"] = int(perc * 100)

        perc = (stats["kernel"] - self._cpu_stats.get("kernel", 0)) / cputime
        self._data["cpu.kernel.percent"] = int(perc * 100)

        perc = (stats["idle"] - self._cpu_stats.get("idle", 0)) / cputime
        self._data["cpu.idle.percent"] = int(perc * 100)

        perc = (stats["iowait"] - self._cpu_stats.get("iowait", 0)) / cputime
        self._data["cpu.iowait.percent"] = int(perc * 100)

        # Compute the current system-wide CPU utilization as a percentage.
        used = stats["user"] + stats["kernel"] + stats["iowait"]
        prev_used = (self._cpu_stats.get("user", 0)
                     + self._cpu_stats.get("kernel", 0)
                     + self._cpu_stats.get("iowait", 0))
        perc = (used - prev_used) / cputime
        self._data["cpu.percent"] = int(perc * 100)

        self._cpu_stats = stats.copy()

