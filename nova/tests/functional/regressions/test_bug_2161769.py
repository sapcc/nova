# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import threading

import fixtures

from nova.compute import manager as compute_manager
from nova.compute import resource_tracker
from nova.compute import vm_states
from nova import context as nova_context
from nova import exception
from nova import objects
from nova import test
from nova.tests import fixtures as nova_fixtures
from nova.tests.functional.api import client as api_client
from nova.tests.functional import fixtures as func_fixtures
from nova.tests.functional import integrated_helpers

# How long to wait for a worker thread to reach a blocking point before
# giving up. These are hit in-process and normally within milliseconds,
# so this only needs to cover a slow machine.
GATE_TIMEOUT = 60


class TestDeleteWhileBuildingBDMNotFound(
    test.TestCase, integrated_helpers.InstanceHelperMixin,
):
    """Regression test for bug 2161769.

    https://bugs.launchpad.net/nova/+bug/2161769

    Deleting a server while it is still building races with the compute
    side build. The delete API takes the local (API side) delete path if
    it reads the instance before the compute's resource claim sets its
    host. That path destroys the BDM rows in _local_cleanup_bdm_volumes()
    before destroying the instance row in instance.destroy(). If the
    compute is still in _build_resources in that window, the root BDM
    save in _default_block_device_names raises BDMNotFound while the
    instance row is still alive. This test reproduces the race
    deterministically and asserts the behavior of the build in that
    window.
    """

    def setUp(self):
        super().setUp()
        self.useFixture(nova_fixtures.GlanceFixture(self))
        self.useFixture(nova_fixtures.NeutronFixture(self))
        self.useFixture(nova_fixtures.CinderFixture(self))
        self.useFixture(func_fixtures.PlacementFixture())
        self.start_service('conductor')
        self.start_service('scheduler')
        self.api = self.useFixture(
            nova_fixtures.OSAPIFixture(api_version='v2.1')).api
        self.start_service('compute')

    def test_delete_while_building_bdm_not_found_race(self):
        # Block the build before the resource claim, which is what sets
        # instance.host. The delete API only takes the local (API side)
        # delete path, which destroys the BDM rows itself, if it reads
        # the instance before it has a host. Reaching the claim also
        # means the conductor has handed the build off to the compute,
        # so the delete will not stop at the build request instead.
        build_at_claim = threading.Event()
        let_build_continue = threading.Event()
        orig_instance_claim = resource_tracker.ResourceTracker.instance_claim

        def slow_instance_claim(rt_self, *args, **kwargs):
            build_at_claim.set()
            if not let_build_continue.wait(GATE_TIMEOUT):
                raise RuntimeError(
                    'timed out waiting to continue past the claim')
            return orig_instance_claim(rt_self, *args, **kwargs)

        # Block the delete right before it destroys the instance row.
        # The BDM rows have already been destroyed at that point, which
        # is the race window: the build's root BDM save will find no row
        # while the instance row is still visible.
        delete_at_destroy = threading.Event()
        let_destroy_continue = threading.Event()
        orig_instance_destroy = objects.Instance.destroy

        def slow_instance_destroy(inst_self, *args, **kwargs):
            delete_at_destroy.set()
            if not let_destroy_continue.wait(GATE_TIMEOUT):
                raise RuntimeError(
                    'timed out waiting to continue the destroy')
            return orig_instance_destroy(inst_self, *args, **kwargs)

        # Record whether the build's root BDM save raised BDMNotFound.
        bdm_not_found = []
        orig_default_block_device_names = (
            compute_manager.ComputeManager._default_block_device_names)

        def record_bdm_not_found(mgr_self, *args, **kwargs):
            try:
                return orig_default_block_device_names(
                    mgr_self, *args, **kwargs)
            except exception.BDMNotFound as exc:
                bdm_not_found.append(exc)
                raise

        # Signal when the build has finished.
        build_done = threading.Event()
        orig_do_build_and_run_instance = (
            compute_manager.ComputeManager._do_build_and_run_instance)

        def signal_build_done(mgr_self, *args, **kwargs):
            try:
                return orig_do_build_and_run_instance(
                    mgr_self, *args, **kwargs)
            finally:
                build_done.set()

        # NOTE: the methods above are replaced with plain functions so
        # that they are bound like the originals and still receive self
        # when called. A mock installed on a class by nova's patched
        # mock.patch is called without self, which would break calls
        # through to the original method.
        self.useFixture(fixtures.MockPatch(
            'nova.compute.resource_tracker.ResourceTracker.instance_claim',
            new=slow_instance_claim))
        self.useFixture(fixtures.MockPatch(
            'nova.objects.instance.Instance.destroy',
            new=slow_instance_destroy))
        self.useFixture(fixtures.MockPatch(
            'nova.compute.manager.ComputeManager._default_block_device_names',
            new=record_bdm_not_found))
        self.useFixture(fixtures.MockPatch(
            'nova.compute.manager.ComputeManager._do_build_and_run_instance',
            new=signal_build_done))

        # Let a blocked worker thread drain on failure instead of
        # hanging the test run.
        self.addCleanup(let_build_continue.set)
        self.addCleanup(let_destroy_continue.set)

        # Boot from volume so the server has a root BDM whose device
        # name is only defaulted, and therefore saved, during the build.
        volume_id = nova_fixtures.CinderFixture.IMAGE_BACKED_VOL
        server_request = self._build_server()
        server_request.pop('imageRef')
        server_request['block_device_mapping_v2'] = [
            {
                'boot_index': 0,
                'uuid': volume_id,
                'source_type': 'volume',
                'destination_type': 'volume',
            }
        ]
        server = self.api.post_server({'server': server_request})

        # Wait for the build to reach the pause before its claim.
        self.assertTrue(
            build_at_claim.wait(GATE_TIMEOUT),
            'timed out waiting for the build to reach the instance claim')

        # Delete the server from a separate thread. The build is blocked
        # before its claim, so the delete reads the instance without a
        # host, takes the API-local path, destroys the BDM rows, and
        # blocks right before destroying the instance row.
        delete_response = {}

        def do_delete():
            response = self.api.delete_server(server['id'])
            delete_response['status'] = response.status

        delete_thread = threading.Thread(target=do_delete)
        delete_thread.daemon = True
        delete_thread.start()
        self.assertTrue(
            delete_at_destroy.wait(GATE_TIMEOUT),
            'timed out waiting for the delete to reach instance.destroy')

        # Let the build continue. Its root BDM save finds no row and
        # raises BDMNotFound while the instance row is still visible.
        let_build_continue.set()
        self.assertTrue(
            build_done.wait(GATE_TIMEOUT),
            'timed out waiting for the build to finish')

        # The build hit the race and the root BDM save raised
        # BDMNotFound.
        self.assertEqual(1, len(bdm_not_found))

        # This is bug 2161769: BDMNotFound is not handled as a deletion
        # signal like InstanceNotFound, so the build falls through to
        # the generic exception handler, gets wrapped in a
        # BuildAbortException, and sets the still visible instance to
        # ERROR state with a recorded fault instead of exiting cleanly
        # and letting the delete finish. The delete is still blocked
        # before instance.destroy() here, so the instance row is still
        # visible.
        ctxt = nova_context.get_admin_context()
        with nova_context.target_cell(
                ctxt, self.cell_mappings['cell1']) as cctxt:
            instance = objects.Instance.get_by_uuid(cctxt, server['id'])
            self.assertEqual(vm_states.ERROR, instance.vm_state)
            faults = objects.InstanceFaultList.get_by_instance_uuids(
                cctxt, [server['id']])
            self.assertEqual(1, len(faults.objects))
            self.assertIn('Failure prepping block device',
                          faults.objects[0].message)

        # Let the delete finish destroying the instance row.
        let_destroy_continue.set()
        delete_thread.join(GATE_TIMEOUT)
        self.assertFalse(delete_thread.is_alive())
        self.assertEqual(204, delete_response['status'])

        # The server is gone from the API.
        exc = self.assertRaises(
            api_client.OpenStackApiException,
            self.api.get_server, server['id'])
        self.assertEqual(404, exc.response.status_code)
