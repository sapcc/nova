# Copyright (c) 2014 OpenStack Foundation
#
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
from .exact_core_filter import ExactCoreFilter

LOG = logging.getLogger(__name__)


class BaremetalExactCoreFilter(ExactCoreFilter):
    """Exact Core Filter."""

    def host_passes(self, host_state, spec_obj):
        extra_specs = spec_obj.flavor.extra_specs
        if not 'capabilities:cpu_arch' in extra_specs:
            return True

        return super(BaremetalExactCoreFilter, self).host_passes(host_state, spec_obj)
