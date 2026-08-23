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

from __future__ import annotations

import typing as ty

from nova.api.openstack import common

if ty.TYPE_CHECKING:
    from nova.api.openstack import wsgi
    from nova.objects.flavor_permission_rule import (
        FlavorPermissionRule, FlavorPermissionRuleList)


class ViewBuilder(common.ViewBuilder):

    _collection_name = 'flavor_permission_rules'

    def _rule_dict(
        self,
        request: wsgi.Request,
        rule: FlavorPermissionRule,
    ) -> dict[str, ty.Any]:
        return {
            'uuid': rule.uuid,
            'domain_id': rule.domain_id,
            'project_id': rule.project_id,
            'flavor_id': rule.flavor_id,
            'effect': rule.effect,
            'scope': rule.scope,
            'links': self._get_links(
                request, rule.uuid, self._collection_name),
        }

    def show(
        self,
        request: wsgi.Request,
        rule: FlavorPermissionRule,
    ) -> dict[str, ty.Any]:
        return {
            'flavor_permission_rule': self._rule_dict(request, rule),
        }

    def index(
        self,
        request: wsgi.Request,
        rules: FlavorPermissionRuleList,
    ) -> dict[str, ty.Any]:
        rules_list = [self._rule_dict(request, r) for r in rules]
        response = {'flavor_permission_rules': rules_list}
        links = self._get_collection_links(
            request, rules_list, self._collection_name)
        if links:
            response['flavor_permission_rules_links'] = links
        return response
