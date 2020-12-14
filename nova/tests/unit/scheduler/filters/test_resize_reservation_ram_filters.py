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

from nova import objects
from nova.scheduler.filters import resize_reservation_filter
from nova import test
from nova.tests.unit.scheduler import fakes
from nova.tests import uuidsentinel


class TestResizeReservedHostRAMFilter(test.NoDBTestCase):
    def setUp(self):
        super(TestResizeReservedHostRAMFilter, self).setUp()
        self.filt_cls = resize_reservation_filter.ResizeReservedHostRAMFilter()

        flavor_small = objects.Flavor(
            id=1,
            name='small',
            memory_mb=1024,
            extra_specs={},
        )
        flavor_big = objects.Flavor(
            id=2,
            name='big',
            memory_mb=2048,
            extra_specs={},
        )
        self.instance = objects.Instance(
            host='host1',
            uuid=uuidsentinel.fake_instance,
            flavor=flavor_small,
        )
        self.request_specs = objects.RequestSpec(
            instance_uuid=self.instance.uuid,
            flavor=flavor_big,
            scheduler_hints={
                '_nova_check_type': ['resize']
            },
        )

    def test_ram_filter_fails_on_memory(self):
        self.flags(
            resize_threshold_reserved_ram_percent=10.0,
            group='filter_scheduler',
        )
        host = fakes.FakeHostState(
            'host1', 'node1', {
                'free_ram_mb': 2047,
                'total_usable_ram_mb': 2048,
                'ram_allocation_ratio': 1.0
            }, [self.instance]
        )
        self.assertFalse(self.filt_cls.host_passes(host, self.request_specs))

    def test_ram_filter_passes(self):
        self.flags(
            resize_threshold_reserved_ram_percent=10.0,
            group='filter_scheduler',
        )
        host = fakes.FakeHostState(
            'host1', 'node1', {
                'free_ram_mb': 3072,
                'total_usable_ram_mb': 4096,
                'ram_allocation_ratio': 1.0
            }, [self.instance]
        )
        self.assertTrue(self.filt_cls.host_passes(host, self.request_specs))

    def test_ram_filter_passes_for_new_build(self):
        self.flags(
            resize_threshold_reserved_ram_percent=10.0,
            group='filter_scheduler',
        )
        request_specs = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=1024)
        )
        host = fakes.FakeHostState(
            'host1', 'node1', {
                'free_ram_mb': 1024,
                'total_usable_ram_mb': 1024,
                'ram_allocation_ratio': 1.0
            }
        )
        self.assertTrue(self.filt_cls.host_passes(host, request_specs))

    def test_ram_filter_oversubscribe(self):
        reserved_ram = 10.0
        self.flags(
            resize_threshold_reserved_ram_percent=reserved_ram,
            group='filter_scheduler',
        )
        host = fakes.FakeHostState(
            'host1', 'node1', {
                'free_ram_mb': -512,
                'total_usable_ram_mb': 4096,
                'ram_allocation_ratio': 2.0
            }, [self.instance]
        )
        self.assertTrue(self.filt_cls.host_passes(host, self.request_specs))
        expected_limit = 4096 * 2.0
        reserved_ram_mb = expected_limit * reserved_ram / 100.0
        self.assertEqual(
            expected_limit - reserved_ram_mb, host.limits['memory_mb']
        )

    def test_ram_filter_oversubscribe_single_instance_fails(self):
        self.flags(
            resize_threshold_reserved_ram_percent=10.0,
            group='filter_scheduler',
        )
        host = fakes.FakeHostState(
            'host1', 'node1', {
                'free_ram_mb': 512,
                'total_usable_ram_mb': 512,
                'ram_allocation_ratio': 2.0
            }, [self.instance]
        )
        self.assertFalse(self.filt_cls.host_passes(host, self.request_specs))
