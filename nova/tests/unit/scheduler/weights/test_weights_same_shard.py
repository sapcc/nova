# Copyright 2022 SAP
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
Tests for scheduler prefer-same-shard weighers.
"""
import ddt
import mock

from nova import objects
from nova.scheduler import weights
from nova.scheduler.weights import same_shard
from nova import test
from nova.tests.unit.scheduler import fakes
from oslo_utils.fixture import uuidsentinel as uuids


class PreferSameShardOnResizeWeigherTestCase(test.NoDBTestCase):
    def setUp(self):
        super(PreferSameShardOnResizeWeigherTestCase, self).setUp()
        self.weight_handler = weights.HostWeightHandler()
        self.weighers = [same_shard.PreferSameShardOnResizeWeigher()]

        self.instance1 = objects.Instance(host='host1', uuid=uuids.instance_1)
        self.instance2 = objects.Instance(host='host1', uuid=uuids.instance_2)
        self.instance3 = objects.Instance(host='host2', uuid=uuids.instance_3)
        self.instance4 = objects.Instance(host='host3', uuid=uuids.instance_4)
        self.aggs1 = [objects.Aggregate(id=1,
                                        name='az-a',
                                        hosts=['host1', 'host3'],
                                        metadata={}),
                      objects.Aggregate(id=1,
                                        name='vc-a-0',
                                        hosts=['host1', 'host3'],
                                        metadata={})]
        self.aggs2 = [objects.Aggregate(id=1,
                                        name='vc-a-1',
                                        hosts=['host2'],
                                        metadata={})]

        self.hosts = [
            fakes.FakeHostState('host1', 'compute', {'aggregates': self.aggs1,
                'instances': {
                    self.instance1.uuid: self.instance1,
                    self.instance2.uuid: self.instance2,
                }}),
            fakes.FakeHostState('host2', 'compute', {'aggregates': self.aggs2,
                'instances': {
                    self.instance3.uuid: self.instance3,
                }}),
            fakes.FakeHostState('host3', 'compute', {'aggregates': self.aggs1,
                'instances': {
                    self.instance4.uuid: self.instance4,
                }}),
        ]

    def test_prefer_resize_to_same_shard(self):
        self.flags(prefer_same_shard_resize_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            instance_uuid=self.instance1.uuid,
            scheduler_hints=dict(_nova_check_type=[self.weighers[0]._TYPE],
                                 source_host=['host1']))
        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)
        self.assertEqual(1.0, weighed_hosts[0].weight)
        self.assertEqual(1.0, weighed_hosts[1].weight)
        self.assertEqual(0.0, weighed_hosts[2].weight)
        self.assertEqual('host1', weighed_hosts[0].obj.host)
        self.assertEqual('host3', weighed_hosts[1].obj.host)
        self.assertEqual('host2', weighed_hosts[2].obj.host)

    def test_prefer_resize_to_different_shard(self):
        self.flags(prefer_same_shard_resize_weight_multiplier=-1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            instance_uuid=self.instance1.uuid,
            scheduler_hints=dict(_nova_check_type=[self.weighers[0]._TYPE],
                                 source_host=['host1']))
        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)
        self.assertEqual(0.0, weighed_hosts[0].weight)
        self.assertEqual(-1.0, weighed_hosts[1].weight)
        self.assertEqual(-1.0, weighed_hosts[2].weight)
        self.assertEqual('host2', weighed_hosts[0].obj.host)
        self.assertEqual('host1', weighed_hosts[1].obj.host)
        self.assertEqual('host3', weighed_hosts[2].obj.host)

    def test_ignore_scheduling_new_instance(self):
        # Explicitly calling resize with new instance and test
        # should fail as 'source_host' is missing.
        self.flags(prefer_same_shard_resize_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            instance_uuid=uuids.fake_new_instance,
            scheduler_hints={'_nova_check_type': [self.weighers[0]._TYPE]})
        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)
        self.assertEqual(0.0, weighed_hosts[0].weight)
        self.assertEqual(0.0, weighed_hosts[1].weight)
        self.assertEqual(0.0, weighed_hosts[2].weight)

    def test_ignore_rebuilding_instance(self):
        self.flags(prefer_same_shard_resize_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            instance_uuid=self.instance1.uuid,
            scheduler_hints={'_nova_check_type': ['rebuild']})
        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)
        self.assertEqual(0.0, weighed_hosts[0].weight)
        self.assertEqual(0.0, weighed_hosts[1].weight)
        self.assertEqual(0.0, weighed_hosts[2].weight)

    def test_ignore_non_resizing_instance(self):
        self.flags(prefer_same_shard_resize_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            instance_uuid=self.instance1.uuid)
        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)
        self.assertEqual(0.0, weighed_hosts[0].weight)
        self.assertEqual(0.0, weighed_hosts[1].weight)
        self.assertEqual(0.0, weighed_hosts[2].weight)

    @mock.patch('nova.scheduler.utils.is_non_vmware_spec', return_value=True)
    def test_ignore_non_vmware_instance(self, mock_is_non_vmware_spec):
        self.flags(prefer_same_shard_resize_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            scheduler_hints=dict(_nova_check_type=[self.weighers[0]._TYPE],
                                 source_host=['host1']))
        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)
        mock_is_non_vmware_spec.assert_has_calls(
            len(self.hosts) * [mock.call(request_spec)])
        self.assertEqual(0.0, weighed_hosts[0].weight)
        self.assertEqual(0.0, weighed_hosts[1].weight)
        self.assertEqual(0.0, weighed_hosts[2].weight)


@ddt.ddt
class PreferSameShardOnLiveMigrateWeigherTestCase(test.NoDBTestCase):

    def setUp(self):
        super().setUp()
        self.weight_handler = weights.HostWeightHandler()
        self.weighers = [same_shard.PreferSameShardOnLiveMigrateWeigher()]
        # we need at least two for the weigher to be called
        self.hosts = [
            fakes.FakeHostState('host1', 'compute', {}),
            fakes.FakeHostState('host2', 'compute', {})]

    @mock.patch('nova.scheduler.utils.is_non_vmware_spec', return_value=True)
    @ddt.data('resize', 'migrate', 'rebuild', None)
    def test_ignore_non_live_migrating_instance(self, type_, mock_vmware):
        """our first step is checking the request-spec type matches"""
        self.flags(prefer_same_shard_live_migrate_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(scheduler_hints={})
        if type_ is not None:
            request_spec.scheduler_hints['_nova_check_type'] = [type_]

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        # make sure we bail before in the type check
        mock_vmware.assert_not_called()
        self.assertEqual(0.0, weighed_hosts[0].weight)

    def test_live_migrating_instance_same_shard(self):
        """in the same shard we should get a high weight"""
        self.hosts[0].aggregates = [
            objects.Aggregate(id=1, name='vc-a-0', hosts=['host1', 'host2'],
                              metadata={})]
        self.flags(prefer_same_shard_live_migrate_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            scheduler_hints={
                '_nova_check_type': [self.weighers[0]._TYPE],
                'source_host': ['host2']},
            flavor=objects.Flavor(extra_specs={}))

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        self.assertEqual(1.0, weighed_hosts[0].weight)

    def test_live_migrating_instance_other_shard(self):
        """in another shard we should get a low weight"""
        self.hosts[0].aggregates = [
            objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'],
                              metadata={})]
        objects.Instance(host='host2', uuid=uuids.instance1)
        self.flags(prefer_same_shard_live_migrate_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            scheduler_hints={
                '_nova_check_type': [self.weighers[0]._TYPE],
                'source_host': ['host2']},
            flavor=objects.Flavor(extra_specs={}))

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        self.assertEqual(0.0, weighed_hosts[0].weight)

    def test_weight_taken_into_account(self):
        """if we change the weight, a different weight is returned"""
        self.flags(prefer_same_shard_live_migrate_weight_multiplier=-1.0,
                   group='filter_scheduler')
        self.hosts[0].aggregates = [
            objects.Aggregate(id=1, name='vc-a-0', hosts=['host1', 'host2'],
                              metadata={})]
        request_spec = objects.RequestSpec(
            scheduler_hints={
                '_nova_check_type': [self.weighers[0]._TYPE],
                'source_host': ['host2']},
            flavor=objects.Flavor(extra_specs={}))

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        self.assertEqual((-1.0, 'host1'),
                         (weighed_hosts[1].weight, weighed_hosts[1].obj.host))


@ddt.ddt
class PreferSameShardOnMigrateWeigherTestCase(test.NoDBTestCase):

    def setUp(self):
        super().setUp()
        self.weight_handler = weights.HostWeightHandler()
        self.weighers = [same_shard.PreferSameShardOnMigrateWeigher()]
        # we need at least two for the weigher to be called
        self.hosts = [
            fakes.FakeHostState('host1', 'compute', {}),
            fakes.FakeHostState('host2', 'compute', {})]

    @mock.patch('nova.scheduler.utils.is_non_vmware_spec', return_value=True)
    @ddt.data('resize', 'live_migrate', 'rebuild', None)
    def test_ignore_non_migrating_instance(self, type_, mock_vmware):
        """our first step is checking the request-spec type matches"""
        self.flags(prefer_same_shard_migrate_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(scheduler_hints={})
        if type_ is not None:
            request_spec.scheduler_hints['_nova_check_type'] = [type_]

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        # make sure we bail before in the type check
        mock_vmware.assert_not_called()
        self.assertEqual(0.0, weighed_hosts[0].weight)

    def test__migrating_instance_same_shard(self):
        """in the same shard we should get a high weight"""
        self.flags(prefer_same_shard_migrate_weight_multiplier=1.0,
                   group='filter_scheduler')
        self.hosts[0].aggregates = [
            objects.Aggregate(id=1, name='vc-a-0', hosts=['host1', 'host2'],
                              metadata={})]
        request_spec = objects.RequestSpec(
            scheduler_hints={
                '_nova_check_type': [self.weighers[0]._TYPE],
                'source_host': ['host2']},
            flavor=objects.Flavor(extra_specs={}))

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        self.assertEqual(1.0, weighed_hosts[0].weight)

    def test_migrating_instance_other_shard(self):
        """in another shard we should get a low weight"""
        self.hosts[0].aggregates = [
            objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'],
                              metadata={})]
        objects.Instance(host='host2', uuid=uuids.instance1)
        self.flags(prefer_same_shard_migrate_weight_multiplier=1.0,
                   group='filter_scheduler')
        request_spec = objects.RequestSpec(
            scheduler_hints={
                '_nova_check_type': [self.weighers[0]._TYPE],
                'source_host': ['host2']},
            flavor=objects.Flavor(extra_specs={}))

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        self.assertEqual(0.0, weighed_hosts[0].weight)

    def test_weight_taken_into_account(self):
        """if we change the weight, a different weight is returned"""
        self.flags(prefer_same_shard_migrate_weight_multiplier=-1.0,
                   group='filter_scheduler')
        self.hosts[0].aggregates = [
            objects.Aggregate(id=1, name='vc-a-0', hosts=['host1', 'host2'],
                              metadata={})]
        request_spec = objects.RequestSpec(
            scheduler_hints={
                '_nova_check_type': [self.weighers[0]._TYPE],
                'source_host': ['host2']},
            flavor=objects.Flavor(extra_specs={}))

        weighed_hosts = self.weight_handler.get_weighed_objects(
            self.weighers, self.hosts, request_spec)

        self.assertEqual((-1.0, 'host1'),
                         (weighed_hosts[1].weight, weighed_hosts[1].obj.host))
