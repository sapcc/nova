# Copyright (c) 2026 SAP SE
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

from datetime import datetime
from datetime import timezone
from unittest import mock

import ddt
import webob.exc

from nova.api.openstack.compute import (
    flavor_permission_rules as fpr_api)
import nova.conf
from nova import exception
from nova.objects import fields
from nova.policies import flavor_permission_rules as fpr_policies
from nova import test
from nova.tests.unit.api.openstack import fakes
from nova.tests.unit import fake_flavor_permission_rule as fake_rule
from nova import utils


CONF = nova.conf.CONF

ROOT = fpr_policies.POLICY_ROOT
DOMAIN_ID = 'fake-domain'
PROJECT_ID = fakes.FAKE_PROJECT_ID
ALLOW = fields.FlavorPermissionRuleEffect.ALLOW
DENY = fields.FlavorPermissionRuleEffect.DENY
SCOPE_DOMAIN = fields.FlavorPermissionRuleScope.DOMAIN
SCOPE_PROJECT = fields.FlavorPermissionRuleScope.PROJECT


def _make_can(allowed_suffixes):
    """Return a context.can side_effect allowing specific policy suffixes."""
    def can(action, target=None, fatal=True):
        if any(action == ROOT % s for s in allowed_suffixes):
            return True
        if fatal:
            raise exception.PolicyNotAuthorized(action=action)
        return False
    return can


def _mock_create_rule_obj(rule_obj):
    rule_obj.created_at = datetime(2026, 2, 6)
    rule_obj.updated_at = None


@ddt.ddt
class FlavorPermissionRulesControllerTest(test.NoDBTestCase):

    def setUp(self):
        super().setUp()
        self.controller = fpr_api.FlavorPermissionRulesController()
        patcher = mock.patch(
            'nova.objects.FlavorList.get_flavor_ids_by_ids', return_value={})
        self.mock_flavorids = patcher.start()
        self.addCleanup(patcher.stop)

    def _req(self, *allowed_policies, qs='', version='2.100'):
        """Build a request whose context allows the given policy suffixes."""
        url = '/flavor-permission-rules'
        if qs:
            url += '?' + qs
        req = fakes.HTTPRequest.blank(url, version=version)
        ctx = req.environ['nova.context']
        ctx.project_domain_id = DOMAIN_ID
        ctx.can = mock.Mock(side_effect=_make_can(allowed_policies))
        return req

    def _rule_obj(self, req, **db_updates):
        ctx = req.environ['nova.context']
        db = fake_rule.fake_db_flavor_permission_rule(**db_updates)
        return fake_rule.fake_flavor_permission_rule_obj(ctx, db)

    def test_version_not_found_below_v2100(self):
        req = self._req(version='2.99')
        fake_id = 'some-uuid'
        for method, args, kwargs in [
            (self.controller.index, (req,), {}),
            (self.controller.show, (req, fake_id), {}),
            (self.controller.create, (req,), {'body': {}}),
            (self.controller.update, (req, fake_id), {'body': {}}),
            (self.controller.delete, (req, fake_id), {}),
        ]:
            self.assertRaises(exception.VersionNotFoundForAPIMethod,
                              method, *args, **kwargs)

    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_all_access(self, mock_get_all):
        mock_get_all.return_value = []
        self.controller.index(self._req('index:all'))
        mock_get_all.assert_called_once_with(
            mock.ANY,
            domain_id=None, project_id=None,
            scope=None, effect=None,
            flavor_id=None, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)

    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_domain_access(self, mock_get_all):
        mock_get_all.return_value = []
        self.controller.index(self._req('index:domain'))
        mock_get_all.assert_called_once_with(
            mock.ANY,
            domain_id=DOMAIN_ID, project_id=None,
            scope=None, effect=None,
            flavor_id=None, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)

    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_project_access(self, mock_get_all):
        mock_get_all.return_value = []
        self.controller.index(self._req('index:project'))
        mock_get_all.assert_called_once_with(
            mock.ANY,
            domain_id=DOMAIN_ID, project_id=PROJECT_ID,
            scope=SCOPE_PROJECT, effect=None,
            flavor_id=None, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)

    @ddt.data(
        ('index:project', 'scope=' + SCOPE_DOMAIN),
        ('index:project', 'project_id=some-project'),
        ('index:project', 'domain_id=some-domain'),
        ('index:domain', 'domain_id=some-domain'),
    )
    @ddt.unpack
    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_filter_forbidden(self, policy, qs, mock_get_all):
        req = self._req(policy, qs=qs)
        self.assertRaises(webob.exc.HTTPForbidden,
                          self.controller.index, req)

    @ddt.data(
        ('index:project', 'project_id'),
        ('index:project', 'project_domain_id'),
        ('index:domain', 'project_domain_id'),
    )
    @ddt.unpack
    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_no_context_forbidden(self, policy, ctx_attr, mock_get_all):
        req = self._req(policy)
        setattr(req.environ['nova.context'], ctx_attr, None)
        self.assertRaises(webob.exc.HTTPForbidden,
                          self.controller.index, req)

    @ddt.data(
        ('index:domain', DOMAIN_ID),
        ('index:all', 'some-domain'),
    )
    @ddt.unpack
    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_domain_id_filter(self, policy, domain_id, mock_get_all):
        mock_get_all.return_value = []
        self.controller.index(self._req(policy, qs='domain_id=' + domain_id))
        mock_get_all.assert_called_once_with(
            mock.ANY,
            domain_id=domain_id, project_id=None,
            scope=None, effect=None,
            flavor_id=None, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)

    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_own_project_id_filter_for_project_access(
            self, mock_get_all):
        mock_get_all.return_value = []
        self.controller.index(
            self._req('index:project', qs='project_id=' + PROJECT_ID))
        mock_get_all.assert_called_once_with(
            mock.ANY,
            domain_id=DOMAIN_ID, project_id=PROJECT_ID,
            scope=SCOPE_PROJECT, effect=None,
            flavor_id=None, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)

    @ddt.data(
        ('project_id=some-project', {'project_id': 'some-project'}),
        ('effect=' + DENY, {'effect': DENY}),
        ('has_flavor=false', {'has_flavor': False}),
        ('has_flavor=true', {'has_flavor': True}),
    )
    @ddt.unpack
    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_single_param_filter(self, qs, overrides, mock_get_all):
        mock_get_all.return_value = []
        self.controller.index(self._req('index:all', qs=qs))
        expected = dict(
            domain_id=None, project_id=None,
            scope=None, effect=None,
            flavor_id=None, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)
        expected.update(overrides)
        mock_get_all.assert_called_once_with(mock.ANY, **expected)

    @mock.patch('nova.api.openstack.common.get_flavor')
    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_flavor_id_resolved(self, mock_get_all, mock_get_flavor):
        mock_get_all.return_value = []
        fake_flavor = mock.Mock()
        fake_flavor.id = 42
        mock_get_flavor.return_value = fake_flavor
        self.controller.index(
            self._req('index:all', qs='flavor_id=1'))
        mock_get_flavor.assert_called_once_with(
            mock.ANY, '1',
            domain_permission=None,
            project_permission=None)
        mock_get_all.assert_called_once_with(
            mock.ANY,
            domain_id=None, project_id=None,
            scope=None, effect=None,
            flavor_id=42, has_flavor=None,
            limit=CONF.api.max_limit, marker=None)

    @mock.patch('nova.api.openstack.common.get_flavor')
    def test_index_flavor_id_not_found(self, mock_get_flavor):
        mock_get_flavor.side_effect = webob.exc.HTTPNotFound
        req = self._req('index:all', qs='flavor_id=nonexistent')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.index, req)

    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_marker_not_found(self, mock_get_all):
        mock_get_all.side_effect = exception.MarkerNotFound(
            marker='bad-marker')
        req = self._req('index:all', qs='marker=bad-marker')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.index, req)

    @mock.patch('nova.api.openstack.common.get_flavor')
    def test_index_has_flavor_and_flavor_id_exclusive(self, mock_get_flavor):
        fake_flavor = mock.Mock()
        fake_flavor.id = 42
        mock_get_flavor.return_value = fake_flavor
        req = self._req('index:all', qs='flavor_id=1&has_flavor=true')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.index, req)

    @mock.patch('nova.objects.FlavorPermissionRuleList.get_all')
    def test_index_returns_rules(self, mock_get_all):
        req = self._req('index:all')
        rule = self._rule_obj(req)
        mock_get_all.return_value = [rule]
        result = self.controller.index(req)
        rules = result['flavor_permission_rules']
        self.assertEqual(1, len(rules))
        self.assertEqual(rule.uuid, rules[0]['id'])
        self.assertEqual(rule.domain_id, rules[0]['domain_id'])
        self.assertEqual(rule.effect, rules[0]['effect'])
        self.assertEqual(utils.isotime(rule.created_at),
                         rules[0]['created_at'])
        self.assertIsNone(rules[0]['updated_at'])

    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_show_domain_rule(self, mock_get):
        req = self._req('show:domain')
        rule = self._rule_obj(req, project_id=None)
        mock_get.return_value = rule
        result = self.controller.show(req, rule.uuid)
        rule_dict = result['flavor_permission_rule']
        self.assertEqual(rule.uuid, rule_dict['id'])
        self.assertEqual(SCOPE_DOMAIN, rule_dict['scope'])
        self.assertIsNone(rule_dict['project_id'])
        self.assertEqual(utils.isotime(rule.created_at),
                         rule_dict['created_at'])
        self.assertIsNone(rule_dict['updated_at'])

    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_show_project_rule(self, mock_get):
        updated = datetime(2026, 6, 17, tzinfo=timezone.utc)
        req = self._req('show:project')
        rule = self._rule_obj(req, updated_at=updated)
        mock_get.return_value = rule
        result = self.controller.show(req, rule.uuid)
        rule_dict = result['flavor_permission_rule']
        self.assertEqual(SCOPE_PROJECT, rule_dict['scope'])
        self.assertEqual(utils.isotime(updated), rule_dict['updated_at'])

    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_show_translates_flavor_id(self, mock_get):
        req = self._req('show:project')
        rule = self._rule_obj(req, flavor_id=123)
        mock_get.return_value = rule
        self.mock_flavorids.return_value = {123: '1'}
        result = self.controller.show(req, rule.uuid)
        rule_dict = result['flavor_permission_rule']
        self.assertEqual('1', rule_dict['flavor_id'])
        self.mock_flavorids.assert_called_once_with(
            mock.ANY, {123},
            domain_permission=None,
            project_permission=None)

    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_show_nonexistent(self, mock_get):
        mock_get.side_effect = exception.FlavorPermissionRuleNotFound(
            id='nonexistent-uuid')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.show,
                          self._req('show:domain', 'show:project'),
                          'nonexistent-uuid')

    @mock.patch('nova.objects.FlavorPermissionRule.create',
        new=_mock_create_rule_obj)
    def test_create_domain_scope(self):
        req = self._req('create:domain')
        body = {'flavor_permission_rule': {
            'domain_id': DOMAIN_ID,
            'effect': ALLOW,
        }}
        result = self.controller.create(req, body=body)
        rule_dict = result['flavor_permission_rule']
        self.assertEqual(DOMAIN_ID, rule_dict['domain_id'])
        self.assertIsNone(rule_dict['project_id'])
        self.assertEqual(SCOPE_DOMAIN, rule_dict['scope'])
        self.assertEqual(ALLOW, rule_dict['effect'])

    @mock.patch('nova.objects.FlavorPermissionRule.create',
        new=_mock_create_rule_obj)
    def test_create_project_scope(self):
        req = self._req('create:project')
        body = {'flavor_permission_rule': {
            'domain_id': DOMAIN_ID,
            'project_id': PROJECT_ID,
            'effect': DENY,
        }}
        result = self.controller.create(req, body=body)
        rule_dict = result['flavor_permission_rule']
        self.assertEqual(DOMAIN_ID, rule_dict['domain_id'])
        self.assertEqual(PROJECT_ID, rule_dict['project_id'])
        self.assertEqual(SCOPE_PROJECT, rule_dict['scope'])
        self.assertEqual(DENY, rule_dict['effect'])

    @mock.patch('nova.api.openstack.common.get_flavor')
    @mock.patch('nova.objects.FlavorPermissionRule.create',
        new=_mock_create_rule_obj)
    def test_create_with_flavor_id(self, mock_get_flavor):
        fake_flavor = mock.Mock()
        fake_flavor.id = 99
        fake_flavor.flavorid = '1'
        fake_flavor.is_public = True
        mock_get_flavor.return_value = fake_flavor
        req = self._req('create:domain')
        body = {'flavor_permission_rule': {
            'domain_id': DOMAIN_ID,
            'effect': DENY,
            'flavor_id': '1',
        }}
        result = self.controller.create(req, body=body)
        mock_get_flavor.assert_called_once_with(
            mock.ANY, '1',
            domain_permission=None,
            project_permission=None)
        self.assertEqual(
            '1', result['flavor_permission_rule']['flavor_id'])

    @mock.patch('nova.api.openstack.common.get_flavor')
    def test_create_flavor_not_found(self, mock_get_flavor):
        mock_get_flavor.side_effect = webob.exc.HTTPNotFound
        req = self._req('create:domain')
        body = {'flavor_permission_rule': {
            'domain_id': DOMAIN_ID,
            'effect': ALLOW,
            'flavor_id': 'nonexistent',
        }}
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.create, req, body=body)

    @mock.patch('nova.api.openstack.common.get_flavor')
    def test_create_private_flavor_raises_bad_request(self, mock_get_flavor):
        fake_flavor = mock.Mock()
        fake_flavor.is_public = False
        mock_get_flavor.return_value = fake_flavor
        req = self._req('create:domain')
        body = {'flavor_permission_rule': {
            'domain_id': DOMAIN_ID,
            'effect': DENY,
            'flavor_id': '2',
        }}
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.create, req, body=body)

    @mock.patch('nova.objects.FlavorPermissionRule.create')
    def test_create_duplicate(self, mock_create):
        mock_create.side_effect = exception.FlavorPermissionRuleExists(
            uuid='fake-uuid', project_id=None, flavor_id=None)
        req = self._req('create:domain')
        body = {'flavor_permission_rule': {
            'domain_id': DOMAIN_ID,
            'effect': ALLOW,
        }}
        self.assertRaises(webob.exc.HTTPConflict,
                          self.controller.create, req, body=body)

    @mock.patch('nova.objects.FlavorPermissionRule.save')
    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_update_success(self, mock_get, mock_save):
        req = self._req('update:project')
        rule = self._rule_obj(req)
        mock_get.return_value = rule
        body = {'flavor_permission_rule': {'effect': DENY}}
        result = self.controller.update(req, rule.uuid, body=body)
        self.assertEqual(DENY, result['flavor_permission_rule']['effect'])

    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_update_nonexistent(self, mock_get):
        mock_get.side_effect = exception.FlavorPermissionRuleNotFound(
            id='nonexistent-uuid')
        req = self._req('update:domain', 'update:project')
        body = {'flavor_permission_rule': {'effect': DENY}}
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.update, req,
                          'nonexistent-uuid', body=body)

    @mock.patch('nova.objects.FlavorPermissionRule.destroy')
    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_delete_success(self, mock_get, mock_destroy):
        req = self._req('delete:project')
        rule = self._rule_obj(req)
        mock_get.return_value = rule
        self.controller.delete(req, rule.uuid)
        mock_destroy.assert_called_once_with()

    @mock.patch('nova.objects.FlavorPermissionRule.get_by_uuid')
    def test_delete_nonexistent(self, mock_get):
        mock_get.side_effect = exception.FlavorPermissionRuleNotFound(
            id='nonexistent-uuid')
        req = self._req('delete:domain', 'delete:project')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.controller.delete, req, 'nonexistent-uuid')
