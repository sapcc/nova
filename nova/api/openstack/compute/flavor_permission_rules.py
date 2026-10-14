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

import webob.exc

from oslo_utils import strutils
from oslo_utils import uuidutils

from nova.api.openstack import common
from nova.api.openstack.compute.schemas import (
    flavor_permission_rules as schema)
from nova.api.openstack.compute.views import (
    flavor_permission_rules as views_fpr)
from nova.api.openstack import wsgi
from nova.api import validation
from nova import exception
from nova import objects
from nova.objects import fields
from nova.policies import flavor_permission_rules as fpr_policies

if ty.TYPE_CHECKING:
    from nova import context as nova_context
    from nova.objects.flavor import Flavor
    from nova.objects.flavor_permission_rule import FlavorPermissionRule


class FlavorPermissionRulesController(wsgi.Controller):
    """Controller for flavor permission rules."""

    _view_builder_class = views_fpr.ViewBuilder

    @staticmethod
    def _policy_target(
        domain_id: str, project_id: str | None,
    ) -> dict[str, str]:
        """Build a policy target dict for a flavor permission rule.

        Allows to restrict rule access to either the context project or the
        context project domain, depending on the rule's scope.
        """
        target = {'project_domain_id': domain_id}
        if project_id is not None:
            target['project_id'] = project_id
        return target

    @staticmethod
    def _resolve_flavor(
        context: nova_context.RequestContext, flavor_ref: str,
    ) -> Flavor:
        """Resolve a flavor reference ignoring permission rules."""
        return common.get_flavor(
            context, flavor_ref,
            domain_permission=None,
            project_permission=None)

    @staticmethod
    def _flavor_refs(
        context: nova_context.RequestContext,
        rules: ty.Iterable[FlavorPermissionRule],
    ) -> dict[int, str]:
        """Map internal flavor ids referenced by rules to public flavorids."""
        flavor_ids = {r.flavor_id for r in rules if r.flavor_id is not None}
        if not flavor_ids:
            return {}
        return objects.FlavorList.get_flavor_ids_by_ids(
            context, flavor_ids,
            domain_permission=None,
            project_permission=None)

    @wsgi.Controller.api_version('2.100')
    @wsgi.expected_errors((400, 403, 404))
    @validation.query_schema(schema.index_query)
    @validation.response_body_schema(schema.index_response)
    def index(self, req: wsgi.Request) -> dict[str, ty.Any]:
        context = req.environ['nova.context']
        root = fpr_policies.POLICY_ROOT
        access_all = context.can(
            root % 'index:all', target={}, fatal=False)
        access_domain = access_all or context.can(
            root % 'index:domain', target={}, fatal=False)
        # Verify access to flavor permission rules
        access_domain or context.can(root % 'index:project', target={})

        project_id = req.GET.get('project_id')
        if not access_domain:
            if not context.project_id:
                raise webob.exc.HTTPForbidden(
                    explanation="Project context required")
            if project_id and project_id != context.project_id:
                raise webob.exc.HTTPForbidden(
                    explanation="Policy doesn't allow filtering by another "
                                "project's id")
            project_id = context.project_id

        scope = req.GET.get('scope')
        if not access_domain:
            if scope == fields.FlavorPermissionRuleScope.DOMAIN:
                raise webob.exc.HTTPForbidden(
                    explanation="Policy doesn't allow filtering by domain "
                                "scope")
            scope = fields.FlavorPermissionRuleScope.PROJECT

        domain_id = req.GET.get('domain_id')
        if not access_all:
            if not context.project_domain_id:
                raise webob.exc.HTTPForbidden(
                    explanation="Domain context required")
            if domain_id and domain_id != context.project_domain_id:
                raise webob.exc.HTTPForbidden(
                    explanation="Policy doesn't allow filtering by another "
                                "domain's id")
            domain_id = context.project_domain_id

        limit, marker = common.get_limit_and_marker(req)
        flavor_id = None
        flavor_ref = req.GET.get('flavor_id')
        if flavor_ref is not None:
            flavor = self._resolve_flavor(context, flavor_ref)
            flavor_id = flavor.id

        has_flavor_str = req.GET.get('has_flavor')
        has_flavor = (strutils.bool_from_string(has_flavor_str, strict=True)
                      if has_flavor_str is not None else None)

        if flavor_id is not None and has_flavor is not None:
            raise webob.exc.HTTPBadRequest(
                explanation="'flavor_id' and 'has_flavor' are mutually "
                            "exclusive")

        try:
            rules = objects.FlavorPermissionRuleList.get_all(
                context,
                domain_id=domain_id, project_id=project_id, scope=scope,
                effect=req.GET.get('effect'), flavor_id=flavor_id,
                has_flavor=has_flavor, limit=limit, marker=marker)
        except exception.MarkerNotFound as e:
            raise webob.exc.HTTPBadRequest(explanation=e.format_message())

        flavor_refs = self._flavor_refs(context, rules)
        return self._view_builder.index(req, rules, flavor_refs)

    @wsgi.Controller.api_version('2.100')
    @wsgi.expected_errors((404))
    @validation.query_schema(schema.show_query)
    @validation.response_body_schema(schema.create_show_update_response)
    def show(self, req: wsgi.Request, id: str) -> dict[str, ty.Any]:
        context = req.environ['nova.context']
        try:
            rule = objects.FlavorPermissionRule.get_by_uuid(context, id)
        except exception.FlavorPermissionRuleNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())

        if not context.can(
                fpr_policies.POLICY_ROOT % ('show:%s' % rule.scope),
                target=self._policy_target(rule.domain_id, rule.project_id),
                fatal=False):
            # Return Not Found rather than Forbidden to avoid leaking the
            # rule's existence to callers who don't have access to it.
            raise webob.exc.HTTPNotFound()

        flavor_ref = self._flavor_refs(context, [rule]).get(rule.flavor_id)
        return self._view_builder.show(req, rule, flavor_ref)

    @wsgi.Controller.api_version('2.100')
    @wsgi.response(201)
    @wsgi.expected_errors((400, 403, 404, 409))
    @validation.schema(schema.create)
    @validation.response_body_schema(schema.create_show_update_response)
    def create(
        self,
        req: wsgi.Request,
        body: dict[str, ty.Any],
    ) -> dict[str, ty.Any]:
        context = req.environ['nova.context']
        data = body['flavor_permission_rule']
        domain_id = data['domain_id']
        project_id = data.get('project_id')
        scope = (fields.FlavorPermissionRuleScope.PROJECT
                 if project_id else fields.FlavorPermissionRuleScope.DOMAIN)

        context.can(
            fpr_policies.POLICY_ROOT % ('create:%s' % scope),
            target=self._policy_target(domain_id, project_id))

        flavor_id = None
        flavor_ref = None
        if 'flavor_id' in data and data['flavor_id'] is not None:
            flavor = self._resolve_flavor(context, str(data['flavor_id']))
            if not flavor.is_public:
                raise webob.exc.HTTPBadRequest(
                    explanation="Flavor permission rules only apply to public "
                                "flavors.")
            flavor_id = flavor.id
            flavor_ref = flavor.flavorid

        rule = objects.FlavorPermissionRule(
            context=context, uuid=uuidutils.generate_uuid(),
            domain_id=domain_id, project_id=project_id, flavor_id=flavor_id,
            effect=data['effect'])
        try:
            rule.create()
        except exception.FlavorPermissionRuleExists as e:
            raise webob.exc.HTTPConflict(explanation=e.format_message())

        return self._view_builder.show(req, rule, flavor_ref)

    @wsgi.Controller.api_version('2.100')
    @wsgi.response(204)
    @wsgi.expected_errors(404)
    @validation.response_body_schema(schema.delete_response)
    def delete(self, req: wsgi.Request, id: str) -> None:
        context = req.environ['nova.context']
        try:
            rule = objects.FlavorPermissionRule.get_by_uuid(context, id)
        except exception.FlavorPermissionRuleNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())

        if not context.can(
                fpr_policies.POLICY_ROOT % ('delete:%s' % rule.scope),
                target=self._policy_target(rule.domain_id, rule.project_id),
                fatal=False):
            # Return Not Found rather than Forbidden to avoid leaking the
            # rule's existence to callers who don't have access to it.
            raise webob.exc.HTTPNotFound()

        rule.destroy()

    @wsgi.Controller.api_version('2.100')
    @wsgi.expected_errors((400, 404))
    @validation.schema(schema.update)
    @validation.response_body_schema(schema.create_show_update_response)
    def update(
        self,
        req: wsgi.Request,
        id: str,
        body: dict[str, ty.Any],
    ) -> dict[str, ty.Any]:
        context = req.environ['nova.context']
        try:
            rule = objects.FlavorPermissionRule.get_by_uuid(context, id)
        except exception.FlavorPermissionRuleNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())

        if not context.can(
                fpr_policies.POLICY_ROOT % ('update:%s' % rule.scope),
                target=self._policy_target(rule.domain_id, rule.project_id),
                fatal=False):
            # Return Not Found rather than Forbidden to avoid leaking the
            # rule's existence to callers who don't have access to it.
            raise webob.exc.HTTPNotFound()

        rule.effect = body['flavor_permission_rule']['effect']
        rule.save()
        flavor_ref = self._flavor_refs(context, [rule]).get(rule.flavor_id)
        return self._view_builder.show(req, rule, flavor_ref)
