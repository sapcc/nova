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

import mock

from nova import objects
from nova.scheduler import weights
from nova.scheduler.weights import affinity
from nova import test
from nova.tests.unit.scheduler import fakes


class SoftWeigherTestBase(test.NoDBTestCase):

    def setUp(self):
        super(SoftWeigherTestBase, self).setUp()
        self.weight_handler = weights.HostWeightHandler()
        self.weighers = []

    def _get_weighed_host(self, hosts, policy, group='default',
                          expected_host=None, match_host=False):
        if group == 'default':
            members = ['member1', 'member2', 'member3', 'member4', 'member5',
                'member6', 'member7']
        else:
            members = ['othermember1', 'othermember2']
        request_spec = objects.RequestSpec(
            instance_group=objects.InstanceGroup(
                policy=policy,
                members=members,
                hosts=[h.host for h in hosts]))
        hosts = self.weight_handler.get_weighed_objects(self.weighers,
                                                        hosts,
                                                        request_spec)
        if not match_host:
            return hosts[0]
        else:
            for host in hosts:
                if host.obj.host == expected_host:
                    return host

    def _get_all_hosts(self):
        aggs1 = [objects.Aggregate(id=1, name='vc-a-1', hosts=['host1'])]
        aggs2 = [objects.Aggregate(id=2, name='vc-a-2', hosts=['host3']),
                 objects.Aggregate(id=2, name='vc-a-3', hosts=['host4'])]
        host_values = [
            ('host1', 'node1', {'instances': {
                'member1': mock.sentinel,
                'instance13': mock.sentinel
            }, 'aggregates': aggs1}),
            ('host2', 'node2', {'instances': {
                'member2': mock.sentinel,
                'member3': mock.sentinel,
                'member4': mock.sentinel,
                'member5': mock.sentinel,
                'othermember1': mock.sentinel,
                'othermember2': mock.sentinel,
                'instance14': mock.sentinel
            }}),
            ('host3', 'node3', {'instances': {
                'instance15': mock.sentinel
            }, 'aggregates': aggs2}),
            ('host4', 'node4', {'instances': {
                'member6': mock.sentinel,
                'member7': mock.sentinel,
                'instance16': mock.sentinel
            }, 'aggregates': aggs2})]
        return [fakes.FakeHostState(host, node, values)
                for host, node, values in host_values]

    def _do_test(self, policy, expected_weight, expected_host,
                 group='default'):
        hostinfo_list = self._get_all_hosts()
        weighed_host = self._get_weighed_host(hostinfo_list,
                                              policy, group)
        self.assertEqual(expected_weight, weighed_host.weight)
        if expected_host:
            self.assertEqual(expected_host, weighed_host.obj.host)


class SoftAffinityWeigherTestCase(SoftWeigherTestBase):

    def setUp(self):
        super(SoftAffinityWeigherTestCase, self).setUp()
        self.weighers = [affinity.ServerGroupSoftAffinityWeigher()]

    def test_soft_affinity_weight_multiplier_by_default(self):
        self._do_test(policy='soft-affinity',
                      expected_weight=1.0,
                      expected_host='host2')

    def test_soft_affinity_weight_multiplier_zero_value(self):
        # We do not know the host, all have same weight.
        self.flags(soft_affinity_weight_multiplier=0.0,
                   group='filter_scheduler')
        self._do_test(policy='soft-affinity',
                      expected_weight=0.0,
                      expected_host=None)

    def test_soft_affinity_weight_multiplier_positive_value(self):
        self.flags(soft_affinity_weight_multiplier=2.0,
                   group='filter_scheduler')
        self._do_test(policy='soft-affinity',
                      expected_weight=2.0,
                      expected_host='host2')

    def test_soft_affinity_weight_multiplier_same_shards(self):
        """For host, which does not contain servers of server-group,
        but in same shard as the servers in the server-group, weight
        is 0.5. Due to normalization smallest weight become 0.0
        """
        self.flags(soft_affinity_weight_multiplier=2.0,
                   group='filter_scheduler')
        expected_weight = 0.0
        hostinfo_list = self._get_all_hosts()
        weighed_host = self._get_weighed_host(hostinfo_list,
            policy='soft-affinity', group='default',
            expected_host='host3', match_host=True)
        self.assertEqual(expected_weight, weighed_host.weight)

    @mock.patch.object(affinity, 'LOG')
    def test_soft_affinity_weight_multiplier_negative_value(self, mock_log):
        self.flags(soft_affinity_weight_multiplier=-1.0,
                   group='filter_scheduler')
        self._do_test(policy='soft-affinity',
                      expected_weight=0.0,
                      expected_host='host3')
        # call twice and assert that only one warning is emitted
        self._do_test(policy='soft-affinity',
                      expected_weight=0.0,
                      expected_host='host3')
        # one from _weigh_object() and two from weight_multiplier()
        self.assertEqual(3, mock_log.warning.call_count)

    def test_running_twice(self):
        """Run the weighing twice for different groups each run

        The first run has a group with more members on the same host than the
        second both. In both cases, most members of their groups are on the
        same host => weight should be maximum (1 with default multiplier).
        """
        self._do_test(policy='soft-affinity',
                      expected_weight=1.0,
                      expected_host='host2')
        self._do_test(policy='soft-affinity',
                      expected_weight=1.0,
                      expected_host='host2',
                      group='other')


class SoftAntiAffinityWeigherTestCase(SoftWeigherTestBase):

    def setUp(self):
        super(SoftAntiAffinityWeigherTestCase, self).setUp()
        self.weighers = [affinity.ServerGroupSoftAntiAffinityWeigher()]

    def test_soft_anti_affinity_weight_multiplier_by_default(self):
        self._do_test(policy='soft-anti-affinity',
                      expected_weight=1.0,
                      expected_host='host3')

    def test_soft_anti_affinity_weight_multiplier_zero_value(self):
        # We do not know the host, all have same weight.
        self.flags(soft_anti_affinity_weight_multiplier=0.0,
                   group='filter_scheduler')
        self._do_test(policy='soft-anti-affinity',
                      expected_weight=0.0,
                      expected_host=None)

    def test_soft_anti_affinity_weight_multiplier_positive_value(self):
        self.flags(soft_anti_affinity_weight_multiplier=2.0,
                   group='filter_scheduler')
        self._do_test(policy='soft-anti-affinity',
                      expected_weight=2.0,
                      expected_host='host3')

    @mock.patch.object(affinity, 'LOG')
    def test_soft_anti_affinity_weight_multiplier_negative_value(self,
                                                                 mock_log):
        self.flags(soft_anti_affinity_weight_multiplier=-1.0,
                   group='filter_scheduler')
        self._do_test(policy='soft-anti-affinity',
                      expected_weight=0.0,
                      expected_host='host2')
        # call twice and assert that only one warning is emitted
        self._do_test(policy='soft-anti-affinity',
                      expected_weight=0.0,
                      expected_host='host2')
        self.assertEqual(1, mock_log.warning.call_count)
