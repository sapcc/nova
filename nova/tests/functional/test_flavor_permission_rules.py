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

import ddt

from nova.api import auth
from nova.objects import fields
from nova import test
from nova.tests import fixtures as nova_fixtures
from nova.tests.functional.api import client

ADMIN = 'admin'
PROJECT_ADMIN = 'project_admin'
DOMAIN_ADMIN = 'domain_admin'


@ddt.ddt
class DomainDeniedFlavorTest(test.TestCase):
    """Functional tests for project-scoped flavor permission rules with flavors
    already denied at domain-scope
    """

    def _rule_dict(self, effect, flavor_id=None, project_id=None):
        rule = {'flavor_permission_rule': {
            'domain_id': self.domain_id,
            'effect': effect,
        }}
        if flavor_id:
            rule['flavor_permission_rule']['flavor_id'] = flavor_id
        if project_id:
            rule['flavor_permission_rule']['project_id'] = project_id
        return rule

    def setUp(self):
        super().setUp()
        root = 'os_compute_api:os-flavor-permission-rules:'
        project_admin_rule = (
            'role:member and project_domain_id:%(project_domain_id)s and '
            'project_id:%(project_id)s')
        domain_admin_rule = (
            'role:other and project_domain_id:%(project_domain_id)s')
        self.policy.set_rules({
            root + 'index:project': 'role:member',
            root + 'create:project': project_admin_rule,
            root + 'show:project': project_admin_rule,
            root + 'update:project': project_admin_rule,
            root + 'delete:project': project_admin_rule,
            root + 'index:domain': 'role:other',
            root + 'create:domain': domain_admin_rule,
            root + 'show:domain': domain_admin_rule,
            root + 'update:domain': domain_admin_rule,
            root + 'delete:domain': domain_admin_rule,

        }, overwrite=False)
        api = self.useFixture(nova_fixtures.OSAPIFixture(api_version='v2.1'))
        self.apis = {
            PROJECT_ADMIN: api.api,
            DOMAIN_ADMIN: api.other_api,
            ADMIN: api.admin_api,
        }
        self.domain_id = 'fake-domain'
        self.project_id = api.project_id

        def domain_context(*args, **kwargs):
            ctx = create_context(*args, **kwargs)
            ctx.project_domain_id = self.domain_id
            return ctx

        # Simulate authentication with a fixed domain
        create_context = auth.NovaKeystoneContext._create_context
        self.stub_out('nova.api.auth.NovaKeystoneContext._create_context',
                      staticmethod(domain_context))

        self.flavor_id = '1'
        self.other_flavor_id = '2'
        self.domain_rule_dict = self._rule_dict(
            fields.FlavorPermissionRuleEffect.DENY, flavor_id=self.flavor_id)
        self.project_rule_dict = self._rule_dict(
            fields.FlavorPermissionRuleEffect.DENY, flavor_id=self.flavor_id,
            project_id=self.project_id)

    def _create_deny_rules(self):
        self.project_rule = (
            self.apis[PROJECT_ADMIN].post_flavor_permission_rule(
                self.project_rule_dict))
        self.domain_rule = (
            self.apis[DOMAIN_ADMIN].post_flavor_permission_rule(
                self.domain_rule_dict))

    def test_project_scope_create_rejects_domain_denied_flavor(self):
        """Must not allow the creation of project-scope rules for domain-denied
        flavors
        """
        self.apis[DOMAIN_ADMIN].post_flavor_permission_rule(
            self.domain_rule_dict)
        self.assertRaises(
            client.OpenStackApiNotFoundException,
            self.apis[PROJECT_ADMIN].post_flavor_permission_rule,
            self.project_rule_dict)

    def test_domain_scope_create_allows_project_denied_flavor(self):
        """Must allow the creation of domain-scope rules even for flavors
        already denied at project-scope
        """
        self._create_deny_rules()

    @ddt.named_data(
        ('admin_sees_all_rules', ADMIN, 4),
        ('domain_admin_sees_undenied_rules', DOMAIN_ADMIN, 3),
        ('project_admin_sees_undenied_project_rules', PROJECT_ADMIN, 2),
    )
    def test_list_domain_specific_denied_project_rules(self, api, expected):
        self._create_deny_rules()
        # Create undenied project-scope default rule
        self.apis[PROJECT_ADMIN].post_flavor_permission_rule(self._rule_dict(
            fields.FlavorPermissionRuleEffect.ALLOW,
            project_id=self.project_id))
        # Create project-scope rule undenied due to different flavor
        self.apis[PROJECT_ADMIN].post_flavor_permission_rule(self._rule_dict(
            fields.FlavorPermissionRuleEffect.ALLOW,
            flavor_id=self.other_flavor_id, project_id=self.project_id))
        rules = self.apis[api].get_flavor_permission_rules()
        self.assertEqual(expected, len(rules))

    @ddt.named_data(
        ('admin_sees_all_rules', ADMIN, 5),
        ('domain_admin_sees_undenied_rules', DOMAIN_ADMIN, 4),
        ('project_admin_sees_undenied_project_rules', PROJECT_ADMIN, 2),
    )
    def test_list_domain_default_denied_project_rules(self, api, expected):
        # Create project-scope rule denied by domain-scope default deny
        self.apis[PROJECT_ADMIN].post_flavor_permission_rule(self._rule_dict(
            fields.FlavorPermissionRuleEffect.ALLOW,
            flavor_id=self.other_flavor_id, project_id=self.project_id))
        # Create domain-scope default-deny rule
        self.apis[DOMAIN_ADMIN].post_flavor_permission_rule(
            self._rule_dict(fields.FlavorPermissionRuleEffect.DENY))
        # Create domain-scope specific-allow rule
        self.apis[DOMAIN_ADMIN].post_flavor_permission_rule(
            self._rule_dict(fields.FlavorPermissionRuleEffect.ALLOW,
                            flavor_id=self.flavor_id))
        # Create project-scope rule undenied due to domain-scope specific allow
        self.apis[PROJECT_ADMIN].post_flavor_permission_rule(
            self.project_rule_dict)
        # Create undenied project-scope default rule
        self.apis[PROJECT_ADMIN].post_flavor_permission_rule(self._rule_dict(
            fields.FlavorPermissionRuleEffect.ALLOW,
            project_id=self.project_id))
        rules = self.apis[api].get_flavor_permission_rules()
        self.assertEqual(expected, len(rules))

    def test_show_domain_denied_project_rule_admin(self):
        self._create_deny_rules()
        self.apis[ADMIN].get_flavor_permission_rule(self.project_rule['id'])

    @ddt.data(DOMAIN_ADMIN, PROJECT_ADMIN)
    def test_show_domain_denied_project_rule_non_admin_raises(self, api):
        self._create_deny_rules()
        self.assertRaises(
            client.OpenStackApiNotFoundException,
            self.apis[api].get_flavor_permission_rule, self.project_rule['id'])

    def test_update_domain_denied_project_rule_admin(self):
        self._create_deny_rules()
        self.apis[ADMIN].put_flavor_permission_rule(
            self.project_rule['id'], fields.FlavorPermissionRuleEffect.ALLOW)

    @ddt.data(DOMAIN_ADMIN, PROJECT_ADMIN)
    def test_update_domain_denied_project_rule_non_admin_raises(self, api):
        self._create_deny_rules()
        self.assertRaises(
            client.OpenStackApiNotFoundException,
            self.apis[api].put_flavor_permission_rule, self.project_rule['id'],
            fields.FlavorPermissionRuleEffect.ALLOW)

    def test_delete_domain_denied_project_rule_admin(self):
        self._create_deny_rules()
        self.apis[ADMIN].delete_flavor_permission_rule(self.project_rule['id'])

    @ddt.data(DOMAIN_ADMIN, PROJECT_ADMIN)
    def test_delete_domain_denied_project_rule_non_admin_raises(self, api):
        self._create_deny_rules()
        self.assertRaises(
            client.OpenStackApiNotFoundException,
            self.apis[api].delete_flavor_permission_rule,
            self.project_rule['id'])
