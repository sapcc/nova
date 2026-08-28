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

create = {
    'type': 'object',
    'properties': {
        'flavor_permission_rule': {
            'type': 'object',
            'properties': {
                'domain_id': parameter_types.project_id,
                'project_id': parameter_types.project_id,
                'flavor_id': parameter_types.flavor_ref,
                'effect': {'type': 'string', 'enum': _RULE_EFFECTS},
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
        'limit': parameter_types.multi_params(
            parameter_types.non_negative_integer),
        'marker': parameter_types.multi_params({'type': 'string'}),
        'scope': parameter_types.multi_params(
            {'type': 'string', 'enum': _RULE_SCOPES}),
        'domain_id': parameter_types.multi_params(
            parameter_types.project_id),
        'project_id': parameter_types.multi_params(
            parameter_types.project_id),
        'flavor_id': parameter_types.multi_params(
            parameter_types.flavor_ref),
        'effect': parameter_types.multi_params(
            {'type': 'string', 'enum': _RULE_EFFECTS}),
    },
    'additionalProperties': False,
}
