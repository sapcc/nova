# Copyright (c) 2025 SAP SE
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
from unittest import mock

from nova import context
from nova.db.api import api as api_db_api
from nova.db.api import models as api_models
from nova import exception
from nova import objects
from nova.objects import fields
from nova.objects.flavor_permission_rule import _DB_NONE_SENTINELS
from nova import test
from nova.tests.unit import fake_flavor_permission_rule as fake_rule
from nova.tests.unit.objects import test_objects


class _TestFlavorPermissionRuleObject:
    """Mixin for FlavorPermissionRule object test cases."""

    _fake_db_rule = fake_rule.fake_db_flavor_permission_rule()

    def _get_fake_db_rule(self, **updates):
        return fake_rule.fake_db_flavor_permission_rule(**updates)

    def _get_fake_rule_obj(self, db_rule=None):
        if db_rule is None:
            db_rule = self._fake_db_rule
        return fake_rule.fake_flavor_permission_rule_obj(
            self.context, db_rule)

    def _compare_obj(self, rule_obj, db_rule, db_allow_missing=None,
                     db_allow_none=None):
        if db_allow_none is None:
            db_allow_none = []
        if db_allow_missing is None:
            db_allow_missing = []
        for field in rule_obj.fields:
            if field not in db_rule and field in db_allow_missing:
                self.assertTrue(rule_obj.obj_attr_is_set(field))
                continue

            db_val = db_rule[field]
            # Mirror the sentinel translation that _from_db_object performs
            if (field in _DB_NONE_SENTINELS
                    and db_val == _DB_NONE_SENTINELS[field]):
                db_val = None

            if db_val is None and field in db_allow_none:
                self.assertTrue(rule_obj.obj_attr_is_set(field))
                continue

            obj_val = getattr(rule_obj, field)
            if isinstance(obj_val, datetime):
                obj_val = obj_val.replace(tzinfo=None)
                # Indirection API loses microsecond precision
                if rule_obj.indirection_api:
                    db_val = db_val.replace(microsecond=0)

            self.assertEqual(db_val, obj_val, f'field: {field}')

    @staticmethod
    @api_db_api.context_manager.writer
    def _create_context_db_rule(context, fake_db_rule=None):
        if fake_db_rule is None:
            fake_db_rule = _TestFlavorPermissionRuleObject._fake_db_rule
        fake_db_rule = fake_db_rule.copy()
        fake_db_rule.pop('id')
        db_rule = api_models.FlavorPermissionRule()
        db_rule.update(fake_db_rule)
        db_rule.save(context.session)
        return db_rule

    def _create_db_rule(self, fake_db_rule=None):
        return self._create_context_db_rule(self.context, fake_db_rule)


class TestFlavorPermissionRuleObjectNoDB(test.NoDBTestCase,
                                         _TestFlavorPermissionRuleObject):

    def setUp(self):
        super().setUp()
        # Set up context like _BaseTestCase does
        self.user_id = 'fake-user'
        self.project_id = 'fake-project'
        self.context = context.RequestContext(self.user_id, self.project_id)

    @mock.patch('nova.objects.FlavorPermissionRule._get_by_id_from_db')
    def test_get_by_id(self, mock_get):
        mock_get.return_value = self._fake_db_rule
        rule = objects.FlavorPermissionRule.get_by_id(
            self.context, self._fake_db_rule['id'])
        self._compare_obj(rule, self._fake_db_rule)
        mock_get.assert_called_once_with(
            self.context, self._fake_db_rule['id'])

    @mock.patch('nova.objects.FlavorPermissionRule._get_by_uuid_from_db')
    def test_get_by_uuid(self, mock_get):
        mock_get.return_value = self._fake_db_rule
        rule = objects.FlavorPermissionRule.get_by_uuid(
            self.context, self._fake_db_rule['uuid'])
        self._compare_obj(rule, self._fake_db_rule)
        mock_get.assert_called_once_with(
            self.context, self._fake_db_rule['uuid'])

    @mock.patch('nova.objects.FlavorPermissionRule._create_in_db')
    def test_create(self, mock_create):
        mock_create.return_value = self._fake_db_rule
        fake_db_rule = self._fake_db_rule.copy()
        fake_db_rule.pop('id')
        # Create rule with tracked changes
        rule = objects.FlavorPermissionRule(context=self.context,
                                            **fake_db_rule)
        rule.create()
        mock_create.assert_called_once_with(self.context, fake_db_rule)
        self._compare_obj(rule, self._fake_db_rule)

    @mock.patch('nova.objects.FlavorPermissionRule._destroy_in_db')
    def test_destroy(self, mock_destroy):
        mock_destroy.return_value = self._fake_db_rule
        rule = self._get_fake_rule_obj()
        rule.destroy()
        mock_destroy.assert_called_once_with(self.context, 1)

    @mock.patch('nova.objects.FlavorPermissionRule._save')
    def test_save(self, mock_save):
        new_effect = fields.FlavorPermissionRuleEffect.DENY
        mock_save.return_value = self._fake_db_rule | {'effect': new_effect}
        rule = self._get_fake_rule_obj()
        rule.effect = new_effect
        rule.save()
        mock_save.assert_called_once_with(
            self.context, rule.id, {'effect': new_effect})

    @mock.patch('nova.objects.FlavorPermissionRule._save')
    def test_save_no_changes(self, mock_save):
        rule = self._get_fake_rule_obj()
        rule.obj_reset_changes()
        rule.save()
        mock_save.assert_not_called()


class _TestFlavorPermissionRuleObjectDB(_TestFlavorPermissionRuleObject):
    """Mixin for FlavorPermissionRule object test cases with DB."""

    def test_get_by_id(self):
        db_rule = self._create_db_rule()
        rule = objects.FlavorPermissionRule.get_by_id(self.context, db_rule.id)
        self._compare_obj(rule, db_rule)

    def test_get_by_uuid(self):
        db_rule = self._create_db_rule()
        rule = objects.FlavorPermissionRule.get_by_uuid(
            self.context, db_rule.uuid)
        self._compare_obj(rule, db_rule)

    def test_create(self):
        fake_db_rule = self._fake_db_rule.copy()
        fake_db_rule.pop('id')
        # Create rule with tracked changes
        rule = objects.FlavorPermissionRule(context=self.context,
                                            **fake_db_rule)
        rule.create()
        self.assertIsNotNone(rule.id)
        self.assertIsNotNone(rule.created_at)
        self._compare_obj(rule, fake_db_rule, db_allow_missing=['id'],
                          db_allow_none=['created_at'])
        updated_rule = objects.FlavorPermissionRule.get_by_id(
            self.context, rule.id)
        self._compare_obj(updated_rule, fake_db_rule, db_allow_missing=['id'],
                          db_allow_none=['created_at'])

    def test_destroy(self):
        db_rule = self._create_db_rule()
        rule = objects.FlavorPermissionRule.get_by_id(self.context, db_rule.id)
        rule.destroy()
        self.assertRaises(
            exception.FlavorPermissionRuleNotFound,
            objects.FlavorPermissionRule.get_by_id,
            self.context, db_rule.id)

    def test_save(self):
        db_rule = self._create_db_rule()
        rule = objects.FlavorPermissionRule.get_by_id(self.context, db_rule.id)
        new_effect = fields.FlavorPermissionRuleEffect.DENY
        rule.effect = new_effect
        rule.save()
        updated_rule = objects.FlavorPermissionRule.get_by_id(
            self.context, db_rule.id)
        self.assertEqual(new_effect, updated_rule.effect)


class TestFlavorPermissionRuleObject(
        test_objects._LocalTest, _TestFlavorPermissionRuleObjectDB):
    pass


class TestFlavorPermissionRuleObjectRemote(
        test_objects._RemoteTest, _TestFlavorPermissionRuleObjectDB):
    pass


@ddt.ddt
class TestFlavorPermissionRuleListObjectNoDB(test.NoDBTestCase,
                                         _TestFlavorPermissionRuleObject):

    def setUp(self):
        super().setUp()
        # Set up context like _BaseTestCase does
        self.user_id = 'fake-user'
        self.project_id = 'fake-project'
        self.context = context.RequestContext(
            self.user_id, self.project_id, project_domain_id='fake-domain')

    @ddt.data(
        ({}, True, False, True),
        ({'filter_domain_rules_by_context_domain': False},
         False, False, True),
        ({'filter_project_rules_by_context_project': False},
         True, False, False),
        ({'filter_project_rules_by_context_project': False,
          'filter_project_rules_by_context_domain': True},
         True, True, False),
    )
    @ddt.unpack
    @mock.patch('nova.objects.FlavorPermissionRuleList._get_from_db')
    def test_get_all_context_filters(
            self, call_kwargs, exp_filter_domain, exp_filter_proj_domain,
            exp_filter_proj_project, mock_get):
        mock_get.return_value = []
        objects.FlavorPermissionRuleList.get_all(
            self.context, **call_kwargs)
        mock_get.assert_called_once_with(
            self.context,
            filter_domain_rules_by_context_domain=exp_filter_domain,
            filter_project_rules_by_context_domain=exp_filter_proj_domain,
            filter_project_rules_by_context_project=exp_filter_proj_project,
            domain_ids=None, project_ids=None, scope=None, effect=None,
            flavor_ids=None, limit=None, marker=None)

    @ddt.data(
        ('scope', fields.FlavorPermissionRuleScope.PROJECT),
        ('effect', fields.FlavorPermissionRuleEffect.DENY),
        ('flavor_ids', {123}),
        ('domain_ids', {'dom-a', 'dom-b'}),
        ('project_ids', {'proj-a', 'proj-b'}),
    )
    @ddt.unpack
    @mock.patch('nova.objects.FlavorPermissionRuleList._get_from_db')
    def test_get_all_filter(self, arg_name, value, mock_get):
        mock_get.return_value = []
        objects.FlavorPermissionRuleList.get_all(
            self.context,
            filter_domain_rules_by_context_domain=False,
            filter_project_rules_by_context_project=False,
            **{arg_name: value})
        self.assertEqual(value, mock_get.call_args[1][arg_name])


class _TestFlavorPermissionRuleListObject(_TestFlavorPermissionRuleObject):

    def test_get_all(self):
        db_rule1 = self._create_db_rule()
        db_rule2 = self._create_db_rule(
            self._get_fake_db_rule(project_id='project-2'))
        rules = objects.FlavorPermissionRuleList.get_all(
            self.context,
            filter_domain_rules_by_context_domain=False,
            filter_project_rules_by_context_project=False)
        self.assertEqual(2, len(rules))
        rules = objects.FlavorPermissionRuleList.get_all(
            self.context,
            filter_domain_rules_by_context_domain=False,
            filter_project_rules_by_context_project=False,
            limit=1)
        self.assertEqual(1, len(rules))
        self.assertEqual(db_rule1.id, rules[0].id)
        rules = objects.FlavorPermissionRuleList.get_all(
            self.context,
            filter_domain_rules_by_context_domain=False,
            filter_project_rules_by_context_project=False,
            limit=1, marker=db_rule1.uuid)
        self.assertEqual(1, len(rules))
        self.assertEqual(db_rule2.id, rules[0].id)

    def test_get_all_domain_and_project_filters(self):
        ctx = context.RequestContext(
            'fake-user', 'fake-project',
            project_domain_id='fake-domain')
        self._create_db_rule()
        self._create_db_rule(
            self._get_fake_db_rule(project_id=None))
        self._create_db_rule(
            self._get_fake_db_rule(project_id='other-project'))
        self._create_db_rule(
            self._get_fake_db_rule(domain_id='other-domain',
                                   project_id='other-project'))
        self._create_db_rule(
            self._get_fake_db_rule(domain_id='other-domain',
                                   project_id=None))

        def _get_all(**kwargs):
            return {(r.domain_id, r.project_id)
                    for r in objects.FlavorPermissionRuleList.get_all(
                        ctx, **kwargs)}

        self.assertEqual(
            {('fake-domain', None), ('fake-domain', 'fake-project')},
            _get_all())
        self.assertEqual(
            {('fake-domain', 'fake-project')},
            _get_all(scope=fields.FlavorPermissionRuleScope.PROJECT))
        self.assertEqual(
            {('fake-domain', None)},
            _get_all(scope=fields.FlavorPermissionRuleScope.DOMAIN))
        self.assertEqual(
            {('fake-domain', None), ('fake-domain', 'fake-project'),
             ('fake-domain', 'other-project')},
            _get_all(filter_project_rules_by_context_domain=True,
                     filter_project_rules_by_context_project=False))
        self.assertEqual(
            {('fake-domain', None), ('fake-domain', 'fake-project'),
             ('fake-domain', 'other-project'),
             ('other-domain', 'other-project'), ('other-domain', None)},
            _get_all(filter_domain_rules_by_context_domain=False,
                     filter_project_rules_by_context_project=False))


class TestFlavorPermissionRuleListObject(
        test_objects._LocalTest, _TestFlavorPermissionRuleListObject):
    pass


class TestRemoteFlavorPermissionRuleListObject(
        test_objects._RemoteTest, _TestFlavorPermissionRuleListObject):
    pass
