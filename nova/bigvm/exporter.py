# Copyright 2022 SAP SE
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
from prometheus_client import CollectorRegistry
from prometheus_client import Counter
from prometheus_client import Gauge
from prometheus_client import start_http_server

import nova.conf

CONF = nova.conf.CONF

LOG = logging.getLogger(__name__)

REGISTRY = CollectorRegistry(auto_describe=True)

ERROR_FREEING = 'freeing'


class _BigVmPrometheusMetrics:

    def __init__(self, registry):
        self.host_errors_counter = \
            Counter('nova_bigvm_host_errors',
                    'Counts errors that happened while reconciling '
                    'a host. The "error" is a short code meaning: '
                    'freeing = Error while freeing up a host',
                    labelnames=['error', 'vc', 'host', 'rp'],
                    registry=registry)

        self.no_candidate_error_counter = \
            Counter('nova_bigvm_no_candidate_error',
                    'Counter that increments each time the '
                    'reconciliation loop cannot find a '
                    'resource-provider for freeing-up a host.',
                    labelnames=['hv_size'],
                    registry=registry)

        self.host_freeing_up_gauge = \
            Gauge('nova_bigvm_host_freeing_up',
                  'Gauge for each BigVM host that is currently '
                  'being freed up.',
                  labelnames=['vc', 'host', 'rp'],
                  registry=registry)

        self.free_hosts_count_gauge = \
            Gauge('nova_bigvm_free_hosts_count',
                  'The total amount of available BigVM hosts '
                  'in the region.',
                  labelnames=['hv_size'],
                  registry=registry)

    def bigvm_host_error(self, error, rp):
        self.host_errors_counter.labels(
            error, rp['vc'], rp['host'], rp['rp']['name']).inc()

    def error_freeing(self, rp):
        self.bigvm_host_error(ERROR_FREEING, rp)

    def no_candidate_error(self, hv_size):
        self.no_candidate_error_counter.labels(hv_size).inc()

    def set_freeing_provider(self, rp):
        self.host_freeing_up_gauge.labels(
            rp['vc'], rp['host'], rp['rp']['name']).set(1)

    def remove_freeing_provider(self, rp):
        try:
            self.host_freeing_up_gauge.remove(
                rp['vc'], rp['host'], rp['rp']['name'])
        except KeyError:
            pass

    def set_free_hosts_count(self, hv_size, count):
        self.free_hosts_count_gauge.labels(hv_size).set(count)


bigvm_metrics = _BigVmPrometheusMetrics(REGISTRY)


def start_bigvm_exporter():
    port = CONF.bigvm_exporter_listen_port
    start_http_server(port, registry=REGISTRY)
    LOG.info("Started BigVM prometheus exporter on port %s", port)
