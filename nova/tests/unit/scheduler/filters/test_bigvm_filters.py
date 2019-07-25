# Copyright (c) 2019 OpenStack Foundation
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

import nova.conf
from nova import objects
from nova.scheduler.filters import bigvm_filter
from nova import test
from nova.tests.unit.scheduler import fakes

CONF = nova.conf.CONF


@mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
class TestBigVmClusterUtilizationFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestBigVmClusterUtilizationFilter, self).setUp()
        self.hv_size = CONF.bigvm_mb + 1024
        self.filt_cls = bigvm_filter.BigVmClusterUtilizationFilter()

    def test_big_vm_with_small_vm_passes(self, agg_mock):
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=1024))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_aggregate(self, agg_mock):
        agg_mock.return_value = {}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_non_int_aggregate(self, agg_mock):
        agg_mock.return_value = {'hv_size_mb': ['foo']}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram(self, agg_mock):
        # there's enough RAM available in the cluster but not enough (~50 % of
        # the requested size on average
        # 12 hosts (bigvm + 1 GB size)
        # 11 big VM + some smaller (12 * 1 GB) already deployed
        # -> still bigvm_mb left, but ram utilization ratio of all hosts is too
        # high
        agg_mock.return_value = {'hv_size_mb': [self.hv_size]}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        total_ram = self.hv_size * 12
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': CONF.bigvm_mb,
                 'total_usable_ram_mb': total_ram})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram_ignores_ram_ratio(self, agg_mock):
        # same as test_big_vm_without_enough_ram but with more theoretical RAM
        # via `ram_allocation_ratio`. big VMs reserve all memory so the ratio
        # does not count for them.
        agg_mock.return_value = {'hv_size_mb': [self.hv_size]}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        total_ram = self.hv_size * 12
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': CONF.bigvm_mb,
                 'total_usable_ram_mb': total_ram,
                 'ram_allocation_ratio': 1.5})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram_percent(self, agg_mock):
        # there's just closely not enough RAM available
        agg_mock.return_value = {'hv_size_mb': [self.hv_size]}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        total_ram = self.hv_size * 12
        hv_percent = self.filt_cls._get_max_ram_percent(CONF.bigvm_mb,
                                                        self.hv_size)
        free_ram_mb = total_ram - (total_ram * hv_percent / 100.0) - 128
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': free_ram_mb,
                 'total_usable_ram_mb': total_ram})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_enough_ram(self, agg_mock):
        agg_mock.return_value = {'hv_size_mb': [self.hv_size]}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        total_ram = self.hv_size * 12
        hv_percent = self.filt_cls._get_max_ram_percent(CONF.bigvm_mb,
                                                        self.hv_size)
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': total_ram - (total_ram * hv_percent / 100.0),
                 'total_usable_ram_mb': total_ram})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))


@mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
class TestBigVmHypervisorRamFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestBigVmHypervisorRamFilter, self).setUp()
        self.hv_size = CONF.bigvm_mb + 1024
        self.filt_cls = bigvm_filter.BigVmHypervisorRamFilter()

    def test_big_vm_with_small_vm_passes(self, agg_mock):
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=1024))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_aggregate(self, agg_mock):
        agg_mock.return_value = {}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_non_int_aggregate(self, agg_mock):
        agg_mock.return_value = {'hv_size_mb': {'foo'}}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram(self, agg_mock):
        agg_mock.return_value = {'hv_size_mb': {self.hv_size}}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=self.hv_size + 1))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_enough_ram(self, agg_mock):
        agg_mock.return_value = {'hv_size_mb': {self.hv_size}}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=self.hv_size))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))
