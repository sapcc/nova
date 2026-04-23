#   Copyright 2013 OpenStack Foundation
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

from oslo_utils.fixture import uuidsentinel as uuids
import webob

from nova.api.openstack.compute import sap_admin_api
from nova.compute import task_states
from nova.compute import vm_states
import nova.conf
from nova import exception
from nova import test
from nova.tests.unit.api.openstack import fakes
from nova.tests.unit import fake_instance


CONF = nova.conf.CONF


def fake_compute_api(*args, **kwargs):
    return True


def fake_compute_api_get(self, context, instance_id, **kwargs):
    if instance_id == uuids.not_found:
        raise exception.InstanceNotFound(instance_id=instance_id)
    else:
        return fake_instance.fake_instance_obj(context, id=1, uuid=instance_id,
                                               task_state=task_states.DELETING,
                                               host='host1',
                                               vm_state=vm_states.ACTIVE)


class EvacuateDeleteTestV295(test.NoDBTestCase):
    validation_error = exception.ValidationError
    _method = 'evacuate_delete'

    def setUp(self):
        super(EvacuateDeleteTestV295, self).setUp()
        self.stub_out('nova.compute.api.API.get', fake_compute_api_get)
        self.UUID = uuids.fake
        self.stub_out('nova.compute.api.API.%s' %
                      self._method, fake_compute_api)
        self.controller = sap_admin_api.SAPAdminApiController()
        self.admin_req = fakes.HTTPRequest.blank('', use_admin_context=True)
        self.req = fakes.HTTPRequest.blank('')

    def _get_evacuate_delete_response(self, body, uuid=None):
        body['instance_uuid'] = uuid or self.UUID
        return self.controller.evacuate_delete(self.admin_req, body=body)

    def _check_evacuate_delete_failure(self, exception, body, uuid=None):
        body['instance_uuid'] = uuid or self.UUID
        return self.assertRaises(exception, self.controller.evacuate_delete,
                                 self.admin_req, body=body)

    def test_evacuate_delete_with_valid_instance(self):
        res = self._get_evacuate_delete_response({})

        self.assertIsNone(res)

    def test_evacuate_delete_with_invalid_instance(self):
        self._check_evacuate_delete_failure(webob.exc.HTTPNotFound, {},
                                            uuid=uuids.not_found)

    def test_evacuate_with_active_service(self):
        def fake_evacuate_delete(*args, **kwargs):
            raise exception.ComputeServiceInUse("Service still in use")

        self.stub_out('nova.compute.api.API.evacuate_delete',
                      fake_evacuate_delete)
        self._check_evacuate_delete_failure(webob.exc.HTTPBadRequest, {})
