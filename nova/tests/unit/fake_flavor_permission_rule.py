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

from oslo_utils import uuidutils

from nova import objects
from nova.objects import fields
from nova.objects.flavor_permission_rule import _DB_NONE_SENTINELS


def fake_db_flavor_permission_rule(**updates):
    for field, sentinel in _DB_NONE_SENTINELS.items():
        if field in updates and updates[field] is None:
            updates[field] = sentinel
    db_rule = {
        'id': 1,
        'uuid': uuidutils.generate_uuid(),
        'domain_id': 'fake-domain',
        'project_id': 'fake-project',
        'flavor_id': 123,
        'effect': fields.FlavorPermissionRuleEffect.ALLOW,
    } | updates

    for name, field in objects.FlavorPermissionRule.fields.items():
        if name in db_rule:
            continue
        if field.nullable:
            db_rule[name] = None
        elif field.default != fields.UnspecifiedDefault:
            db_rule[name] = field.default
        else:
            raise Exception(
                f'fake_db_flavor_permission_rule needs help with {name}')

    return db_rule


def fake_flavor_permission_rule_obj(context, db_rule=None, **updates):
    if db_rule is None:
        db_rule = fake_db_flavor_permission_rule()
    return objects.FlavorPermissionRule._from_db_object(
        context, objects.FlavorPermissionRule(), db_rule | updates)
