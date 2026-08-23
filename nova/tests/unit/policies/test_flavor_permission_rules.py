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

import ddt
import fixtures
from oslo_utils.fixture import uuidsentinel as uuids
import webob.exc

from nova.api.openstack.compute import (
    flavor_permission_rules as fpr_api)
from nova.api.openstack.compute import flavors as flavors_api
from nova import context as nova_context
from nova import objects
from nova.objects import fields
from nova.policies import flavor_permission_rules as fpr_policies
from nova.tests.unit.api.openstack import fakes
from nova.tests.unit import fake_flavor_permission_rule as fake_fpr
from nova.tests.unit.policies import base


RULE_DOMAIN_ID = 'test-domain'
EFFECT_ALLOW = fields.FlavorPermissionRuleEffect.ALLOW
EFFECT_DENY = fields.FlavorPermissionRuleEffect.DENY


@ddt.ddt
class FlavorPermissionRulesPolicyTest(base.BasePolicyTest):
    """Test flavor permission rules API policies with all possible contexts.

    Only admin contexts are authorized by default.
    """

    def setUp(self):
        super().setUp()
        self.controller = fpr_api.FlavorPermissionRulesController()
        self.flavors_controller = flavors_api.FlavorsController()
        self.req = fakes.HTTPRequest.blank('', version='2.100')
        # Create flavor permission rules with domain and project scope
        ctx = self.project_admin_context
        self.domain_rule = fake_fpr.fake_flavor_permission_rule_obj(
            ctx,
            fake_fpr.fake_db_flavor_permission_rule(
                domain_id=RULE_DOMAIN_ID, project_id=None))
        self.project_rule = fake_fpr.fake_flavor_permission_rule_obj(
            ctx,
            fake_fpr.fake_db_flavor_permission_rule(
                domain_id=RULE_DOMAIN_ID, project_id=self.project_id))
        # Mock the get_by_uuid method to return the domain rule by default
        self.mock_get_rule = self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorPermissionRule.get_by_uuid')).mock
        self.mock_get_rule.return_value = self.domain_rule
        # Mock other FlavorPermissionRuleList and FlavorList methods
        self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorPermissionRuleList.get_all',
            return_value=[]))
        self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorList.get_all',
            return_value=objects.FlavorList()))
        self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorList.get_flavor_ids_by_ids',
            return_value={}))

        def _mock_create(rule_obj):
            rule_obj.created_at = datetime(2026, 2, 6)
            rule_obj.updated_at = None

        self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorPermissionRule.create',
            new=_mock_create))
        self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorPermissionRule.destroy'))
        self.useFixture(fixtures.MockPatch(
            'nova.objects.FlavorPermissionRule.save'))
        # Ensure all contexts have project_domain_id and project_id
        for ctx in self.all_contexts:
            if ctx.project_domain_id is None:
                ctx.project_domain_id = RULE_DOMAIN_ID
            if ctx.project_id is None:
                ctx.project_id = self.admin_project_id
        # With legacy rules and no scope check, all admin contexts can manage
        # flavor permission rules.
        self.admin_authorized_contexts = {
            self.legacy_admin_context, self.system_admin_context,
            self.project_admin_context}

    def _scope_rule(self, scope: str):
        return self.domain_rule if scope == 'domain' else self.project_rule

    def _authorized_contexts(self, scope: str) -> set:
        return self.admin_authorized_contexts

    def _check_unauth_raises(self, authorized_contexts, func,
                              *args, exc=webob.exc.HTTPNotFound, **kwargs):
        """Check a policy where unauthorized access raises exc"""
        unauth = list(set(self.all_contexts) - set(authorized_contexts))
        for ctx in authorized_contexts:
            self.req.environ['nova.context'] = ctx
            func(self.req, *args, **kwargs)
        for ctx in unauth:
            self.req.environ['nova.context'] = ctx
            self.assertRaises(
                exc, func, self.req, *args, **kwargs)

    @ddt.data('all', 'domain', 'project')
    def test_index(self, scope):
        # Block access via the other scopes' policies to isolate the test scope
        self.policy.set_rules({
            fpr_policies.POLICY_ROOT % f'index:{s}': '!'
            for s in ('all', 'domain', 'project') if s != scope
        }, overwrite=False)
        # index:project is the terminal fatal check if index:all and
        # index:domain are not authorized
        rule_name = fpr_policies.POLICY_ROOT % 'index:project'
        self.common_policy_auth(
            self._authorized_contexts(scope),
            rule_name, self.controller.index, self.req)

    @ddt.data('domain', 'project')
    def test_show(self, scope):
        rule = self._scope_rule(scope)
        self.mock_get_rule.return_value = rule
        self._check_unauth_raises(
            self._authorized_contexts(scope),
            self.controller.show, rule.uuid)

    @ddt.data('domain', 'project')
    def test_create(self, scope):
        rule_name = fpr_policies.POLICY_ROOT % f'create:{scope}'
        data = {'domain_id': RULE_DOMAIN_ID, 'effect': EFFECT_ALLOW}
        if scope == 'project':
            data['project_id'] = self.project_id
        self.common_policy_auth(
            self._authorized_contexts(scope),
            rule_name, self.controller.create, self.req,
            body={'flavor_permission_rule': data})

    @ddt.data('domain', 'project')
    def test_delete(self, scope):
        rule = self._scope_rule(scope)
        self.mock_get_rule.return_value = rule
        self._check_unauth_raises(
            self._authorized_contexts(scope),
            self.controller.delete, rule.uuid)

    @ddt.data('domain', 'project')
    def test_update(self, scope):
        rule = self._scope_rule(scope)
        self.mock_get_rule.return_value = rule
        body = {'flavor_permission_rule': {'effect': EFFECT_DENY}}
        self._check_unauth_raises(
            self._authorized_contexts(scope),
            self.controller.update, rule.uuid, body=body)

    @ddt.data('domain', 'project')
    def test_flavor_index_permission_params(self, scope):
        # Block access via the other scopes' policies to isolate the test scope
        other = 'project' if scope == 'domain' else 'domain'
        self.policy.set_rules(
            {fpr_policies.POLICY_ROOT % f'index:{other}': '!'},
            overwrite=False)
        self.req = fakes.HTTPRequest.blank(
            f'?{scope}_permission={EFFECT_ALLOW}', version='2.100')
        self._check_unauth_raises(
            self._authorized_contexts(scope),
            self.flavors_controller.index,
            exc=webob.exc.HTTPForbidden)


class FlavorPermissionRulesNoLegacyPolicyTest(
        FlavorPermissionRulesPolicyTest):
    """Test flavor permission rules API policies without deprecated rules"""

    without_deprecated_rules = True


class FlavorPermissionRulesScopePolicyTest(
        FlavorPermissionRulesPolicyTest):
    """Test flavor permission rules API policies with scope enforcement"""

    def setUp(self):
        super().setUp()
        self.flags(enforce_scope=True, group="oslo_policy")
        # system_admin_context is unauthorized because it is system scoped and
        # the policies require project scope
        self.admin_authorized_contexts = {
            self.legacy_admin_context,
            self.project_admin_context}


class FlavorPermissionRulesScopeNoLegacyPolicyTest(
        FlavorPermissionRulesScopePolicyTest):
    """Test flavor permission rules API policies with scope enforcement and
    without deprecated rules
    """

    without_deprecated_rules = True


class FlavorPermissionRulesAdminOverridePolicyTest(
        FlavorPermissionRulesPolicyTest):
    """Test flavor permission rules API policies with custom overrides for
    delegating flavor permission rule management to scope-based global_admin,
    domain_admin and project_admin roles.
    """

    def setUp(self):
        super().setUp()
        self.flags(enforce_scope=True, group="oslo_policy")
        # Create domain and project admin context
        self.domain_admin_context = nova_context.RequestContext(
            user_id='domain_admin',
            project_id=uuids.domain_admin_project,
            project_domain_id=RULE_DOMAIN_ID,
            roles=['domain_admin'])
        self.all_contexts.add(self.domain_admin_context)
        self.project_custom_context = nova_context.RequestContext(
            user_id='project_admin_user',
            project_id=self.project_id,
            project_domain_id=RULE_DOMAIN_ID,
            roles=['project_admin'])
        self.all_contexts.add(self.project_custom_context)
        self.global_admin_context = nova_context.RequestContext(
            user_id='global_admin',
            project_id=uuids.global_admin_project,
            project_domain_id=uuids.global_admin_domain,
            roles=['global_admin'])
        self.all_contexts.add(self.global_admin_context)
        # Override the default policy rules to restrict access to the custom
        # role of the respective scope
        domain_admin_check = (
            'role:domain_admin and project_domain_id:%(project_domain_id)s')
        project_admin_check = (
            'role:project_admin and project_id:%(project_id)s')
        self.policy.set_rules({
            **{fpr_policies.POLICY_ROOT % rule: domain_admin_check
               for rule in [
                   'show:domain', 'create:domain',
                   'delete:domain', 'update:domain']},
            **{fpr_policies.POLICY_ROOT % rule: project_admin_check
               for rule in [
                   'show:project', 'create:project',
                   'delete:project', 'update:project']},
            fpr_policies.POLICY_ROOT % 'index:all': 'role:global_admin',
            fpr_policies.POLICY_ROOT % 'index:domain': 'role:domain_admin',
            fpr_policies.POLICY_ROOT % 'index:project': 'role:project_admin',
        }, overwrite=False)

    def _authorized_contexts(self, scope: str) -> set:
        return {
            'all': {self.global_admin_context},
            'domain': {self.domain_admin_context},
            'project': {self.project_custom_context},
        }[scope]
