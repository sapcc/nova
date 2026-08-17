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

from unittest import mock

import contextlib

from oslo_config import cfg
import oslo_messaging as messaging
from oslo_serialization import jsonutils
from oslo_utils.fixture import uuidsentinel as uuids
from oslo_utils import units

from nova.compute import power_state
from nova.compute import rpcapi as compute_rpcapi
from nova.compute import utils as compute_utils
from nova.conductor.tasks import migrate
from nova import context
from nova import exception
from nova import objects
from nova.scheduler.client import query
from nova.scheduler.client import report
from nova.scheduler import utils as scheduler_utils
from nova import test
from nova.tests.unit.conductor.test_conductor import FakeContext
from nova.tests.unit import fake_flavor
from nova.tests.unit import fake_instance


class _FakeConversionError(Exception):
    """Stand-in for an unexpected failure that must propagate unchanged."""


class MigrationTaskTestCase(test.NoDBTestCase):
    def setUp(self):
        super(MigrationTaskTestCase, self).setUp()
        self.user_id = 'fake'
        self.project_id = 'fake'
        self.context = FakeContext(self.user_id, self.project_id)
        # Normally RequestContext.cell_uuid would be set when targeting
        # the context in nova.conductor.manager.targets_cell but we just
        # fake it here.
        self.context.cell_uuid = uuids.cell1
        self.flavor = fake_flavor.fake_flavor_obj(self.context)
        self.flavor.extra_specs = {'extra_specs': 'fake'}
        inst = fake_instance.fake_db_instance(
            image_ref='image_ref', flavor=self.flavor)
        inst_object = objects.Instance(
            flavor=self.flavor,
            numa_topology=None,
            pci_requests=None,
            system_metadata={'image_hw_disk_bus': 'scsi'})
        self.instance = objects.Instance._from_db_object(
            self.context, inst_object, inst, [])
        self.request_spec = objects.RequestSpec(image=objects.ImageMeta())
        self.host_lists = [[objects.Selection(service_host="host1",
                nodename="node1", cell_uuid=uuids.cell1)]]
        self.filter_properties = {'limits': {}, 'retry': {'num_attempts': 1,
                                  'hosts': [['host1', 'node1']]}}
        self.reservations = []
        self.clean_shutdown = True

        _p = mock.patch('nova.compute.utils.heal_reqspec_is_bfv')
        self.heal_reqspec_is_bfv_mock = _p.start()
        self.addCleanup(_p.stop)

        _p = mock.patch('nova.compute.utils.sanitize_image_props_for_kvm')
        self.sanitize_mock = _p.start()
        self.sanitize_mock.return_value = {}
        self.addCleanup(_p.stop)

        _p = mock.patch('nova.objects.RequestSpec.ensure_network_information')
        self.ensure_network_information_mock = _p.start()
        self.addCleanup(_p.stop)

        self.mock_network_api = mock.Mock()

    def _generate_task(self):
        return migrate.MigrationTask(self.context, self.instance, self.flavor,
                                     self.request_spec,
                                     self.clean_shutdown,
                                     compute_rpcapi.ComputeAPI(),
                                     query.SchedulerQueryClient(),
                                     report.SchedulerReportClient(),
                                     host_list=None,
                                     network_api=self.mock_network_api)

    @mock.patch.object(objects.MigrationList, 'get_by_filters')
    @mock.patch('nova.scheduler.client.report.SchedulerReportClient')
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    @mock.patch('nova.objects.Migration.save')
    @mock.patch('nova.objects.Migration.create')
    @mock.patch('nova.objects.Service.get_minimum_version_multi')
    @mock.patch('nova.availability_zones.get_host_availability_zone')
    @mock.patch.object(scheduler_utils, 'setup_instance_group')
    @mock.patch.object(query.SchedulerQueryClient, 'select_destinations')
    @mock.patch.object(compute_rpcapi.ComputeAPI, 'prep_resize')
    @mock.patch('nova.conductor.tasks.cross_cell_migrate.'
                'CrossCellMigrationTask.execute')
    def _test_execute(self, cross_cell_exec_mock, prep_resize_mock,
                      sel_dest_mock, sig_mock, az_mock, gmv_mock, cm_mock,
                      sm_mock, cn_mock, rc_mock, gbf_mock,
                      requested_destination=False, same_cell=True):
        sel_dest_mock.return_value = self.host_lists
        az_mock.return_value = 'myaz'
        gbf_mock.return_value = objects.MigrationList()
        mock_get_resources = \
            self.mock_network_api.get_requested_resource_for_instance
        mock_get_resources.return_value = ([], objects.RequestLevelParams())

        if requested_destination:
            self.request_spec.requested_destination = objects.Destination(
                host='target_host', node=None,
                allow_cross_cell_move=not same_cell)
            self.request_spec.retry = objects.SchedulerRetries.from_dict(
                self.context, self.filter_properties['retry'])
            self.filter_properties.pop('retry')
            self.filter_properties['requested_destination'] = (
                self.request_spec.requested_destination)

        task = self._generate_task()
        gmv_mock.return_value = 23

        # We just need this hook point to set a uuid on the
        # migration before we use it for teardown
        def set_migration_uuid(*a, **k):
            task._migration.uuid = uuids.migration
            return mock.MagicMock()

        # NOTE(danms): It's odd to do this on cn_mock, but it's just because
        # of when we need to have it set in the flow and where we have an easy
        # place to find it via self.migration.
        cn_mock.side_effect = set_migration_uuid

        selection = self.host_lists[0][0]
        with mock.patch.object(task, '_is_selected_host_in_source_cell',
                               return_value=same_cell) as _is_source_cell_mock:
            task.execute()
            _is_source_cell_mock.assert_called_once_with(selection)

        self.ensure_network_information_mock.assert_called_once_with(
            self.instance)
        self.heal_reqspec_is_bfv_mock.assert_called_once_with(
            self.context, self.request_spec, self.instance)
        sig_mock.assert_called_once_with(self.context, self.request_spec)
        task.query_client.select_destinations.assert_called_once_with(
            self.context, self.request_spec, [self.instance.uuid],
            return_objects=True, return_alternates=True)

        if same_cell:
            prep_resize_mock.assert_called_once_with(
                self.context, self.instance, self.request_spec.image,
                self.flavor, selection.service_host, task._migration,
                request_spec=self.request_spec,
                filter_properties=self.filter_properties,
                node=selection.nodename,
                clean_shutdown=self.clean_shutdown, host_list=[])
            az_mock.assert_called_once_with(self.context, 'host1')
            cross_cell_exec_mock.assert_not_called()
        else:
            cross_cell_exec_mock.assert_called_once_with()
            az_mock.assert_not_called()
            prep_resize_mock.assert_not_called()

        self.assertIsNotNone(task._migration)

        old_flavor = self.instance.flavor
        new_flavor = self.flavor
        self.assertEqual(old_flavor.id, task._migration.old_instance_type_id)
        self.assertEqual(new_flavor.id, task._migration.new_instance_type_id)
        self.assertEqual('pre-migrating', task._migration.status)
        self.assertEqual(self.instance.uuid, task._migration.instance_uuid)
        self.assertEqual(self.instance.host, task._migration.source_compute)
        self.assertEqual(self.instance.node, task._migration.source_node)
        if old_flavor.id != new_flavor.id:
            self.assertEqual('resize', task._migration.migration_type)
        else:
            self.assertEqual('migration', task._migration.migration_type)

        task._migration.create.assert_called_once_with()

        if requested_destination:
            self.assertIsNone(self.request_spec.retry)
            self.assertIn('cell', self.request_spec.requested_destination)
            self.assertIsNotNone(self.request_spec.requested_destination.cell)
            self.assertEqual(
                not same_cell,
                self.request_spec.requested_destination.allow_cross_cell_move)

        mock_get_resources.assert_called_once_with(
            self.context, self.instance.uuid)
        self.assertEqual([], self.request_spec.requested_resources)
        self.assertEqual(
            mock_get_resources.return_value[1],
            self.request_spec.request_level_params,
        )

    def test_execute(self):
        self._test_execute()

    def test_execute_with_destination(self):
        self._test_execute(requested_destination=True)

    def test_execute_resize(self):
        self.flavor = self.flavor.obj_clone()
        self.flavor.id = 3
        self._test_execute()

    def test_execute_same_cell_false(self):
        """Tests the execute() scenario that the RequestSpec allows cross
        cell move and the selected target host is in another cell so
        CrossCellMigrationTask is executed.
        """
        self._test_execute(same_cell=False)

    @mock.patch.object(objects.MigrationList, 'get_by_filters')
    @mock.patch('nova.conductor.tasks.migrate.revert_allocation_for_migration')
    @mock.patch('nova.scheduler.client.report.SchedulerReportClient')
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    @mock.patch('nova.objects.Migration.save')
    @mock.patch('nova.objects.Migration.create')
    @mock.patch('nova.objects.Service.get_minimum_version_multi')
    @mock.patch('nova.availability_zones.get_host_availability_zone')
    @mock.patch.object(scheduler_utils, 'setup_instance_group')
    @mock.patch.object(query.SchedulerQueryClient, 'select_destinations')
    @mock.patch.object(compute_rpcapi.ComputeAPI, 'prep_resize')
    def test_execute_rollback(self, prep_resize_mock, sel_dest_mock, sig_mock,
                              az_mock, gmv_mock, cm_mock, sm_mock, cn_mock,
                              rc_mock, mock_ra, mock_gbf):
        sel_dest_mock.return_value = self.host_lists
        az_mock.return_value = 'myaz'
        task = self._generate_task()
        gmv_mock.return_value = 23
        mock_gbf.return_value = objects.MigrationList()
        mock_get_resources = \
            self.mock_network_api.get_requested_resource_for_instance
        mock_get_resources.return_value = ([], objects.RequestLevelParams())

        # We just need this hook point to set a uuid on the
        # migration before we use it for teardown
        def set_migration_uuid(*a, **k):
            task._migration.uuid = uuids.migration
            return mock.MagicMock()

        # NOTE(danms): It's odd to do this on cn_mock, but it's just because
        # of when we need to have it set in the flow and where we have an easy
        # place to find it via self.migration.
        cn_mock.side_effect = set_migration_uuid

        prep_resize_mock.side_effect = test.TestingException
        task._held_allocations = mock.sentinel.allocs
        self.assertRaises(test.TestingException, task.execute)
        self.assertIsNotNone(task._migration)
        task._migration.create.assert_called_once_with()
        task._migration.save.assert_called_once_with()
        self.assertEqual('error', task._migration.status)
        mock_ra.assert_called_once_with(task.context, task._source_cn,
                                        task.instance, task._migration)
        mock_get_resources.assert_called_once_with(
            self.context, self.instance.uuid)

    @mock.patch.object(scheduler_utils, 'claim_resources')
    @mock.patch.object(context.RequestContext, 'elevated')
    def test_execute_reschedule(self, mock_elevated, mock_claim):
        report_client = report.SchedulerReportClient()
        # setup task for re-schedule
        alloc_req = {
            "allocations": {
                uuids.host1: {
                    "resources": {
                        "VCPU": 1,
                        "MEMORY_MB": 1024,
                        "DISK_GB": 100}}}}
        alternate_selection = objects.Selection(
            service_host="host1",
            nodename="node1",
            cell_uuid=uuids.cell1,
            allocation_request=jsonutils.dumps(alloc_req),
            allocation_request_version='1.19')
        task = migrate.MigrationTask(
            self.context, self.instance, self.flavor, self.request_spec,
            self.clean_shutdown, compute_rpcapi.ComputeAPI(),
            query.SchedulerQueryClient(), report_client,
            host_list=[alternate_selection], network_api=self.mock_network_api)
        mock_claim.return_value = True

        actual_selection = task._reschedule()

        self.assertIs(alternate_selection, actual_selection)
        mock_claim.assert_called_once_with(
            mock_elevated.return_value, report_client, self.request_spec,
            self.instance.uuid, alloc_req, '1.19')

    @mock.patch.object(scheduler_utils, 'fill_provider_mapping')
    @mock.patch.object(scheduler_utils, 'claim_resources')
    @mock.patch.object(context.RequestContext, 'elevated')
    def test_execute_reschedule_claim_fails_no_more_alternate(
            self, mock_elevated, mock_claim, mock_fill_provider_mapping):
        report_client = report.SchedulerReportClient()
        # set up the task for re-schedule
        alloc_req = {
            "allocations": {
                uuids.host1: {
                    "resources": {
                        "VCPU": 1,
                        "MEMORY_MB": 1024,
                        "DISK_GB": 100}}}}
        alternate_selection = objects.Selection(
            service_host="host1",
            nodename="node1",
            cell_uuid=uuids.cell1,
            allocation_request=jsonutils.dumps(alloc_req),
            allocation_request_version='1.19')
        task = migrate.MigrationTask(
            self.context, self.instance, self.flavor, self.request_spec,
            self.clean_shutdown, compute_rpcapi.ComputeAPI(),
            query.SchedulerQueryClient(), report_client,
            host_list=[alternate_selection], network_api=self.mock_network_api)
        mock_claim.return_value = False

        self.assertRaises(exception.MaxRetriesExceeded, task._reschedule)

        mock_claim.assert_called_once_with(
            mock_elevated.return_value, report_client, self.request_spec,
            self.instance.uuid, alloc_req, '1.19')
        mock_fill_provider_mapping.assert_not_called()

    @mock.patch('nova.objects.InstanceMapping.get_by_instance_uuid',
                return_value=objects.InstanceMapping(
                    cell_mapping=objects.CellMapping(uuid=uuids.cell1)))
    @mock.patch('nova.conductor.tasks.migrate.LOG.debug')
    def test_set_requested_destination_cell_allow_cross_cell_resize_true(
            self, mock_debug, mock_get_im):
        """Tests the scenario that the RequestSpec is configured for
        allow_cross_cell_resize=True.
        """
        task = self._generate_task()
        legacy_props = self.request_spec.to_legacy_filter_properties_dict()
        self.request_spec.requested_destination = objects.Destination(
            allow_cross_cell_move=True)
        task._set_requested_destination_cell(legacy_props)
        mock_get_im.assert_called_once_with(self.context, self.instance.uuid)
        mock_debug.assert_called_once()
        self.assertIn('Allowing migration from cell',
                      mock_debug.call_args[0][0])
        self.assertEqual(mock_get_im.return_value.cell_mapping,
                         self.request_spec.requested_destination.cell)

    @mock.patch('nova.objects.InstanceMapping.get_by_instance_uuid',
                return_value=objects.InstanceMapping(
                    cell_mapping=objects.CellMapping(uuid=uuids.cell1)))
    @mock.patch('nova.conductor.tasks.migrate.LOG.debug')
    def test_set_requested_destination_cell_allow_cross_cell_resize_true_host(
            self, mock_debug, mock_get_im):
        """Tests the scenario that the RequestSpec is configured for
        allow_cross_cell_resize=True and there is a requested target host.
        """
        task = self._generate_task()
        legacy_props = self.request_spec.to_legacy_filter_properties_dict()
        self.request_spec.requested_destination = objects.Destination(
            allow_cross_cell_move=True, host='fake-host')
        task._set_requested_destination_cell(legacy_props)
        mock_get_im.assert_called_once_with(self.context, self.instance.uuid)
        mock_debug.assert_called_once()
        self.assertIn('Not restricting cell', mock_debug.call_args[0][0])
        self.assertIsNone(self.request_spec.requested_destination.cell)

    @mock.patch('nova.objects.InstanceMapping.get_by_instance_uuid',
                return_value=objects.InstanceMapping(
                    cell_mapping=objects.CellMapping(uuid=uuids.cell1)))
    @mock.patch('nova.conductor.tasks.migrate.LOG.debug')
    def test_set_requested_destination_cell_allow_cross_cell_resize_false(
            self, mock_debug, mock_get_im):
        """Tests the scenario that the RequestSpec is configured for
        allow_cross_cell_resize=False.
        """
        task = self._generate_task()
        legacy_props = self.request_spec.to_legacy_filter_properties_dict()
        # We don't have to explicitly set RequestSpec.requested_destination
        # since _set_requested_destination_cell will do that and the
        # Destination object will default allow_cross_cell_move to False.
        task._set_requested_destination_cell(legacy_props)
        mock_get_im.assert_called_once_with(self.context, self.instance.uuid)
        mock_debug.assert_called_once()
        self.assertIn('Restricting to cell', mock_debug.call_args[0][0])

    def test_is_selected_host_in_source_cell_true(self):
        """Tests the scenario that the host Selection from the scheduler is in
        the same cell as the instance.
        """
        task = self._generate_task()
        selection = objects.Selection(cell_uuid=self.context.cell_uuid)
        self.assertTrue(task._is_selected_host_in_source_cell(selection))

    def test_is_selected_host_in_source_cell_false(self):
        """Tests the scenario that the host Selection from the scheduler is
        not in the same cell as the instance.
        """
        task = self._generate_task()
        selection = objects.Selection(cell_uuid=uuids.cell2, service_host='x')
        self.assertFalse(task._is_selected_host_in_source_cell(selection))

    @mock.patch.object(objects.MigrationList, 'get_by_filters')
    @mock.patch('nova.scheduler.client.report.SchedulerReportClient')
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    @mock.patch('nova.objects.Migration.save')
    @mock.patch('nova.objects.Migration.create')
    @mock.patch('nova.objects.Service.get_minimum_version_multi')
    @mock.patch('nova.availability_zones.get_host_availability_zone')
    @mock.patch.object(scheduler_utils, 'setup_instance_group')
    @mock.patch.object(query.SchedulerQueryClient, 'select_destinations')
    @mock.patch.object(compute_rpcapi.ComputeAPI, 'prep_resize')
    @mock.patch.object(migrate.MigrationTask, '_get_root_bdm')
    def test_execute_calls_sanitize_and_stashes_journal(
            self, mock_get_bdm, prep_resize_mock, sel_dest_mock, sig_mock,
            az_mock, gmv_mock, cm_mock, sm_mock, cn_mock, rc_mock, gbf_mock):
        """Verify sanitize is called for cross-HV and result stashed."""
        sel_dest_mock.return_value = self.host_lists
        az_mock.return_value = 'myaz'
        gbf_mock.return_value = objects.MigrationList()
        mock_get_resources = \
            self.mock_network_api.get_requested_resource_for_instance
        mock_get_resources.return_value = ([], objects.RequestLevelParams())
        gmv_mock.return_value = 23
        # BFV instance: root BDM is volume-backed - passes validation,
        # skips conversion
        bfv_bdm = mock.Mock()
        bfv_bdm.destination_type = 'volume'
        bfv_bdm.source_type = 'volume'
        mock_get_bdm.return_value = bfv_bdm

        fake_journal = {'img_hv_type': 'vmware', 'hw_disk_bus': 'scsi'}
        self.sanitize_mock.return_value = fake_journal

        self.request_spec.is_bfv = True
        self.flavor.extra_specs['capabilities:hypervisor_type'] = 'CH'
        self.flags(enable_cross_hv_resize=True, group='workarounds')
        task = self._generate_task()
        task.instance.save = mock.Mock()
        task.instance.power_state = power_state.RUNNING

        def set_migration_uuid(*a, **k):
            task._migration.uuid = uuids.migration
            task._migration.id = 1
            cn = mock.MagicMock()
            cn.hypervisor_type = 'VMware vCenter Server'
            task._source_cn = cn
            return cn

        cn_mock.side_effect = set_migration_uuid
        task.execute()

        self.sanitize_mock.assert_called_once_with(self.request_spec)
        self.assertEqual(fake_journal, task._old_image_properties)
        self.assertEqual(
            'true', task.instance.system_metadata['cross_hv_resize'])
        self.assertEqual(
            'false',
            task.instance.system_metadata['cross_hv_source_prepared'])
        # Verify journal was persisted to migration_context
        self.assertIsNotNone(task.instance.migration_context)
        self.assertEqual(
            fake_journal,
            task.instance.migration_context.old_image_properties)
        task.instance.save.assert_called()

    @mock.patch.object(objects.MigrationList, 'get_by_filters')
    @mock.patch('nova.scheduler.client.report.SchedulerReportClient')
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    @mock.patch('nova.objects.Migration.save')
    @mock.patch('nova.objects.Migration.create')
    @mock.patch('nova.objects.Service.get_minimum_version_multi')
    @mock.patch('nova.availability_zones.get_host_availability_zone')
    @mock.patch.object(scheduler_utils, 'setup_instance_group')
    @mock.patch.object(query.SchedulerQueryClient, 'select_destinations')
    @mock.patch.object(compute_rpcapi.ComputeAPI, 'prep_resize')
    def test_execute_skips_sanitize_for_same_hv(
            self, prep_resize_mock, sel_dest_mock, sig_mock, az_mock,
            gmv_mock, cm_mock, sm_mock, cn_mock, rc_mock, gbf_mock):
        """Verify sanitize is NOT called for same-HV resize."""
        sel_dest_mock.return_value = self.host_lists
        az_mock.return_value = 'myaz'
        gbf_mock.return_value = objects.MigrationList()
        mock_get_resources = \
            self.mock_network_api.get_requested_resource_for_instance
        mock_get_resources.return_value = ([], objects.RequestLevelParams())
        gmv_mock.return_value = 23

        task = self._generate_task()

        def set_migration_uuid(*a, **k):
            task._migration.uuid = uuids.migration
            return mock.MagicMock()

        cn_mock.side_effect = set_migration_uuid
        task.execute()

        self.sanitize_mock.assert_not_called()
        self.assertEqual({}, task._old_image_properties)


class CrossHvResizeTestCase(test.NoDBTestCase):
    HV_VMWARE = 'VMware vCenter Server'
    HV_CH = 'CH'
    HV_KVM = 'QEMU'

    def setUp(self):
        super().setUp()
        self.flags(enable_cross_hv_resize=True, group='workarounds')
        ctx = FakeContext('fake', 'fake')
        flavor = fake_flavor.fake_flavor_obj(ctx)
        flavor.extra_specs = {}
        inst = fake_instance.fake_db_instance(flavor=flavor)
        inst_obj = objects.Instance(
            flavor=flavor, numa_topology=None,
            pci_requests=None, system_metadata={})
        self.instance = objects.Instance._from_db_object(
            ctx, inst_obj, inst, [])
        self.instance.power_state = power_state.RUNNING
        self.instance.save = mock.Mock()
        request_spec = objects.RequestSpec(image=objects.ImageMeta())
        request_spec.is_bfv = True
        self.task = migrate.MigrationTask(
            ctx, self.instance, flavor, request_spec,
            clean_shutdown=True,
            compute_rpcapi=mock.Mock(),
            query_client=mock.Mock(),
            report_client=mock.Mock(),
            host_list=None,
            network_api=mock.Mock())
        self.task._source_cn = mock.Mock()
        self.task._source_cn.hypervisor_type = self.HV_VMWARE

    def test_prep_raises_for_disallowed_transition(self):
        for src, dest in [
            ('CH', 'VMware vCenter Server'),
            ('QEMU', 'CH'),
        ]:
            with self.subTest(src=src, dest=dest):
                self.assertRaises(
                    exception.InvalidCrossHvResize,
                    self.task._prep_cross_hv_resize, src, dest)

    @mock.patch('nova.compute.utils.sanitize_image_props_for_kvm',
                return_value={'img_hv_type': 'vmware'})
    def test_prep_vmware_to_ch_passes_and_sanitizes(self, mock_sanitize):
        self.task._prep_cross_hv_resize(self.HV_VMWARE, self.HV_CH)
        mock_sanitize.assert_called_once_with(self.task.request_spec)
        self.assertEqual({'img_hv_type': 'vmware'},
                         self.task._old_image_properties)
        self.assertEqual('true',
                         self.instance.system_metadata['cross_hv_resize'])
        self.assertEqual(
            'false',
            self.instance.system_metadata['cross_hv_source_prepared'])

    @mock.patch('nova.compute.utils.is_supported_cross_hypervisor_resize')
    @mock.patch(
        'nova.compute.utils.raise_on_unsupported_cross_hypervisor_resize')
    @mock.patch('nova.compute.utils.sanitize_image_props_for_kvm',
                return_value={'img_hv_type': 'vmware'})
    def test_prep_reuses_raise_helper_for_support_check(
            self, mock_sanitize, mock_raise, mock_supported):
        mock_supported.side_effect = AssertionError(
            'support check should stay in raise_on_unsupported')

        self.task._prep_cross_hv_resize(self.HV_VMWARE, self.HV_CH)

        mock_raise.assert_called_once_with(
            self.task.context, self.instance, self.task.request_spec,
            self.HV_VMWARE, self.HV_CH)
        mock_sanitize.assert_called_once_with(self.task.request_spec)

    def test_persist_image_properties_journal_saves_empty_journal(self):
        self.task._old_image_properties = {}
        self.instance.system_metadata['cross_hv_resize'] = 'true'
        self.instance.system_metadata['cross_hv_source_prepared'] = 'false'
        migration = objects.Migration(id=42)

        self.task._persist_image_properties_journal(migration)

        self.assertTrue(self.instance.obj_attr_is_set('migration_context'))
        self.assertEqual(
            {}, self.instance.migration_context.old_image_properties)
        self.instance.save.assert_called_once_with()

    def test_prep_vmware_to_qemu_raises(self):
        self.assertRaises(exception.InvalidCrossHvResize,
                          self.task._prep_cross_hv_resize,
                          self.HV_VMWARE, self.HV_KVM)

    def test_prep_raises_if_source_hv_missing_for_cross_hv_dest(self):
        self.assertRaises(exception.InvalidCrossHvResize,
                          self.task._prep_cross_hv_resize, None, self.HV_CH)

    @mock.patch('nova.compute.utils.sanitize_image_props_for_kvm',
                return_value={'img_hv_type': 'vmware'})
    def test_prep_allows_non_bfv(self, mock_sanitize):
        """Non-BFV instances are now allowed for cross-HV resize."""
        self.task.request_spec.is_bfv = False
        # Should NOT raise
        self.task._prep_cross_hv_resize(self.HV_VMWARE, self.HV_CH)
        mock_sanitize.assert_called_once_with(self.task.request_spec)
        self.assertEqual('true',
                         self.instance.system_metadata['cross_hv_resize'])

    def test_prep_raises_not_running(self):
        for ps in (power_state.SHUTDOWN, power_state.PAUSED,
                   power_state.SUSPENDED):
            with self.subTest(power_state=ps):
                self.instance.power_state = ps
                self.assertRaises(
                    exception.InvalidCrossHvResizePrecondition,
                    self.task._prep_cross_hv_resize, self.HV_VMWARE,
                    self.HV_CH)


class CrossHvImageConversionDetectionTestCase(test.NoDBTestCase):
    """Tests for image-backed root BDM detection in MigrationTask."""

    def setUp(self):
        super().setUp()
        ctx = FakeContext('fake', 'fake')
        flavor = fake_flavor.fake_flavor_obj(ctx)
        flavor.extra_specs = {}
        inst = fake_instance.fake_db_instance(flavor=flavor)
        inst_obj = objects.Instance(
            flavor=flavor, numa_topology=None,
            pci_requests=None, system_metadata={})
        self.instance = objects.Instance._from_db_object(
            ctx, inst_obj, inst, [])
        self.instance.power_state = power_state.RUNNING
        self.instance.save = mock.Mock()
        request_spec = objects.RequestSpec(image=objects.ImageMeta())
        request_spec.is_bfv = False
        self.task = migrate.MigrationTask(
            ctx, self.instance, flavor, request_spec,
            clean_shutdown=True,
            compute_rpcapi=mock.Mock(),
            query_client=mock.Mock(),
            report_client=mock.Mock(),
            host_list=None,
            network_api=mock.Mock())

    def _make_bdm(self, source_type='image', destination_type='local',
                  boot_index=0, volume_id=None, image_id='fake-image'):
        bdm = objects.BlockDeviceMapping(
            source_type=source_type,
            destination_type=destination_type,
            boot_index=boot_index,
            volume_id=volume_id,
            image_id=image_id)
        return bdm

    @mock.patch('nova.objects.BlockDeviceMappingList.get_by_instance_uuid')
    def test_get_root_bdm_returns_root(self, mock_get_bdms):
        root = self._make_bdm(boot_index=0)
        non_root = self._make_bdm(boot_index=1, source_type='blank',
                                  destination_type='local')
        mock_get_bdms.return_value = objects.BlockDeviceMappingList(
            objects=[root, non_root])
        result = self.task._get_root_bdm()
        self.assertIs(result, root)

    @mock.patch('nova.objects.BlockDeviceMappingList.get_by_instance_uuid')
    def test_get_root_bdm_returns_none_if_no_root(self, mock_get_bdms):
        non_root = self._make_bdm(boot_index=1)
        mock_get_bdms.return_value = objects.BlockDeviceMappingList(
            objects=[non_root])
        result = self.task._get_root_bdm()
        self.assertIsNone(result)

    def test_is_image_backed_local_root_true(self):
        bdm = self._make_bdm(source_type='image', destination_type='local')
        self.assertTrue(self.task._is_image_backed_local_root(bdm))

    def test_is_image_backed_local_root_false_for_volume(self):
        bdm = self._make_bdm(source_type='volume', destination_type='volume',
                             volume_id='vol-1')
        self.assertFalse(self.task._is_image_backed_local_root(bdm))

    def test_is_image_backed_local_root_false_for_none(self):
        self.assertFalse(self.task._is_image_backed_local_root(None))

    def test_is_image_backed_local_root_false_for_image_volume(self):
        """image/volume (BFV from image) is not a conversion candidate."""
        bdm = self._make_bdm(source_type='image', destination_type='volume',
                             volume_id='vol-1')
        self.assertFalse(self.task._is_image_backed_local_root(bdm))

    def test_metadata_set_but_bdm_image_still_converts(self):
        """Even if metadata says converted, BDM shape is the real guard."""
        self.instance.system_metadata['cross_hv_image_converted'] = 'True'
        bdm = self._make_bdm(source_type='image', destination_type='local',
                             volume_id='vol-123')
        self.assertTrue(self.task._is_image_backed_local_root(bdm))

    def test_validate_cross_hv_root_bdm_allows_image_local(self):
        bdm = self._make_bdm(source_type='image', destination_type='local')
        # Should not raise
        self.task._validate_cross_hv_root_bdm(bdm)

    def test_validate_cross_hv_root_bdm_allows_volume_backed(self):
        bdm = self._make_bdm(source_type='volume', destination_type='volume',
                             volume_id='vol-1')
        # Should not raise
        self.task._validate_cross_hv_root_bdm(bdm)

    def test_validate_cross_hv_root_bdm_allows_image_volume(self):
        """BFV-from-image (image/volume) is also allowed."""
        bdm = self._make_bdm(source_type='image', destination_type='volume',
                             volume_id='vol-1')
        self.task._validate_cross_hv_root_bdm(bdm)

    def test_validate_cross_hv_root_bdm_rejects_none(self):
        self.assertRaises(
            exception.InvalidCrossHvResizePrecondition,
            self.task._validate_cross_hv_root_bdm, None)

    def test_validate_cross_hv_root_bdm_rejects_blank_local(self):
        """blank/local swap disk is not a supported root for cross-HV."""
        bdm = self._make_bdm(source_type='blank', destination_type='local')
        self.assertRaises(
            exception.InvalidCrossHvResizePrecondition,
            self.task._validate_cross_hv_root_bdm, bdm)


class MigrationTaskAllocationUtils(test.NoDBTestCase):
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    def test_replace_allocation_with_migration_no_host(self, mock_cn):
        mock_cn.side_effect = exception.ComputeHostNotFound(host='host')
        migration = objects.Migration()
        instance = objects.Instance(host='host', node='node')

        self.assertRaises(exception.ComputeHostNotFound,
                          migrate.replace_allocation_with_migration,
                          mock.sentinel.context,
                          instance, migration)
        mock_cn.assert_called_once_with(mock.sentinel.context,
                                        instance.host, instance.node)

    @mock.patch('nova.scheduler.client.report.SchedulerReportClient.'
                'get_allocs_for_consumer')
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    def test_replace_allocation_with_migration_no_allocs(self, mock_cn,
                                                         mock_ga):
        mock_ga.return_value = {'allocations': {}}
        migration = objects.Migration(uuid=uuids.migration)
        instance = objects.Instance(uuid=uuids.instance,
                                    host='host', node='node')

        result = migrate.replace_allocation_with_migration(
            mock.sentinel.context, instance, migration)
        self.assertEqual((None, None), result)

    @mock.patch('nova.scheduler.client.report.SchedulerReportClient.'
                'put_allocations')
    @mock.patch('nova.scheduler.client.report.SchedulerReportClient.'
                'get_allocs_for_consumer')
    @mock.patch('nova.objects.ComputeNode.get_by_host_and_nodename')
    def test_replace_allocation_with_migration_allocs_fail(self, mock_cn,
                                                           mock_ga, mock_pa):
        ctxt = context.get_admin_context()
        migration = objects.Migration(uuid=uuids.migration)
        instance = objects.Instance(uuid=uuids.instance,
                                    user_id='fake', project_id='fake',
                                    host='host', node='node')
        mock_pa.return_value = False

        self.assertRaises(exception.NoValidHost,
                           migrate.replace_allocation_with_migration,
                           ctxt,
                           instance, migration)


class CrossHvImageConversionTestCase(test.NoDBTestCase):
    """Tests for _convert_image_backed_root_to_bfv orchestration."""

    @staticmethod
    def _ensure_cross_hv_conf():
        """Register [cross_hv] group and fcd_volume_type if not present."""
        _CONF = cfg.CONF
        try:
            _CONF.cross_hv.fcd_volume_type
        except (cfg.NoSuchGroupError, cfg.NoSuchOptError):
            try:
                _CONF.register_group(cfg.OptGroup('cross_hv'))
            except cfg.DuplicateOptError:
                pass
            try:
                _CONF.register_opt(
                    cfg.StrOpt('fcd_volume_type', default='vmware'),
                    group='cross_hv')
            except cfg.DuplicateOptError:
                pass

    def setUp(self):
        super().setUp()
        self._ensure_cross_hv_conf()
        self.flags(enable_cross_hv_resize=True, group='workarounds')
        self.flags(fcd_volume_type='vmware', group='cross_hv')
        ctx = FakeContext('fake', 'fake')
        flavor = fake_flavor.fake_flavor_obj(ctx)
        flavor.extra_specs = {}
        inst = fake_instance.fake_db_instance(flavor=flavor)
        inst_obj = objects.Instance(
            flavor=flavor, numa_topology=None,
            pci_requests=None, system_metadata={},
            image_ref='original-image-ref')
        self.instance = objects.Instance._from_db_object(
            ctx, inst_obj, inst, [])
        self.instance.power_state = power_state.RUNNING
        self.instance.save = mock.Mock()

        request_spec = objects.RequestSpec(image=objects.ImageMeta())
        request_spec.is_bfv = False

        self.mock_compute_rpcapi = mock.Mock()
        self.mock_volume_api = mock.Mock()

        self.task = migrate.MigrationTask(
            ctx, self.instance, flavor, request_spec,
            clean_shutdown=True,
            compute_rpcapi=self.mock_compute_rpcapi,
            query_client=mock.Mock(),
            report_client=mock.Mock(),
            host_list=None,
            network_api=mock.Mock(),
            volume_api=self.mock_volume_api)

        # Standard prep response
        self.prep_response = {
            'vmdk_path': '[datastore1] vm-uuid/vm-uuid.vmdk',
            'size_bytes': 10 * 1024 * 1024 * 1024,  # 10 GiB
            'cinder_host': 'cinder-vol@vmware_fcd',
            'source_fcd_id': 'fcd-123',
            'rollback': {'disk_key': 2000, 'controller_key': 1000},
        }
        self.mock_compute_rpcapi.prep_cross_hv_conversion.return_value = (
            self.prep_response)

        # manage_existing (ticket 672) polls until available and raises on
        # failure. The conductor gets back an already-available volume dict.
        self.mock_volume_api.manage_existing.return_value = {
            'id': uuids.volume, 'status': 'available'}

        # Standard attachment response
        self.mock_volume_api.attachment_create.return_value = {
            'id': uuids.attachment}

    def _make_root_bdm(self):
        bdm = objects.BlockDeviceMapping(
            context=self.task.context,
            source_type='image',
            destination_type='local',
            boot_index=0,
            volume_id=None,
            image_id=uuids.image,
            volume_size=None,
            connection_info=None,
            attachment_id=None,
            snapshot_id=None)
        bdm.save = mock.Mock()
        return bdm

    def test_validate_manage_config_missing_volume_type(self):
        self.flags(fcd_volume_type='', group='cross_hv')
        self.assertRaises(exception.CrossHVConfigurationMissing,
                          self.task._validate_cross_hv_manage_config)

    def test_happy_path_converts_root_bdm(self):
        root_bdm = self._make_root_bdm()

        self.task._convert_image_backed_root_to_bfv(root_bdm)

        # Verify prep was called
        self.mock_compute_rpcapi.prep_cross_hv_conversion \
            .assert_called_once_with(self.task.context, self.instance)

        # Verify manage_existing called with correct args including size_gb.
        self.mock_volume_api.manage_existing.assert_called_once_with(
            self.task.context,
            host='cinder-vol@vmware_fcd',
            ref={'source-name': '[datastore1] vm-uuid/vm-uuid.vmdk',
                 'size_gb': 10,
                 'source-id': 'fcd-123'},
            volume_type='vmware',
            name='cross-hv-%s' % self.instance.uuid,
            description='Auto-converted from image-backed instance',
            bootable=True)

        # Verify attachment created
        self.mock_volume_api.attachment_create.assert_called_once_with(
            self.task.context, uuids.volume, self.instance.uuid)

        # Verify BDM mutation
        self.assertEqual('volume', root_bdm.destination_type)
        self.assertEqual(uuids.volume, root_bdm.volume_id)
        self.assertEqual(10, root_bdm.volume_size)
        self.assertEqual(uuids.attachment, root_bdm.attachment_id)
        self.assertIsNone(root_bdm.snapshot_id)
        root_bdm.save.assert_called_once()

        # source_type and image_id preserved
        self.assertEqual('image', root_bdm.source_type)
        self.assertEqual(uuids.image, root_bdm.image_id)

        # System metadata set
        self.assertEqual(
            'True',
            self.instance.system_metadata['cross_hv_image_converted'])
        self.instance.save.assert_called()

        # connection_info has placeholder
        conn_info = jsonutils.loads(root_bdm.connection_info)
        self.assertTrue(conn_info['cross_hv_placeholder'])

        # abort was NOT called
        self.mock_compute_rpcapi.abort_cross_hv_conversion \
            .assert_not_called()

    def test_prep_rpc_failure_does_not_abort(self):
        """If prep_cross_hv_conversion fails, no abort is called."""
        self.mock_compute_rpcapi.prep_cross_hv_conversion.side_effect = (
            _FakeConversionError('RPC timeout'))
        root_bdm = self._make_root_bdm()

        self.assertRaises(_FakeConversionError,
                          self.task._convert_image_backed_root_to_bfv,
                          root_bdm)
        self.mock_volume_api.manage_existing.assert_not_called()
        self.mock_compute_rpcapi.abort_cross_hv_conversion \
            .assert_not_called()
        self.assertEqual('local', root_bdm.destination_type)

    def test_abort_safe_manage_exception_aborts(self):
        self.mock_volume_api.manage_existing.side_effect = (
            exception.VolumeManageFailed(reason='manage failed'))
        root_bdm = self._make_root_bdm()

        self.assertRaises(exception.VolumeManageFailed,
                          self.task._convert_image_backed_root_to_bfv,
                          root_bdm)
        self.mock_compute_rpcapi.abort_cross_hv_conversion \
            .assert_called_once_with(
                self.task.context, self.instance, self.prep_response)
        self.assertEqual('local', root_bdm.destination_type)

    def test_no_abort_manage_exception_does_not_abort(self):
        self.mock_volume_api.manage_existing.side_effect = (
            exception.VolumeManageFailedNoAbort(
                volume_id=uuids.volume, reason='manage failed'))
        root_bdm = self._make_root_bdm()

        self.assertRaises(exception.VolumeManageFailedNoAbort,
                          self.task._convert_image_backed_root_to_bfv,
                          root_bdm)
        self.mock_compute_rpcapi.abort_cross_hv_conversion \
            .assert_not_called()
        self.assertEqual('local', root_bdm.destination_type)

    def test_attachment_failure_leaves_volume(self):
        """attachment_create failure: volume left, no abort."""
        self.mock_volume_api.attachment_create.side_effect = (
            _FakeConversionError('attachment failed'))
        root_bdm = self._make_root_bdm()

        self.assertRaises(_FakeConversionError,
                          self.task._convert_image_backed_root_to_bfv,
                          root_bdm)
        self.mock_compute_rpcapi.abort_cross_hv_conversion \
            .assert_not_called()
        self.mock_volume_api.manage_existing.assert_called_once()
        self.assertEqual('local', root_bdm.destination_type)

    def test_bdm_save_failure_deletes_attachment(self):
        """BDM save failure -> delete attachment, leave volume."""
        root_bdm = self._make_root_bdm()
        root_bdm.save = mock.Mock(side_effect=_FakeConversionError('db error'))

        self.assertRaises(_FakeConversionError,
                          self.task._convert_image_backed_root_to_bfv,
                          root_bdm)
        self.mock_volume_api.attachment_delete.assert_called_once_with(
            self.task.context, uuids.attachment)
        self.mock_compute_rpcapi.abort_cross_hv_conversion \
            .assert_not_called()

    def test_size_rounded_up(self):
        """size_bytes not evenly divisible by GiB rounds up."""
        # 10 GiB + 1 byte -> 11 GiB
        self.prep_response['size_bytes'] = 10 * 1024 * 1024 * 1024 + 1
        root_bdm = self._make_root_bdm()

        self.task._convert_image_backed_root_to_bfv(root_bdm)

        self.assertEqual(11, root_bdm.volume_size)
        call_kwargs = self.mock_volume_api.manage_existing.call_args[1]
        self.assertEqual(11, call_kwargs['ref']['size_gb'])

    def test_source_fcd_id_omitted_when_absent(self):
        """ref does not include source-id when source_fcd_id is absent."""
        self.prep_response.pop('source_fcd_id')
        root_bdm = self._make_root_bdm()

        self.task._convert_image_backed_root_to_bfv(root_bdm)

        call_kwargs = self.mock_volume_api.manage_existing.call_args[1]
        self.assertNotIn('source-id', call_kwargs['ref'])
        self.assertIn('source-name', call_kwargs['ref'])
        self.assertIn('size_gb', call_kwargs['ref'])

    def test_image_ref_preserved(self):
        """instance.image_ref must not be cleared."""
        self.instance.image_ref = 'previous-image-ref'
        root_bdm = self._make_root_bdm()

        self.task._convert_image_backed_root_to_bfv(root_bdm)

        self.assertEqual('previous-image-ref', self.instance.image_ref)


class CrossHvExecuteIntegrationTestCase(test.NoDBTestCase):
    """Tests for conversion ordering in MigrationTask._execute().

    Exercises the full _execute() path with controlled mocks to verify
    that conversion happens before prep_resize, BFV skips conversion,
    unsupported roots are rejected, and cross-cell cross-HV raises.
    """

    HV_VMWARE = 'VMware vCenter Server'
    HV_CH = 'CH'

    def setUp(self):
        super().setUp()
        CrossHvImageConversionTestCase._ensure_cross_hv_conf()
        self.flags(enable_cross_hv_resize=True, group='workarounds')
        self.flags(fcd_volume_type='vmware', group='cross_hv')

        self.ctx = FakeContext('fake', 'fake')
        self.ctx.cell_uuid = uuids.cell1

        flavor = fake_flavor.fake_flavor_obj(self.ctx)
        flavor.extra_specs = {'capabilities:hypervisor_type': self.HV_CH}
        inst = fake_instance.fake_db_instance(flavor=flavor)
        inst_obj = objects.Instance(
            flavor=flavor, numa_topology=None,
            pci_requests=None, system_metadata={},
            image_ref=uuids.image)
        self.instance = objects.Instance._from_db_object(
            self.ctx, inst_obj, inst, [])
        self.instance.power_state = power_state.RUNNING
        self.instance.save = mock.Mock()
        self.instance.drop_migration_context = mock.Mock()

        self.request_spec = objects.RequestSpec(image=objects.ImageMeta())
        self.request_spec.is_bfv = False

        self.mock_compute_rpcapi = mock.Mock()
        self.mock_volume_api = mock.Mock()
        self.mock_network_api = mock.Mock()
        self.mock_network_api.get_requested_resource_for_instance \
            .return_value = ([], objects.RequestLevelParams())

        self.task = migrate.MigrationTask(
            self.ctx, self.instance, flavor, self.request_spec,
            clean_shutdown=True,
            compute_rpcapi=self.mock_compute_rpcapi,
            query_client=query.SchedulerQueryClient(),
            report_client=report.SchedulerReportClient(),
            host_list=None,
            network_api=self.mock_network_api,
            volume_api=self.mock_volume_api)

        source_cn = mock.Mock()
        source_cn.hypervisor_type = self.HV_VMWARE
        self.task._source_cn = source_cn

        self.selection = objects.Selection(
            service_host='kvm-host', nodename='kvm-node',
            cell_uuid=uuids.cell1, availability_zone='az1')

    def _make_bdm(self, source_type, destination_type, volume_id=None):
        bdm = mock.Mock()
        bdm.source_type = source_type
        bdm.destination_type = destination_type
        bdm.volume_id = volume_id
        return bdm

    def _execute_with_mocks(self, root_bdm):
        """Run _execute() with all external dependencies patched."""
        patches = [
            mock.patch('nova.compute.utils.heal_reqspec_is_bfv'),
            mock.patch('nova.compute.utils.sanitize_image_props_for_kvm',
                       return_value={}),
            mock.patch.object(objects.RequestSpec,
                               'ensure_network_information'),
            mock.patch.object(self.task, '_preallocate_migration',
                              return_value=mock.Mock(id=1,
                                                    uuid=uuids.migration)),
            mock.patch.object(self.task, '_schedule',
                              return_value=self.selection),
            mock.patch.object(self.task, '_is_selected_host_in_source_cell',
                              return_value=True),
            mock.patch.object(self.task, '_persist_image_properties_journal'),
            mock.patch.object(self.task, '_get_root_bdm',
                              return_value=root_bdm),
            mock.patch('nova.scheduler.utils.setup_instance_group'),
            mock.patch('nova.scheduler.utils.populate_retry'),
            mock.patch('nova.scheduler.utils.populate_filter_properties'),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            self.task.execute()

    @mock.patch.object(migrate.MigrationTask,
                       '_convert_image_backed_root_to_bfv')
    def test_image_backed_triggers_conversion_before_prep_resize(
            self, mock_convert):
        """image/local root: conversion called, then prep_resize."""
        root_bdm = self._make_bdm('image', 'local')
        call_order = []
        mock_convert.side_effect = lambda _: call_order.append('convert')
        self.mock_compute_rpcapi.prep_resize.side_effect = (
            lambda *a, **k: call_order.append('prep_resize'))

        self._execute_with_mocks(root_bdm)

        self.assertEqual(['convert', 'prep_resize'], call_order)

    @mock.patch.object(migrate.MigrationTask,
                       '_convert_image_backed_root_to_bfv')
    def test_bfv_root_skips_conversion(self, mock_convert):
        """volume-backed root: conversion skipped, prep_resize called."""
        root_bdm = self._make_bdm('volume', 'volume', volume_id=uuids.vol)

        self._execute_with_mocks(root_bdm)

        mock_convert.assert_not_called()
        self.mock_compute_rpcapi.prep_resize.assert_called_once()

    def test_unsupported_root_raises_before_prep_resize(self):
        """blank/local root: ValidationError before prep_resize."""
        root_bdm = self._make_bdm('blank', 'local')

        self.assertRaises(
            exception.InvalidCrossHvResizePrecondition,
            self._execute_with_mocks, root_bdm)

        self.mock_compute_rpcapi.prep_resize.assert_not_called()

    def test_cross_hv_cross_cell_raises_before_conversion(self):
        """Cross-HV resize must be same-cell; cross-cell selection raises."""
        cross_cell_selection = objects.Selection(
            service_host='kvm-host-other-cell', nodename='kvm-node',
            cell_uuid=uuids.cell2, availability_zone='az1')

        base_patches = [
            mock.patch('nova.compute.utils.heal_reqspec_is_bfv'),
            mock.patch('nova.compute.utils.sanitize_image_props_for_kvm',
                       return_value={}),
            mock.patch.object(objects.RequestSpec,
                               'ensure_network_information'),
            mock.patch.object(self.task, '_preallocate_migration',
                              return_value=mock.Mock(id=1,
                                                    uuid=uuids.migration)),
            mock.patch.object(self.task, '_schedule',
                              return_value=cross_cell_selection),
            mock.patch.object(self.task, '_is_selected_host_in_source_cell',
                              return_value=False),
            mock.patch.object(self.task, '_persist_image_properties_journal'),
            mock.patch.object(self.task, '_get_root_bdm',
                              return_value=self._make_bdm('image', 'local')),
            mock.patch('nova.scheduler.utils.setup_instance_group'),
            mock.patch('nova.scheduler.utils.populate_retry'),
            mock.patch('nova.scheduler.utils.populate_filter_properties'),
        ]
        with contextlib.ExitStack() as stack:
            for p in base_patches:
                stack.enter_context(p)
            mock_conv = stack.enter_context(
                mock.patch.object(migrate.MigrationTask,
                                  '_convert_image_backed_root_to_bfv'))
            self.assertRaises(
                exception.InvalidCrossHvResizePrecondition,
                self.task.execute)

        mock_conv.assert_not_called()
        self.mock_compute_rpcapi.prep_resize.assert_not_called()

    @mock.patch.object(migrate.MigrationTask,
                       '_convert_image_backed_root_to_bfv')
    def test_conversion_failure_blocks_prep_resize(self, mock_convert):
        """If conversion raises, prep_resize must not be called."""
        root_bdm = self._make_bdm('image', 'local')
        mock_convert.side_effect = exception.VolumeMigrationError(
            volume_id=uuids.vol, reason='manage failed')

        self.assertRaises(
            exception.VolumeMigrationError,
            self._execute_with_mocks, root_bdm)

        self.mock_compute_rpcapi.prep_resize.assert_not_called()

    def _prep_result(self):
        return {
            'size_bytes': 10 * units.Gi,
            'vmdk_path': '[ds1] fake/fake.vmdk',
            'cinder_host': 'host1',
        }

    def test_rollback_clears_cross_hv_markers_when_prep_rejected(self):
        """rollback() clears markers when prep_cross_hv_conversion is
        rejected before it runs (e.g. an unpatched host rejects the RPC
        version). The source was never touched, so it is safe to clear
        the markers. Otherwise a later, unrelated confirm_migration()
        could mistake this instance for one still mid conversion.
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.side_effect = (
            exception.UnsupportedRPCVersion(api='compute',
                                             required='6.2.1'))

        self.assertRaises(
            exception.UnsupportedRPCVersion,
            self._execute_with_mocks, root_bdm)

        self.assertNotIn('cross_hv_resize', self.instance.system_metadata)
        self.assertNotIn(
            'cross_hv_root_detach_state', self.instance.system_metadata)
        self.instance.drop_migration_context.assert_called_once()
        self.mock_compute_rpcapi.prep_resize.assert_not_called()

    def test_rollback_preserves_markers_when_prep_outcome_unknown(self):
        """rollback() must not touch markers when the RPC outcome is
        unknown, for example after a timeout. The disk may already be
        detached, so treat this like a completed prep and keep the
        markers for manual recovery.
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.side_effect = (
            messaging.MessagingTimeout('timed out'))

        self.assertRaises(
            messaging.MessagingTimeout,
            self._execute_with_mocks, root_bdm)

        self.assertEqual(
            'true', self.instance.system_metadata['cross_hv_resize'])
        self.assertEqual(
            'unknown',
            self.instance.system_metadata['cross_hv_root_detach_state'])
        self.instance.drop_migration_context.assert_not_called()

    def test_rollback_preserves_markers_after_volume_manage_failed_no_abort(
            self):
        """rollback() must not touch markers once the source is prepared.

        A successful prep_cross_hv_conversion powers off the source VM
        and sets cross_hv_root_detach_state to 'detached'. If
        manage_existing then raises VolumeManageFailedNoAbort, abort is
        unsafe. The source stays in that state for manual recovery, so
        rollback() must not erase the markers.
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.return_value = (
            self._prep_result())
        self.mock_volume_api.manage_existing.side_effect = (
            exception.VolumeManageFailedNoAbort(
                volume_id=uuids.vol, reason='manage failed'))

        self.assertRaises(
            exception.VolumeManageFailedNoAbort,
            self._execute_with_mocks, root_bdm)

        self.assertEqual(
            'true', self.instance.system_metadata['cross_hv_resize'])
        self.assertEqual(
            'detached',
            self.instance.system_metadata['cross_hv_root_detach_state'])
        self.instance.drop_migration_context.assert_not_called()
        self.mock_compute_rpcapi.abort_cross_hv_conversion.\
            assert_not_called()

    def test_rollback_clears_markers_after_successful_abort(self):
        """rollback() clears markers after a successful abort.

        If manage_existing raises the abortable VolumeManageFailed,
        _convert_image_backed_root_to_bfv calls abort_cross_hv_
        conversion and clears cross_hv_root_detach_state. The source is
        back to normal, so rollback() can clear the remaining markers
        and the migration context.
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.return_value = (
            self._prep_result())
        self.mock_volume_api.manage_existing.side_effect = (
            exception.VolumeManageFailed(
                volume_id=uuids.vol, reason='manage failed'))

        self.assertRaises(
            exception.VolumeManageFailed,
            self._execute_with_mocks, root_bdm)

        self.mock_compute_rpcapi.abort_cross_hv_conversion.\
            assert_called_once()
        self.assertNotIn('cross_hv_resize', self.instance.system_metadata)
        self.assertNotIn(
            'cross_hv_root_detach_state', self.instance.system_metadata)
        self.instance.drop_migration_context.assert_called_once()

    def test_rollback_preserves_markers_when_abort_itself_fails(self):
        """rollback() must not touch markers if abort_cross_hv_
        conversion itself raises. The source disk state is now unknown,
        so keep the markers instead of assuming the abort worked.
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.return_value = (
            self._prep_result())
        self.mock_volume_api.manage_existing.side_effect = (
            exception.VolumeManageFailed(
                volume_id=uuids.vol, reason='manage failed'))
        self.mock_compute_rpcapi.abort_cross_hv_conversion.side_effect = (
            messaging.MessagingTimeout('timed out'))

        self.assertRaises(
            messaging.MessagingTimeout,
            self._execute_with_mocks, root_bdm)

        self.assertEqual(
            'true', self.instance.system_metadata['cross_hv_resize'])
        self.assertEqual(
            'detached',
            self.instance.system_metadata['cross_hv_root_detach_state'])
        self.instance.drop_migration_context.assert_not_called()

    def test_rollback_preserves_markers_when_attachment_create_fails(self):
        """rollback() must not touch markers once the source is detached.

        attachment_create failing happens after the source RPC already
        succeeded, so cross_hv_root_detach_state is 'detached' and the
        markers must survive rollback().
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.return_value = (
            self._prep_result())
        self.mock_volume_api.manage_existing.return_value = {'id': uuids.vol}
        self.mock_volume_api.attachment_create.side_effect = (
            exception.NovaException())

        self.assertRaises(
            exception.NovaException,
            self._execute_with_mocks, root_bdm)

        self.assertEqual(
            'true', self.instance.system_metadata['cross_hv_resize'])
        self.assertEqual(
            'detached',
            self.instance.system_metadata['cross_hv_root_detach_state'])
        self.instance.drop_migration_context.assert_not_called()

    def test_rollback_restores_image_properties_from_journal(self):
        """rollback() restores sanitized image properties from the
        journal when the source was never touched, so the request_spec
        is not left with VMware-only values pointed at a KVM/CH host.
        """
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.side_effect = (
            exception.UnsupportedRPCVersion(api='compute',
                                             required='6.2.1'))
        self.request_spec.image.properties = objects.ImageMetaProps(
            hw_disk_bus='virtio')
        self.request_spec.save = mock.Mock()
        self.instance.migration_context = objects.MigrationContext(
            old_image_properties={'hw_disk_bus': 'ide'})

        with mock.patch.object(
                compute_utils, 'restore_image_props_from_cross_hv_journal',
                wraps=compute_utils.restore_image_props_from_cross_hv_journal
        ) as mock_restore:
            self.assertRaises(
                exception.UnsupportedRPCVersion,
                self._execute_with_mocks, root_bdm)

        mock_restore.assert_called_once_with(
            request_spec=self.request_spec,
            old_image_properties={'hw_disk_bus': 'ide'})
        self.assertEqual(
            'ide', self.request_spec.image.properties.hw_disk_bus)
        # In-memory only: the sanitized spec was never persisted, so
        # rollback must not write the abandoned resize's spec back.
        self.request_spec.save.assert_not_called()

    def test_rollback_cleans_up_markers_when_allocation_revert_fails(self):
        """rollback() must clean up cross-HV markers even if the
        allocation revert itself raises. A failure there must not skip
        the marker cleanup, since that would leave the stale markers
        this fix exists to remove.
        """
        self.instance.system_metadata['cross_hv_resize'] = 'true'
        self.task._migration = mock.Mock(status=None)
        self.task._held_allocations = True
        self.task._source_cn = mock.Mock()

        with mock.patch.object(
                migrate, 'revert_allocation_for_migration',
                side_effect=exception.NovaException()):
            self.assertRaises(
                exception.NovaException, self.task.rollback, Exception())

        self.assertNotIn('cross_hv_resize', self.instance.system_metadata)
        self.instance.drop_migration_context.assert_called_once()

    def test_failed_conversion_does_not_persist_destination_az(self):
        """The conversion saves the instance before and after its source
        RPC. The destination availability_zone must not be assigned
        until after the conversion, or a rejected prep would commit the
        destination AZ for a resize that never happened, leaving a
        running instance reporting the wrong AZ.
        """
        self.instance.availability_zone = 'az-source'
        root_bdm = self._make_bdm('image', 'local')
        self.mock_compute_rpcapi.prep_cross_hv_conversion.side_effect = (
            exception.UnsupportedRPCVersion(api='compute',
                                            required='6.2.1'))
        seen = []
        self.instance.save.side_effect = (
            lambda *a, **kw: seen.append(self.instance.availability_zone))

        self.assertRaises(
            exception.UnsupportedRPCVersion,
            self._execute_with_mocks, root_bdm)

        self.assertTrue(seen, 'expected at least one instance.save()')
        self.assertEqual(['az-source'] * len(seen), seen)
