# Copyright 2025 SAP SE or an SAP affiliate company.
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

# Tests for external scheduler api.

from unittest.mock import call
from unittest.mock import MagicMock
from unittest.mock import patch

import requests

import nova.conf
from nova import context
from nova import objects
from nova.scheduler.external import call_external_scheduler_api
from nova import test
from nova.tests.unit.scheduler import fakes

CONF = nova.conf.CONF


class ExternalSchedulerAPITestCase(test.NoDBTestCase):
    def setUp(self):
        super(ExternalSchedulerAPITestCase, self).setUp()
        self.flags(
            external_scheduler_api_url='http://127.0.0.1:1234',
            group='filter_scheduler'
        )
        self.example_hosts = [
            fakes.FakeHostState('host1', 'node1', {'status': 'up'}),
            fakes.FakeHostState('host2', 'node2', {'status': 'up'}),
            fakes.FakeHostState('host3', 'node3', {'status': 'down'})
        ]
        self.example_weights = {
            'host1': 1.0,
            'host2': 0.5,
            'host3': 0.0,
        }
        self.example_ctx = context.RequestContext(
            user_id='fake_user',
            project_id='fake_project',
            is_admin=False,
            read_deleted='no',
            global_request_id='fake_global_request_id',
            # Sensitive data to be removed.
            auth_token='fake_auth_token',
        )
        self.example_spec = objects.RequestSpec(
            context=self.example_ctx,
            flavor=objects.Flavor(
                name='small',
                vcpus=4,
                memory_mb=1024,
                extra_specs={},
            ),
            requested_destination=objects.Destination(
                host='fake_host',
                cell=objects.CellMapping(
                    uuid=objects.CellMapping.CELL0_UUID,
                    # Sensitive data to be removed.
                    database_connection='fake_db_conn',
                    transport_url='fake_transport_url',
                ),
            ),
        )

    @patch('requests.post')
    def test_context_included_in_request(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'hosts': ['host1', 'host3']}
        mock_post.return_value = mock_response

        call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )

        # Check that the context is serialized and included in the request
        _, kwargs = mock_post.call_args
        self.assertIn(
            'context', kwargs['json'],
            'Context should be included in the request'
        )
        self.assertIn(
            'global_request_id', kwargs['json']['context'],
            'Global request ID should be included in the context'
        )
        expected_dict = self.example_ctx.to_dict()
        del expected_dict['auth_token']
        self.assertEqual(
            expected_dict,
            kwargs['json']['context'],
            'Context should be serialized correctly'
        )

    @patch('requests.post')
    def test_no_credentials_in_cell_mapping(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'hosts': ['host1', 'host3']}
        mock_post.return_value = mock_response

        call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )

        # Check that sensitive data in cell mapping is removed
        _, kwargs = mock_post.call_args
        spec_data = kwargs['json']['spec']
        obj_key = "nova_object.data"
        self.assertIn('requested_destination', spec_data[obj_key])
        req_destination = spec_data[obj_key]['requested_destination']
        self.assertIn('cell', req_destination[obj_key])
        cell_mapping = req_destination[obj_key]['cell']
        self.assertNotIn('database_connection', cell_mapping[obj_key])
        self.assertNotIn('transport_url', cell_mapping[obj_key])

    @patch('requests.post')
    def test_no_credentials_in_cell_mapping_missing_keys(self, mock_post):
        """This should not raise if requested_destination or cell is missing"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'hosts': ['host1', 'host3']}
        mock_post.return_value = mock_response

        self.example_spec.requested_destination.cell = None

        call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )

        self.example_spec.requested_destination = None

        call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.debug')
    def test_enabled_api_success(self, mock_debug_log, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'hosts': ['host1', 'host3']}
        mock_post.return_value = mock_response

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_debug_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        self.assertEqual(
            ['host1', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Calling external scheduler API with ', log)

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.warning')
    def test_enabled_api_empty_response(self, mock_warn_log, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'hosts': []}
        mock_post.return_value = mock_response

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        self.assertEqual([], hosts)
        mock_warn_log.assert_called_with(
            'External scheduler filtered out all hosts.'
        )

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    def test_enabled_api_timeout(self, mock_err_log, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host2', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API (attempt 1/', log)

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    def test_enabled_api_invalid_response(self, mock_err_log, mock_post):
        invalid_response_dicts = [
            {},
            {"hosts": "not a list"},
            {"hosts": [1, 2, "host1"]},
            {"hosts": [{"name": "host1", "status": "up"}]},
        ]

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        for response_dict in invalid_response_dicts:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = response_dict
            mock_post.return_value = mock_response

            hosts = call_external_scheduler_api(
                self.example_ctx,
                self.example_hosts,
                self.example_weights,
                self.example_spec,
            )
            # Should fallback to the original host list.
            self.assertEqual(
                ['host1', 'host2', 'host3'],
                [h.host for h in hosts]
            )
            self.assertIn('External scheduler response is invalid: ', log)

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    def test_enabled_api_json_decode_err(self, mock_err_log, mock_post):
        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        mock_response = MagicMock()
        mock_response.status_code = 200
        # Note: requests.exceptions.InvalidJSONError is also a RequestException
        mock_response.json.side_effect = requests.exceptions.InvalidJSONError
        mock_post.return_value = mock_response

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host2', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API (attempt 1/', log)

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    def test_enabled_api_error_reply(self, mock_err_log, mock_post):
        mock_post.side_effect = requests.exceptions.HTTPError

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host2', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API (attempt 1/', log)

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    @patch('time.sleep')
    def test_enabled_api_error_reply_retries(self, mock_sleep, mock_err_log,
            mock_post):
        CONF.set_override('external_scheduler_retries', 2,
                          group='filter_scheduler')

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_post.side_effect = (requests.exceptions.HTTPError,
            requests.exceptions.HTTPError, requests.exceptions.HTTPError)

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host2', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API (attempt 1/', log)
        self.assertIn('Failed to call external scheduler API (attempt 2/', log)
        self.assertIn('Failed to call external scheduler API (attempt 3/', log)

        sleep_time = \
            CONF.filter_scheduler.external_scheduler_retry_sleep_seconds
        mock_sleep.assert_has_calls([call(sleep_time), call(sleep_time)])

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    @patch('time.sleep')
    def test_enabled_api_error_reply_retries_and_working(self, mock_sleep,
            mock_err_log, mock_post):
        CONF.set_override('external_scheduler_retries', 2,
                          group='filter_scheduler')

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'hosts': ['host1', 'host3']}

        mock_post.side_effect = (requests.exceptions.HTTPError,
            requests.exceptions.HTTPError, mock_response)

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )
        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API (attempt 1/', log)
        self.assertIn('Failed to call external scheduler API (attempt 2/', log)
        self.assertNotIn('Failed to call external scheduler API (attempt 3/',
                         log)

        sleep_time = \
            CONF.filter_scheduler.external_scheduler_retry_sleep_seconds
        mock_sleep.assert_has_calls([call(sleep_time), call(sleep_time)])

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    @patch('time.sleep')
    def test_enabled_api_error_reply_retries_no_sleep(self, mock_sleep,
            mock_err_log, mock_post):
        CONF.set_override('external_scheduler_retries', 1,
                          group='filter_scheduler')
        CONF.set_override('external_scheduler_retry_sleep_seconds', -1,
                          group='filter_scheduler')

        mock_post.side_effect = (requests.exceptions.HTTPError,
            requests.exceptions.HTTPError)

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )

        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host2', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API (attempt 1/', log)
        self.assertIn('Failed to call external scheduler API (attempt 2/', log)

        mock_sleep.assert_not_called()

    @patch('requests.post')
    @patch('nova.scheduler.external.LOG.error')
    def test_enabled_api_error_reply_no_retry_on_400(self, mock_err_log,
            mock_post):
        CONF.set_override('external_scheduler_retries', 1,
                          group='filter_scheduler')

        mock_error = requests.exceptions.HTTPError()
        mock_error.response = MagicMock()
        mock_error.response.status_code = 400

        mock_post.side_effect = mock_error

        log = ""

        def append_log(msg, *data):
            nonlocal log
            log += msg % data
        mock_err_log.side_effect = append_log

        hosts = call_external_scheduler_api(
            self.example_ctx,
            self.example_hosts,
            self.example_weights,
            self.example_spec,
        )

        # Should fallback to the original host list.
        self.assertEqual(
            ['host1', 'host2', 'host3'],
            [h.host for h in hosts]
        )
        self.assertIn('Failed to call external scheduler API: ', log)
        self.assertNotIn('Failed to call external scheduler API (attempt 1/',
                         log)
