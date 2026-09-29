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

from nova.api.validation import parameter_types
from nova.objects import fields


_RULE_EFFECTS = list(fields.FlavorPermissionRuleEffect.ALL)
_RULE_SCOPES = list(fields.FlavorPermissionRuleScope.ALL)

_links = {
    'type': 'array',
    'items': {
        'type': 'object',
        'properties': {
            'href': {'type': 'string', 'format': 'uri'},
            'rel': {'type': 'string'},
        },
        'required': ['href', 'rel'],
        'additionalProperties': False,
    },
}

_rule = {
    'type': 'object',
    'properties': {
        'created_at': {'type': 'string', 'format': 'date-time'},
        'domain_id': {'type': 'string'},
        'effect': {'type': 'string', 'enum': _RULE_EFFECTS},
        'flavor_id': {'type': ['string', 'null']},
        'id': {'type': 'string'},
        'links': _links,
        'project_id': {'type': ['string', 'null']},
        'scope': {'type': 'string', 'enum': _RULE_SCOPES},
        'updated_at': {
            'anyOf': [
                {'type': 'string', 'format': 'date-time'},
                {'type': 'null'},
            ]
        },
    },
    'required': ['id', 'domain_id', 'project_id', 'flavor_id',
                 'effect', 'scope', 'links'],
    'additionalProperties': False,
}

create_show_update_response = {
    'type': 'object',
    'properties': {
        'flavor_permission_rule': _rule,
    },
    'required': ['flavor_permission_rule'],
    'additionalProperties': False,
}

index_response = {
    'type': 'object',
    'properties': {
        'flavor_permission_rules': {
            'type': 'array',
            'items': _rule,
        },
        'flavor_permission_rules_links': _links,
    },
    'required': ['flavor_permission_rules'],
    'additionalProperties': False,
}

delete_response = {
    'type': 'null',
}

create = {
    'type': 'object',
    'properties': {
        'flavor_permission_rule': {
            'type': 'object',
            'properties': {
                'domain_id': parameter_types.project_id,
                'effect': {'type': 'string', 'enum': _RULE_EFFECTS},
                'flavor_id': parameter_types.flavor_ref,
                'project_id': parameter_types.project_id,
            },
            'required': ['domain_id', 'effect'],
            'additionalProperties': False,
        },
    },
    'required': ['flavor_permission_rule'],
    'additionalProperties': False,
}

update = {
    'type': 'object',
    'properties': {
        'flavor_permission_rule': {
            'type': 'object',
            'properties': {
                'effect': {'type': 'string', 'enum': _RULE_EFFECTS},
            },
            'required': ['effect'],
            'additionalProperties': False,
        },
    },
    'required': ['flavor_permission_rule'],
    'additionalProperties': False,
}

index_query = {
    'type': 'object',
    'properties': {
        'domain_id': parameter_types.multi_params(
            parameter_types.project_id),
        'effect': parameter_types.multi_params(
            {'type': 'string', 'enum': _RULE_EFFECTS}),
        'flavor_id': parameter_types.multi_params(
            parameter_types.flavor_ref),
        'has_flavor': parameter_types.multi_params(
            parameter_types.boolean),
        'limit': parameter_types.multi_params(
            parameter_types.non_negative_integer),
        'marker': parameter_types.multi_params({'type': 'string'}),
        'project_id': parameter_types.multi_params(
            parameter_types.project_id),
        'scope': parameter_types.multi_params(
            {'type': 'string', 'enum': _RULE_SCOPES}),
    },
    'additionalProperties': False,
}

show_query = {
    'type': 'object',
    'properties': {},
    'additionalProperties': False,
}
