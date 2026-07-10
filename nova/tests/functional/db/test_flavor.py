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

from nova import context
from nova.db.api import api as api_db_api
from nova.db.api import models as api_models
from nova import exception
from nova import objects
from nova.objects import fields
from nova import test
from nova.tests import fixtures

fake_api_flavor = {
    'created_at': None,
    'updated_at': None,
    'name': 'm1.foo',
    'memory_mb': 1024,
    'vcpus': 4,
    'root_gb': 20,
    'ephemeral_gb': 0,
    'flavorid': 'm1.foo',
    'swap': 0,
    'rxtx_factor': 1.0,
    'vcpu_weight': 1,
    'disabled': False,
    'is_public': True,
    'extra_specs': {'foo': 'bar'},
    'projects': ['project1', 'project2'],
    'description': None
    }


class FlavorObjectTestCase(test.NoDBTestCase):
    USES_DB_SELF = True

    def setUp(self):
        super(FlavorObjectTestCase, self).setUp()
        self.useFixture(fixtures.Database())
        self.useFixture(fixtures.Database(database='api'))
        self.context = context.RequestContext('fake-user', 'fake-project')

    def test_create(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        self.assertIn('id', flavor)

        # Make sure we find this in the API database
        flavor2 = objects.Flavor._flavor_get_from_db(self.context, flavor.id,
                                                     False)
        self.assertEqual(flavor.id, flavor2['id'])

    def test_get_with_no_projects(self):
        fields = dict(fake_api_flavor, projects=[])
        flavor = objects.Flavor(context=self.context, **fields)
        flavor.create()
        flavor = objects.Flavor.get_by_flavor_id(self.context, flavor.flavorid)
        self.assertEqual([], flavor.projects)

    def test_get_with_projects_and_specs(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        flavor = objects.Flavor.get_by_id(self.context, flavor.id)
        self.assertEqual(fake_api_flavor['projects'], flavor.projects)
        self.assertEqual(fake_api_flavor['extra_specs'], flavor.extra_specs)

    def _test_query(self, flavor):
        flavor2 = objects.Flavor.get_by_id(self.context, flavor.id)
        self.assertEqual(flavor.id, flavor2.id)

        flavor2 = objects.Flavor.get_by_flavor_id(self.context,
                                                  flavor.flavorid)
        self.assertEqual(flavor.id, flavor2.id)

        flavor2 = objects.Flavor.get_by_name(self.context, flavor.name)
        self.assertEqual(flavor.id, flavor2.id)

    def test_query_api(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        self._test_query(flavor)

    def test_save(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        flavor.extra_specs['marty'] = 'mcfly'
        flavor.extra_specs['foo'] = 'bart'
        projects = list(flavor.projects)
        flavor.projects.append('project3')
        flavor.save()

        flavor2 = objects.Flavor.get_by_flavor_id(self.context,
                                                  flavor.flavorid)
        self.assertEqual({'marty': 'mcfly', 'foo': 'bart'},
                         flavor2.extra_specs)
        self.assertEqual(set(projects + ['project3']), set(flavor.projects))

        del flavor.extra_specs['foo']
        del flavor.projects[-1]
        flavor.save()

        flavor2 = objects.Flavor.get_by_flavor_id(self.context,
                                                  flavor.flavorid)
        self.assertEqual({'marty': 'mcfly'}, flavor2.extra_specs)
        self.assertEqual(set(projects), set(flavor2.projects))

    @staticmethod
    @api_db_api.context_manager.reader
    def _collect_flavor_residue_api(context, flavor):
        flavors = context.session.query(api_models.Flavors).\
                  filter_by(id=flavor.id).all()
        specs = context.session.query(api_models.FlavorExtraSpecs).\
                filter_by(flavor_id=flavor.id).all()
        projects = context.session.query(api_models.FlavorProjects).\
                   filter_by(flavor_id=flavor.id).all()

        return len(flavors) + len(specs) + len(projects)

    def _test_destroy(self, flavor):
        flavor.destroy()

        self.assertRaises(exception.FlavorNotFound,
                          objects.Flavor.get_by_name, self.context,
                          flavor.name)

    def test_destroy_api(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        self._test_destroy(flavor)
        self.assertEqual(
            0, self._collect_flavor_residue_api(self.context, flavor))

    def test_destroy_missing_flavor_by_flavorid(self):
        flavor = objects.Flavor(context=self.context, flavorid='foo')
        self.assertRaises(exception.FlavorNotFound,
                          flavor.destroy)

    def test_destroy_missing_flavor_by_id(self):
        flavor = objects.Flavor(context=self.context, flavorid='foo', id=1234)
        self.assertRaises(exception.FlavorNotFound,
                          flavor.destroy)

    def _test_get_all(self, expect_len, marker=None, limit=None):
        flavors = objects.FlavorList.get_all(self.context, marker=marker,
                                             limit=limit)
        self.assertEqual(expect_len, len(flavors))
        return flavors

    def test_get_all_with_all_api_flavors(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        self._test_get_all(1)

    def test_get_all_with_marker_in_api(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        fake_flavor2 = dict(fake_api_flavor, name='m1.zoo', flavorid='m1.zoo')
        flavor = objects.Flavor(context=self.context, **fake_flavor2)
        flavor.create()
        result = self._test_get_all(1, marker='m1.foo', limit=1)
        result_flavorids = [x.flavorid for x in result]
        self.assertEqual(['m1.zoo'], result_flavorids)

    def test_get_all_with_marker_not_found(self):
        flavor = objects.Flavor(context=self.context, **fake_api_flavor)
        flavor.create()
        fake_flavor2 = dict(fake_api_flavor, name='m1.zoo', flavorid='m1.zoo')
        flavor = objects.Flavor(context=self.context, **fake_flavor2)
        flavor.create()
        self.assertRaises(exception.MarkerNotFound,
                          self._test_get_all, 2, marker='noflavoratall')


class FlavorPermissionRuleFilterTestCase(test.NoDBTestCase):
    """Tests for the flavor permission rule filtering in
    _flavor_get_query_from_db.

    Uses a non-admin context with both project_id and project_domain_id set.
    All flavors are public so the is_public filter never blocks visibility --
    only permission rules are tested here.
    """
    USES_DB_SELF = True

    PROJECT_ID = 'fake-project'
    DOMAIN_ID = 'fake-domain'
    ALLOW = fields.FlavorPermissionRuleEffect.ALLOW
    DENY = fields.FlavorPermissionRuleEffect.DENY

    def _get_context(self, include_domain=True):
        return context.RequestContext(
            'fake-user', self.PROJECT_ID,
            project_domain_id=self.DOMAIN_ID if include_domain else None)

    def setUp(self):
        super().setUp()
        self.useFixture(fixtures.Database())
        self.useFixture(fixtures.Database(database='api'))
        self.context = self._get_context()
        flavor = objects.Flavor(context=self.context,
                                **dict(fake_api_flavor, is_public=True))
        flavor.create()
        self.flavor = flavor

    def _create_rule(self, domain_id, effect, project_id=None,
                     flavor_id=None):
        rule = objects.FlavorPermissionRule(
            context=self.context,
            domain_id=domain_id,
            project_id=project_id,
            effect=effect,
            flavor_id=flavor_id,
        )
        rule.create()
        return rule

    def _flavor_visible(self, skip_project_fprs=False, context=None):
        flavors = objects.FlavorList.get_all(
            context or self.context,
            skip_project_fprs=skip_project_fprs)
        return any(f.id == self.flavor.id for f in flavors)

    def test_no_rules_flavor_accessible(self):
        self.assertTrue(self._flavor_visible())

    def test_domain_specific_deny_hides_flavor(self):
        self._create_rule(self.DOMAIN_ID, self.DENY, flavor_id=self.flavor.id)
        self.assertFalse(self._flavor_visible())
        # skip_project_fprs only bypasses project-level denial
        self.assertFalse(self._flavor_visible(skip_project_fprs=True))

    def test_domain_default_deny_hides_flavor(self):
        self._create_rule(self.DOMAIN_ID, self.DENY)
        self.assertFalse(self._flavor_visible())
        # skip_project_fprs only bypasses project-level denial
        self.assertFalse(self._flavor_visible(skip_project_fprs=True))

    def test_domain_default_deny_overridden_by_specific_allow(self):
        self._create_rule(self.DOMAIN_ID, self.DENY)
        self._create_rule(self.DOMAIN_ID, self.ALLOW, flavor_id=self.flavor.id)
        self.assertTrue(self._flavor_visible())

    def test_project_specific_deny_hides_flavor(self):
        self._create_rule(self.DOMAIN_ID, self.DENY, project_id=self.PROJECT_ID,
                          flavor_id=self.flavor.id)
        self.assertFalse(self._flavor_visible())
        self.assertTrue(self._flavor_visible(skip_project_fprs=True))

    def test_project_default_deny_hides_flavor(self):
        self._create_rule(self.DOMAIN_ID, self.DENY, project_id=self.PROJECT_ID)
        self.assertFalse(self._flavor_visible())
        self.assertTrue(self._flavor_visible(skip_project_fprs=True))

    def test_project_default_deny_overridden_by_specific_allow(self):
        self._create_rule(self.DOMAIN_ID, self.DENY, project_id=self.PROJECT_ID)
        self._create_rule(self.DOMAIN_ID, self.ALLOW, project_id=self.PROJECT_ID,
                          flavor_id=self.flavor.id)
        self.assertTrue(self._flavor_visible())

    def test_no_domain_id_in_context_skips_domain_filter(self):
        self._create_rule(self.DOMAIN_ID, self.DENY, flavor_id=self.flavor.id)
        self.assertTrue(self._flavor_visible(
            context=self._get_context(include_domain=False)))

    def test_deny_for_other_project_does_not_affect_visibility(self):
        self._create_rule(self.DOMAIN_ID, self.DENY, project_id='other-project',
                          flavor_id=self.flavor.id)
        self._create_rule(self.DOMAIN_ID, self.DENY, project_id='other-project')
        self.assertTrue(self._flavor_visible())

    def test_deny_for_other_domain_does_not_affect_visibility(self):
        self._create_rule('other-domain', self.DENY, flavor_id=self.flavor.id)
        self._create_rule('other-domain', self.DENY)
        self.assertTrue(self._flavor_visible())
