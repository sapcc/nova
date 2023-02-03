# Copyright (c) 2019 SAP SE
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
import time

import mock

from nova.db.main import api as main_db_api
from nova import objects
from nova.scheduler.filters import shard_filter
from nova import test
from nova.tests.unit import fake_flavor
from nova.tests.unit import fake_instance
from nova.tests.unit.scheduler import fakes


class TestShardFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestShardFilter, self).setUp()
        self.filt_cls = shard_filter.ShardFilter()
        self.filt_cls._PROJECT_TAG_CACHE = {
            'foo': ['vc-a-0', 'vc-b-0'],
            'last_modified': time.time()
        }
        self.fake_instance = fake_instance.fake_instance_obj(
            mock.sentinel.ctx, expected_attrs=['metadata', 'tags'])
        build_req = objects.BuildRequest()
        build_req.instance_uuid = self.fake_instance.uuid
        build_req.tags = objects.TagList(objects=[])
        build_req.instance = self.fake_instance
        self.fake_build_req = build_req

    @mock.patch('nova.scheduler.filters.shard_filter.'
                'ShardFilter._update_cache')
    def test_get_shards_cache_timeout(self, mock_update_cache):
        def set_cache():
            self.filt_cls._PROJECT_TAG_CACHE = {
                'foo': ['vc-a-1']
            }
        mock_update_cache.side_effect = set_cache

        project_id = 'foo'
        mod = time.time() - self.filt_cls._PROJECT_TAG_CACHE_RETENTION_TIME

        self.assertEqual(self.filt_cls._get_shards(project_id),
                                                   ['vc-a-0', 'vc-b-0'])

        self.filt_cls._PROJECT_TAG_CACHE['last_modified'] = mod
        self.assertEqual(self.filt_cls._get_shards(project_id), ['vc-a-1'])

    @mock.patch('nova.scheduler.filters.shard_filter.'
                'ShardFilter._update_cache')
    def test_get_shards_project_not_included(self, mock_update_cache):
        def set_cache():
            self.filt_cls._PROJECT_TAG_CACHE = {
                'bar': ['vc-a-1', 'vc-b-0']
            }
        mock_update_cache.side_effect = set_cache

        self.assertEqual(self.filt_cls._get_shards('bar'),
                         ['vc-a-1', 'vc-b-0'])
        mock_update_cache.assert_called_once()

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_shard_baremetal_passes(self, agg_mock, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        extra_specs = {'capabilities:cpu_arch': 'x86_64'}
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs'],
                extra_specs=extra_specs))
        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.scheduler.filters.shard_filter.'
                'ShardFilter._update_cache')
    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_shard_project_not_found(self, agg_mock, mock_update_cache,
                                     get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='bar',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))
        self._assert_passes(host, spec_obj, False)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_shard_project_no_shards(self, agg_mock, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        self.filt_cls._PROJECT_TAG_CACHE['foo'] = []
        self._assert_passes(host, spec_obj, False)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_shard_host_no_shard_aggregate(self, agg_mock, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        host = fakes.FakeHostState('host1', 'compute', {})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        agg_mock.return_value = {}
        self._assert_passes(host, spec_obj, False)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_host_no_shards_in_aggregate(self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        self._assert_passes(host, spec_obj, False)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_shard_match_host_shard(self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_shard_do_not_match_host_shard(self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                objects.Aggregate(id=1, name='vc-a-1', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        self._assert_passes(host, spec_obj, False)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_has_multiple_shards_per_az(self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                objects.Aggregate(id=1, name='vc-a-1', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        self.filt_cls._PROJECT_TAG_CACHE['foo'] = ['vc-a-0', 'vc-a-1',
                                                     'vc-b-0']
        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_has_multiple_shards_per_az_resize_same_shard(
            self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1',
                                                                 'host2']),
                objects.Aggregate(id=1, name='vc-a-1', hosts=['host1',
                                                              'host2'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']),
            scheduler_hints=dict(_nova_check_type=['resize'],
                                 source_host=['host2']))

        self.filt_cls._PROJECT_TAG_CACHE['foo'] = ['vc-a-0', 'vc-a-1',
                                                     'vc-b-0']
        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_has_multiple_shards_per_az_resize_other_shard(
            self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1',
                                                                 'host2']),
                objects.Aggregate(id=1, name='vc-a-1', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']),
            instance_uuid=self.fake_build_req.instance_uuid,
            scheduler_hints=dict(_nova_check_type=['resize'],
                                 source_host=['host2']))

        self.filt_cls._PROJECT_TAG_CACHE['foo'] = ['vc-a-0', 'vc-a-1',
                                                     'vc-b-0']
        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_has_sharding_enabled_any_host_passes(
            self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        self.filt_cls._PROJECT_TAG_CACHE['baz'] = ['sharding_enabled']
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                 objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='baz',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))
        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    def test_shard_project_has_sharding_enabled_and_single_shards(
            self, get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        self.filt_cls._PROJECT_TAG_CACHE['baz'] = ['sharding_enabled',
                                                     'vc-a-1']
        aggs = [objects.Aggregate(id=1, name='some-az-a', hosts=['host1']),
                 objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])]
        host = fakes.FakeHostState('host1', 'compute', {'aggregates': aggs})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='baz',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))
        self._assert_passes(host, spec_obj, True)

    @mock.patch('nova.objects.AggregateList.get_all')
    @mock.patch('nova.context.scatter_gather_skip_cell0')
    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.context.get_admin_context')
    def test_same_shard_for_kubernikus_cluster(self, get_context,
                                               get_by_uuid,
                                               gather_host,
                                               get_aggrs):
        kks_cluster = 'kubernikus:kluster-example'
        build_req = objects.BuildRequest()
        build_req.tags = objects.TagList(objects=[
            objects.Tag(tag=kks_cluster)
        ])
        build_req.instance = self.fake_instance
        get_by_uuid.return_value = build_req

        result = self._filter_k8s_hosts(get_context,
                                        gather_host,
                                        get_aggrs)

        gather_host.assert_called_once_with(
            get_context.return_value,
            main_db_api.get_k8s_hosts_by_instances_tag,
            'kubernikus:kluster-example',
            filters={'hv_type': 'VMware vCenter Server',
                     'availability_zone': 'az-2'})

        self.assertEqual(2, len(result))
        self.assertEqual(result[0].host, 'host4')
        self.assertEqual(result[1].host, 'host5')

    @mock.patch('nova.objects.AggregateList.get_all')
    @mock.patch('nova.context.scatter_gather_skip_cell0')
    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.context.get_admin_context')
    def test_same_shard_for_gardener_cluster(self, get_context,
                                             get_by_uuid,
                                             gather_host,
                                             get_aggrs):
        gardener_cluster = 'kubernetes.io-cluster-shoot--garden--testCluster'
        new_instance = fake_instance.fake_instance_obj(
            get_context.return_value,
            expected_attrs=['metadata'],
            metadata={gardener_cluster: '1'},
            uuid=self.fake_instance.uuid)
        build_req = objects.BuildRequest()
        build_req.instance = new_instance
        build_req.tags = objects.TagList()
        get_by_uuid.return_value = build_req

        result = self._filter_k8s_hosts(get_context,
                                        gather_host,
                                        get_aggrs)

        gather_host.assert_called_once_with(
            get_context.return_value,
            main_db_api.get_k8s_hosts_by_instances_metadata,
            gardener_cluster, '1',
            filters={'hv_type': 'VMware vCenter Server',
                     'availability_zone': 'az-2'})

        self.assertEqual(2, len(result))
        self.assertEqual(result[0].host, 'host4')
        self.assertEqual(result[1].host, 'host5')

    @mock.patch('nova.objects.AggregateList.get_all')
    @mock.patch('nova.context.scatter_gather_skip_cell0')
    @mock.patch('nova.objects.Instance.get_by_uuid')
    @mock.patch('nova.context.get_admin_context')
    def test_same_shard_for_nonbuild_requests(self, get_context,
                                              get_by_uuid,
                                              gather_host,
                                              get_aggrs):
        gardener_cluster = 'kubernetes.io-cluster-shoot--garden--testCluster'
        new_instance = fake_instance.fake_instance_obj(
            get_context.return_value,
            expected_attrs=['metadata'],
            metadata={gardener_cluster: '1'})
        get_by_uuid.return_value = new_instance

        result = self._filter_k8s_hosts(
            get_context, gather_host, get_aggrs,
            scheduler_hints={'_nova_check_type': ['live_migrate']})

        gather_host.assert_called_once_with(
            get_context.return_value,
            main_db_api.get_k8s_hosts_by_instances_metadata,
            gardener_cluster, '1',
            filters={'hv_type': 'VMware vCenter Server',
                     'availability_zone': 'az-2'})

        self.assertEqual(2, len(result))
        self.assertEqual(result[0].host, 'host4')
        self.assertEqual(result[1].host, 'host5')

    def _filter_k8s_hosts(self, get_context, gather_host, get_aggrs,
                          **request_spec):
        """Given a K8S cluster that spans across 3 shards
        (vc-a-0, vc-b-0, vc-b-1) and 2 availability zones (az-1, az-2)
        where the most k8s hosts are in the vc-b-1 shard. When there is
        a RequestSpec for 'az-2', then the hosts in 'vc-b-1' shard must
        be returned, since it's the dominant shard.
        """
        gather_host.return_value = {'cell1': [
            ('host3', 4), ('host4', 2), ('host5', 3)
        ]}

        self.filt_cls._PROJECT_TAG_CACHE['foo'] = ['sharding_enabled',
                                                     'vc-a-1']
        agg1 = objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])
        agg2 = objects.Aggregate(id=2, name='vc-b-0', hosts=['host2', 'host3'])
        agg3 = objects.Aggregate(id=3, name='vc-b-1', hosts=['host4', 'host5'])

        get_aggrs.return_value = [agg1, agg2, agg3]

        host1 = fakes.FakeHostState('host1', 'compute',
                                    {'aggregates': [agg1]})
        host2 = fakes.FakeHostState('host2', 'compute',
                                    {'aggregates': [agg2]})
        host3 = fakes.FakeHostState('host3', 'compute',
                                    {'aggregates': [agg2]})
        host4 = fakes.FakeHostState('host4', 'compute',
                                    {'aggregates': [agg3]})
        host5 = fakes.FakeHostState('host5', 'compute',
                                    {'aggregates': [agg3]})

        spec_obj = objects.RequestSpec(
            context=get_context.return_value, project_id='foo',
            availability_zone='az-2',
            instance_uuid=self.fake_instance.uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs'],
                name='m1'),
            **request_spec)

        return list(self.filt_cls.filter_all(
            [host1, host2, host3, host4, host5], spec_obj))

    @mock.patch('nova.objects.AggregateList.get_all')
    @mock.patch('nova.context.scatter_gather_skip_cell0')
    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.context.get_admin_context')
    def test_k8s_bypass_hana_flavors(self, get_context,
                                     get_by_uuid,
                                     gather_host,
                                     get_aggrs):
        gardener_cluster = 'kubernetes.io-cluster-shoot--garden--testCluster'
        hana_flavor = fake_flavor.fake_flavor_obj(
            mock.sentinel.ctx, expected_attrs=['extra_specs'],
            id=1, name='hana_flavor1', memory_mb=256, vcpus=1, root_gb=1)
        new_instance = fake_instance.fake_instance_obj(
            get_context.return_value,
            flavor=hana_flavor,
            expected_attrs=['metadata'],
            metadata={gardener_cluster: '1'})
        build_req = objects.BuildRequest()
        build_req.instance = new_instance
        build_req.tags = objects.TagList()

        get_by_uuid.return_value = build_req

        self.filt_cls._PROJECT_TAG_CACHE['baz'] = ['sharding_enabled',
                                                     'vc-a-1']
        agg1 = objects.Aggregate(id=1, name='vc-a-0', hosts=['host1'])
        hana_agg = objects.Aggregate(id=1, name='vc-b-0',
                                     hosts=['host2', 'host3'])

        host1 = fakes.FakeHostState('host1', 'compute',
                                    {'aggregates': [agg1]})
        host2 = fakes.FakeHostState('host2', 'compute',
                                    {'aggregates': [hana_agg]})
        host3 = fakes.FakeHostState('host3', 'compute',
                                    {'aggregates': [hana_agg]})
        get_aggrs.return_value = [agg1, hana_agg]

        spec_obj = objects.RequestSpec(
            context=get_context.return_value, project_id='foo',
            availability_zone='az-1',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs'],
                name='hana_flavor1'))

        result = list(self.filt_cls.filter_all([host1, host2, host3],
                                               spec_obj))

        gather_host.assert_not_called()
        self.assertEqual(3, len(result))
        self.assertEqual(result[0].host, 'host1')
        self.assertEqual(result[1].host, 'host2')
        self.assertEqual(result[2].host, 'host3')

    @mock.patch('nova.objects.BuildRequest.get_by_instance_uuid')
    @mock.patch('nova.scheduler.filters.shard_filter.LOG')
    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_log_level_for_missing_vc_aggregate(self, agg_mock, log_mock,
                                                get_by_uuid):
        get_by_uuid.return_value = self.fake_build_req
        host = fakes.FakeHostState('host1', 'compute', {})
        spec_obj = objects.RequestSpec(
            context=mock.sentinel.ctx, project_id='foo',
            instance_uuid=self.fake_build_req.instance_uuid,
            flavor=fake_flavor.fake_flavor_obj(
                mock.sentinel.ctx, expected_attrs=['extra_specs']))

        agg_mock.return_value = {}

        # For ironic hosts we log debug
        log_mock.debug = mock.Mock()
        log_mock.error = mock.Mock()
        host.hypervisor_type = 'ironic'
        self._assert_passes(host, spec_obj, False)
        log_mock.debug.assert_called_once_with(mock.ANY, mock.ANY)
        log_mock.error.assert_not_called()

        # For other hosts we log error
        log_mock.debug = mock.Mock()
        log_mock.error = mock.Mock()
        host.hypervisor_type = 'Some HV'
        self._assert_passes(host, spec_obj, False)
        log_mock.error.assert_called_once_with(mock.ANY, mock.ANY)
        log_mock.debug.assert_not_called()

    @mock.patch('nova.scheduler.utils.is_non_vmware_spec', return_value=True)
    def test_non_vmware_spec(self, mock_is_non_vmware_spec):
        host1 = mock.sentinel.host1
        host2 = mock.sentinel.host2
        spec_obj = mock.sentinel.spec_obj

        result = list(self.filt_cls.filter_all([host1, host2], spec_obj))

        self.assertEqual([host1, host2], result)
        mock_is_non_vmware_spec.assert_called_once_with(spec_obj)

    def _assert_passes(self, host, spec_obj, passes):
        result = bool(list(self.filt_cls.filter_all([host], spec_obj)))
        self.assertEqual(passes, result)
