# Copyright (c) 2012 OpenStack Foundation
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
from oslo_config import cfg

from nova.i18n import _LW
from nova import objects
from nova.scheduler import filters
from nova import servicegroup
from nova.context import get_admin_context
from nova.db.api import aggregate_get_by_host

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

class BigFlavorFilter(filters.BaseHostFilter):
    """Filter for specific amount of VM's from a specific custom flavor
       Flavor name to check is registered in `nova.conf`
    """

    RUN_ON_REBUILD = False

    def __init__(self):
        self.servicegroup_api = servicegroup.API()
        self._context = None

    # Host state does not change within a request
    run_filter_once_per_request = True

    def host_passes(self, host_state, spec_obj):
        self._context = self._context or get_admin_context()
        flavor_check = self._schedule_big_flavor(self._context, spec_obj,
                                                 host_state.host)

        if flavor_check == False:
            return False

        return True

    def get_flavor_quota_limit(self, context, host):

        aggregate_list = objects.AggregateList.get_by_host(context, host)
        flavor_quota = None

        for aggr in aggregate_list:
            if aggr.hosts[0] == host:
                flavor_quota = aggr.metadata['flavor_quota']
        return flavor_quota

    def _schedule_big_flavor(self, context, spec_obj, host):
        instance_list = objects.InstanceList.get_by_host(context, host)

        flavor_quota_limit = self.get_flavor_quota_limit(context, host)
        flavor_name = spec_obj.flavor.name
        big_flavor_quota = 0
        for i in instance_list:
            if i.get_flavor().name == CONF.big_vm_flavor:
                big_flavor_quota += 1
        if flavor_name == CONF.big_vm_flavor:
            if big_flavor_quota >= \
                    int(flavor_quota_limit):
               return False
        else:
            LOG.info("Flavor used is %s. Skipping flavor filtering" %
                                                            flavor_name)

        return True