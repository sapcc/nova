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

from oslo_policy import policy

from nova.policies import base

POLICY_ROOT = 'os_compute_api:os-flavor-permission-rules:%s'

flavor_permission_rules_policies = [
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'index:domain',
        check_str=base.ADMIN,
        description='List domain-scope flavor permission rules for the '
                    'context domain.',
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'index:domain_all',
        check_str=base.ADMIN,
        description='List domain-scope flavor permission rules for all '
                    'domains.',
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'index:project',
        check_str=base.ADMIN,
        description='List project-scope flavor permission rules for the '
                    'context project.',
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'index:project_domain',
        check_str=base.ADMIN,
        description='List project-scope flavor permission rules for all '
                    'projects in the context domain.',
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'index:project_all',
        check_str=base.ADMIN,
        description='List project-scope flavor permission rules for all '
                    'projects.',
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'show:domain',
        check_str=base.ADMIN,
        description=(
            'Show a domain-scoped flavor permission rule. '
            'Supports %(project_domain_id)s in check_str to restrict access '
            'to users whose context matches the rule domain.'),
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules/{id}'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'show:project',
        check_str=base.ADMIN,
        description=(
            'Show a project-scoped flavor permission rule. '
            'Supports %(project_domain_id)s and %(project_id)s in check_str '
            'to restrict access to users whose context matches the rule '
            'domain or project.'),
        operations=[{'method': 'GET',
                     'path': '/flavor-permission-rules/{id}'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'create:domain',
        check_str=base.ADMIN,
        description=(
            'Create a domain-scoped flavor permission rule. '
            'Supports %(project_domain_id)s in check_str to restrict access '
            'to users whose context matches the rule domain.'),
        operations=[{'method': 'POST',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'create:project',
        check_str=base.ADMIN,
        description=(
            'Create a project-scoped flavor permission rule. '
            'Supports %(project_domain_id)s and %(project_id)s in check_str '
            'to restrict access to users whose context matches the rule '
            'domain or project.'),
        operations=[{'method': 'POST',
                     'path': '/flavor-permission-rules'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'delete:domain',
        check_str=base.ADMIN,
        description=(
            'Delete a domain-scoped flavor permission rule. '
            'Supports %(project_domain_id)s in check_str to restrict access '
            'to users whose context matches the rule domain.'),
        operations=[{'method': 'DELETE',
                     'path': '/flavor-permission-rules/{id}'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'delete:project',
        check_str=base.ADMIN,
        description=(
            'Delete a project-scoped flavor permission rule. '
            'Supports %(project_domain_id)s and %(project_id)s in check_str '
            'to restrict access to users whose context matches the rule '
            'domain or project.'),
        operations=[{'method': 'DELETE',
                     'path': '/flavor-permission-rules/{id}'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'update:domain',
        check_str=base.ADMIN,
        description=(
            'Update a domain-scoped flavor permission rule. '
            'Supports %(project_domain_id)s in check_str to restrict access '
            'to users whose context matches the rule domain.'),
        operations=[{'method': 'PUT',
                     'path': '/flavor-permission-rules/{id}'}],
        scope_types=['project']),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'update:project',
        check_str=base.ADMIN,
        description=(
            'Update a project-scoped flavor permission rule. '
            'Supports %(project_domain_id)s and %(project_id)s in check_str '
            'to restrict access to users whose context matches the rule '
            'domain or project.'),
        operations=[{'method': 'PUT',
                     'path': '/flavor-permission-rules/{id}'}],
        scope_types=['project']),
]


def list_rules() -> list[policy.DocumentedRuleDefault]:
    return flavor_permission_rules_policies
