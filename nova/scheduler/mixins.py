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
from oslo_cache.backends.dictionary import DictCacheBackend
from oslo_cache import core as cache_core
from oslo_log import log as logging

from nova import context
from nova.scheduler.client import report
from nova import utils as nova_utils

LOG = logging.getLogger(__name__)

_PLACEMENT_CLIENT = None

class HypervisorSizeMixin(object):

    _HV_SIZE_CACHE = DictCacheBackend({'expiration_time': 10 * 60})

    def _update_cache(self, host_uuid):
        """Update the cache for the given host

        We need to keep the _PLACEMENT_CLIENT as singleton, because it uses
        openstacksdk below which - at least in bobcat - leaks `Connection`
        objects.
        """
        global _PLACEMENT_CLIENT

        if _PLACEMENT_CLIENT is None:
            _PLACEMENT_CLIENT = report.SchedulerReportClient()

        elevated = context.get_admin_context()
        res = _PLACEMENT_CLIENT._get_inventory(elevated, host_uuid)
        if not res:
            return
        inventories = res.get('inventories', {})
        hv_size_mb = inventories.get('MEMORY_MB', {}).get('max_unit')
        self._HV_SIZE_CACHE.set(host_uuid, hv_size_mb)

    def _get_hv_size(self, host_state):
        hv_size_mb = self._HV_SIZE_CACHE.get(host_state.uuid)
        if hv_size_mb != cache_core.NO_VALUE:
            return hv_size_mb

        # we inline the function to customize the key with the name of the
        # class we're used as mixin in and the host
        key = (f"update-hv-size-cache-{self.__class__.__name__}"
               f"-{host_state.uuid}")

        @nova_utils.synchronized(key)
        def _synchronized_update_cache(host_uuid):
            # recheck whether another thread already updated our cache
            hv_size_mb = self._HV_SIZE_CACHE.get(host_uuid)
            if hv_size_mb != cache_core.NO_VALUE:
                return

            self._update_cache(host_uuid)

        _synchronized_update_cache(host_state.uuid)
        return self._HV_SIZE_CACHE.get(host_state.uuid) or None
