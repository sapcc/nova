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

import typing as ty

import webob.exc

from oslo_utils import uuidutils

from nova.api.openstack import common
from nova.api.openstack.compute.schemas import flavor_permission_rules as schema
from nova.api.openstack.compute.views import (
    flavor_permission_rules as views_fpr)
from nova.api.openstack import wsgi
from nova.api import validation
from nova import exception
from nova import objects
from nova.objects import fields
from nova.policies import flavor_permission_rules as fpr_policies


class FlavorPermissionRulesController(wsgi.Controller):
    """Controller for flavor permission rules."""

    _view_builder_class = views_fpr.ViewBuilder

    @staticmethod
    def _policy_target(
        domain_id: str,
        project_id: str | None,
    ) -> dict[str, str]:
        """Build a policy target dict for a flavor permission rule.

        Allows to restrict rule access to either the context project or the
        context project domain, depending on the rule's scope.
        """
        target = {'project_domain_id': domain_id}
        if project_id is not None:
            target['project_id'] = project_id
        return target

    @wsgi.expected_errors((400, 403))
    @validation.query_schema(schema.index_query)
    def index(self, req: wsgi.Request) -> dict[str, ty.Any]:
        context = req.environ['nova.context']
        root = fpr_policies.POLICY_ROOT
        include_all_domains = context.can(
            root % 'index:domain_all', target={}, fatal=False)
        include_domain = context.can(
            root % 'index:domain', target={}, fatal=False)
        include_all_projects = context.can(
            root % 'index:project_all', target={}, fatal=False)
        include_domain_projects = context.can(
            root % 'index:project_domain', target={}, fatal=False)
        include_project = context.can(
            root % 'index:project', target={}, fatal=False)

        domain_access = any([include_all_domains, include_domain])
        project_access = any(
            [include_all_projects, include_domain_projects, include_project])
        # No access to any rules
        if not domain_access and not project_access:
            raise webob.exc.HTTPForbidden()

        project_id = req.GET.get('project_id')
        # Only allow filtering by project_id if the user has access to
        # project-scope rules
        if project_id and not project_access:
            raise webob.exc.HTTPForbidden()

        scope = req.GET.get('scope')
        # Only allow filtering by a scope that the user has access to
        if (scope == fields.FlavorPermissionRuleScope.PROJECT
                and not project_access):
            raise webob.exc.HTTPForbidden()
        if (scope == fields.FlavorPermissionRuleScope.DOMAIN
                and not domain_access):
            raise webob.exc.HTTPForbidden()

        # Filter domains based on policies and query parameters
        domain_id = req.GET.get('domain_id')
        domain_ids = {domain_id} if domain_id else None
        filter_domain_rules_by_context_domain = True
        if include_all_domains:
            filter_domain_rules_by_context_domain = False
        elif not include_domain:
            scope = fields.FlavorPermissionRuleScope.PROJECT

        # Filter projects based on policies and query parameters
        project_ids = {project_id} if project_id else None
        filter_project_rules_by_context_domain = False
        filter_project_rules_by_context_project = True
        if include_all_projects:
            filter_project_rules_by_context_project = False
        elif include_domain_projects:
            filter_project_rules_by_context_project = False
            filter_project_rules_by_context_domain = True
        elif not include_project:
            scope = fields.FlavorPermissionRuleScope.DOMAIN

        limit, marker = common.get_limit_and_marker(req)
        flavor_ids = None
        flavor_ref = req.GET.get('flavor_id')
        if flavor_ref is not None:
            flavor = common.get_flavor(context, flavor_ref)
            flavor_ids = {flavor.id}

        try:
            rules = objects.FlavorPermissionRuleList.get_all(
                context,
                filter_domain_rules_by_context_domain=(
                    filter_domain_rules_by_context_domain),
                filter_project_rules_by_context_domain=(
                    filter_project_rules_by_context_domain),
                filter_project_rules_by_context_project=(
                    filter_project_rules_by_context_project),
                domain_ids=domain_ids, project_ids=project_ids, scope=scope,
                effect=req.GET.get('effect'), flavor_ids=flavor_ids,
                limit=limit, marker=marker)
        except exception.MarkerNotFound as e:
            raise webob.exc.HTTPBadRequest(explanation=e.format_message())

        return self._view_builder.index(req, rules)

    @wsgi.expected_errors((403, 404))
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
            raise webob.exc.HTTPNotFound()

        return self._view_builder.show(req, rule)

    @wsgi.response(201)
    @wsgi.expected_errors((400, 403, 404, 409))
    @validation.schema(schema.create)
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
        if 'flavor_id' in data and data['flavor_id'] is not None:
            flavor = common.get_flavor(context, str(data['flavor_id']))
            flavor_id = flavor.id

        rule = objects.FlavorPermissionRule(
            context=context, uuid=uuidutils.generate_uuid(),
            domain_id=domain_id, project_id=project_id, flavor_id=flavor_id,
            effect=data['effect'])
        try:
            rule.create()
        except exception.FlavorPermissionRuleExists as e:
            raise webob.exc.HTTPConflict(explanation=e.format_message())

        return self._view_builder.show(req, rule)

    @wsgi.response(204)
    @wsgi.expected_errors((403, 404))
    def delete(self, req: wsgi.Request, id: str) -> None:
        context = req.environ['nova.context']
        try:
            rule = objects.FlavorPermissionRule.get_by_uuid(context, id)
        except exception.FlavorPermissionRuleNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())

        context.can(
            fpr_policies.POLICY_ROOT % ('delete:%s' % rule.scope),
            target=self._policy_target(rule.domain_id, rule.project_id))

        rule.destroy()

    @wsgi.expected_errors((400, 403, 404))
    @validation.schema(schema.update)
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

        context.can(
            fpr_policies.POLICY_ROOT % ('update:%s' % rule.scope),
            target=self._policy_target(rule.domain_id, rule.project_id))

        rule.effect = body['flavor_permission_rule']['effect']
        rule.save()
        return self._view_builder.show(req, rule)
