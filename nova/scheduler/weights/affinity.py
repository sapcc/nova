# Copyright (c) 2015 Ericsson AB
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
"""
Affinity Weighers.  Weigh hosts by the number of instances from a given host.

AffinityWeigher implements the soft-affinity policy for server groups by
preferring the hosts that has more instances from the given group.

AntiAffinityWeigher implements the soft-anti-affinity policy for server groups
by preferring the hosts that has less instances from the given group.

"""
from oslo_config import cfg
from oslo_log import log as logging

from nova.scheduler import weights

CONF = cfg.CONF

LOG = logging.getLogger(__name__)


class _SoftAffinityWeigherBase(weights.BaseHostWeigher):
    policy_name = None

    def _weigh_object(self, host_state, request_spec):
        """Higher weights win."""
        if not request_spec.instance_group:
            return 0

        policy = request_spec.instance_group.policy

        if self.policy_name != policy:
            return 0

        instances = set(host_state.instances.keys())
        members = set(request_spec.instance_group.members)
        member_on_host = instances.intersection(members)

        return len(member_on_host)

    def host_info_requiring_instance_ids(self, request_spec):
        if not request_spec.instance_group:
            return set()

        policy = request_spec.instance_group.policy

        if self.policy_name != policy:
            return set()

        return set(request_spec.instance_group.members)


class ServerGroupSoftAffinityWeigher(_SoftAffinityWeigherBase):
    policy_name = 'soft-affinity'
    warning_sent = False
    _SHARD_PREFIX = 'vc-'

    def weight_multiplier(self):
        if (CONF.filter_scheduler.soft_affinity_weight_multiplier < 0 and
                not self.warning_sent):
            LOG.warning('For the soft_affinity_weight_multiplier only a '
                        'positive value is meaningful as a negative value '
                        'would mean that the affinity weigher would '
                        'prefer non-collocating placement. Future '
                        'versions of nova will restrict the config '
                        'option to values >=0. Update your configuration '
                        'file to mitigate future upgrade issues.')
            self.warning_sent = True

        return CONF.filter_scheduler.soft_affinity_weight_multiplier

    def _weigh_object(self, host_state, request_spec):
        weight = super(ServerGroupSoftAffinityWeigher, self)._weigh_object(
            host_state, request_spec)

        # if the host contained servers from the same group, return that weight
        if weight:
            return weight

        # get shards of the host
        host_shard_aggrs = [aggr for aggr in host_state.aggregates
                            if aggr.name.startswith(self._SHARD_PREFIX)]
        if not host_shard_aggrs:
            LOG.warning('No aggregates found for host %(host)s.',
                        {'host': host_state.host})
            return 0

        if len(host_shard_aggrs) > 1:
            LOG.warning('More than one host aggregates found for '
                        'host %(host)s, selecting first.',
                        {'host': host_state.host})
        host_shard_aggr = host_shard_aggrs[0]

        group_hosts = None
        if request_spec.instance_group and request_spec.instance_group.hosts:
            group_hosts = set(request_spec.instance_group.hosts)
        if not group_hosts:
            return 0
        # group_hosts doesn't contain our host, because otherwise we would
        # have returned a weighed already as we would have instances
        if group_hosts & set(host_shard_aggr.hosts):
            return 0.5
        return 0


class ServerGroupSoftAntiAffinityWeigher(_SoftAffinityWeigherBase):
    policy_name = 'soft-anti-affinity'
    warning_sent = False

    def weight_multiplier(self):
        if (CONF.filter_scheduler.soft_anti_affinity_weight_multiplier < 0 and
                not self.warning_sent):
            LOG.warning('For the soft_anti_affinity_weight_multiplier '
                        'only a positive value is meaningful as a '
                        'negative value would mean that the anti-affinity '
                        'weigher would prefer collocating placement. '
                        'Future versions of nova will restrict the '
                        'config option to values >=0. Update your '
                        'configuration file to mitigate future upgrade '
                        'issues.')
            self.warning_sent = True

        return CONF.filter_scheduler.soft_anti_affinity_weight_multiplier

    def _weigh_object(self, host_state, request_spec):
        weight = super(ServerGroupSoftAntiAffinityWeigher, self)._weigh_object(
            host_state, request_spec)
        return -1 * weight
