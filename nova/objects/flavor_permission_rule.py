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

from __future__ import annotations

import typing as ty

from oslo_db import exception as db_exc
from oslo_db.sqlalchemy.utils import paginate_query
from oslo_utils import uuidutils
import sqlalchemy as sa

from nova.db.api import api as api_db_api
from nova.db.api import models as api_models
from nova.db import utils as db_utils
from nova import exception
from nova.objects import base
from nova.objects import fields

if ty.TYPE_CHECKING:
    from nova import context as nova_context


# Maps each DB field that uses a sentinel value to that sentinel. The sentinel
# stands in for None in the object layer: '' means domain-scope (no project),
# -1 means default rule (no specific flavor).
_DB_NONE_SENTINELS = {
    'project_id': '',
    'flavor_id': -1,
}


@base.NovaObjectRegistry.register
class FlavorPermissionRule(base.NovaPersistentObject, base.NovaObject):
    """Restricts which flavors are permitted for which domains and projects.

    A flavor permission rule has a 'domain_id', an optional 'project_id', an
    optional 'flavor_id' and an 'effect' ('allow' or 'deny').

    The scope of a flavor permission rule is derived from 'project_id':
    - 'domain' scope rules have no 'project_id'. They apply to all projects
      within domain 'domain_id'.
    - 'project' scope rules have a 'project_id' and apply only to that project.
      They are not inherited by sub-projects. 'domain_id' is the domain of the
      project.

    A flavor is permitted for a project if it is permitted at both the domain
    scope and the project scope. Rules without a 'flavor_id' define the
    domain's or project's default behavior for flavors without a
    flavor-specific rule. If a domain or project does not have a default
    behavior rule, then all flavors are permitted at that scope by default.

    There is at most one flavor permission rule for each combination of
    'domain_id', 'project_id' and 'flavor_id'. Consequently, a flavor is only
    denied at a scope if for the corresponding project:
    - there is a 'deny' rule matching the 'flavor_id'
    - OR there is a 'deny' rule without a 'flavor_id' AND there is no 'allow'
      rule matching the 'flavor_id'

    Note: Flavor permission rules are independent of flavor privacy and the
    corresponding flavor access list. A flavor is only available to a project
    if allowed by both the flavor permission rules and the flavor access list.
    """
    # Version 1.0: Initial version
    VERSION = '1.0'

    fields = {
        'id': fields.IntegerField(),
        'uuid': fields.UUIDField(),
        'domain_id': fields.StringField(),
        'project_id': fields.StringField(nullable=True),
        'flavor_id': fields.IntegerField(nullable=True),
        'effect': fields.FlavorPermissionRuleEffectField(),
        }

    @property
    def scope(self) -> str:
        """Derived scope: 'domain' if project_id is None, else 'project'."""
        if self.project_id is None:
            return fields.FlavorPermissionRuleScope.DOMAIN
        return fields.FlavorPermissionRuleScope.PROJECT

    @staticmethod
    def _from_db_object(
        context: nova_context.RequestContext,
        rule: FlavorPermissionRule,
        db_rule: api_models.FlavorPermissionRule,
    ) -> FlavorPermissionRule:
        # NOTE(sebkro) Delete fields are not implemented in the API DB models,
        # but are inherited from NovaPersistentObject
        ignore = {'deleted': False, 'deleted_at': None}
        for field in rule.fields:
            if field in ignore and not hasattr(db_rule, field):
                setattr(rule, field, ignore[field])
            if field in db_rule:
                value = db_rule[field]
                # Translate DB sentinels to None in the object layer
                if (field in _DB_NONE_SENTINELS
                        and value == _DB_NONE_SENTINELS[field]):
                    value = None
                setattr(rule, field, value)
        rule._context = context
        rule.obj_reset_changes()
        return rule

    @staticmethod
    @db_utils.require_context
    @api_db_api.context_manager.reader
    def _get_by_id_from_db(
        context: nova_context.RequestContext,
        id: int,
    ) -> api_models.FlavorPermissionRule:
        query = context.session.query(
            api_models.FlavorPermissionRule).filter_by(id=id)
        db_rule = query.first()
        if not db_rule:
            raise exception.FlavorPermissionRuleNotFound(id=id)
        return db_rule

    @staticmethod
    @db_utils.require_context
    @api_db_api.context_manager.reader
    def _get_by_uuid_from_db(
        context: nova_context.RequestContext,
        uuid: str,
    ) -> api_models.FlavorPermissionRule:
        query = context.session.query(
            api_models.FlavorPermissionRule).filter_by(uuid=uuid)
        db_rule = query.first()
        if not db_rule:
            raise exception.FlavorPermissionRuleNotFound(id=uuid)
        return db_rule

    @staticmethod
    def _to_db_values(
        values: dict[str, ty.Any],
    ) -> dict[str, ty.Any]:
        """Translate object-layer None sentinels to DB sentinel values."""
        values = values.copy()
        for field, sentinel in _DB_NONE_SENTINELS.items():
            if values.get(field) is None:
                values[field] = sentinel
        return values

    @staticmethod
    @db_utils.require_context
    @api_db_api.context_manager.writer
    def _create_in_db(
        context: nova_context.RequestContext,
        values: dict[str, ty.Any],
    ) -> api_models.FlavorPermissionRule:
        db_rule = api_models.FlavorPermissionRule()
        values = FlavorPermissionRule._to_db_values(values)
        db_rule.update(values)
        try:
            db_rule.save(context.session)
        except db_exc.DBDuplicateEntry:
            raise exception.FlavorPermissionRuleExists(
                uuid=values.get('uuid'),
                project_id=values.get('project_id'),
                flavor_id=values.get('flavor_id'))
        return db_rule

    @staticmethod
    @db_utils.require_context
    @api_db_api.context_manager.writer
    def _destroy_in_db(
        context: nova_context.RequestContext,
        id: int,
    ) -> None:
        result = context.session.query(
            api_models.FlavorPermissionRule).filter_by(id=id).delete()
        if not result:
            raise exception.FlavorPermissionRuleNotFound(id=id)

    @staticmethod
    @db_utils.require_context
    @api_db_api.context_manager.writer
    def _save(
        context: nova_context.RequestContext,
        id: int,
        values: dict[str, ty.Any],
    ) -> api_models.FlavorPermissionRule:
        db_rule = FlavorPermissionRule._get_by_id_from_db(context, id)
        values = FlavorPermissionRule._to_db_values(values)
        db_rule.update(values)
        try:
            db_rule.save(context.session)
        except db_exc.DBDuplicateEntry:
            raise exception.FlavorPermissionRuleExists(
                uuid=values.get('uuid'),
                project_id=values.get('project_id'),
                flavor_id=values.get('flavor_id'))
        return db_rule

    @base.remotable_classmethod
    def get_by_id(
        cls,
        context: nova_context.RequestContext,
        id: int,
    ) -> FlavorPermissionRule:
        db_rule = cls._get_by_id_from_db(context, id)
        return cls._from_db_object(context, cls(context), db_rule)

    @base.remotable_classmethod
    def get_by_uuid(
        cls,
        context: nova_context.RequestContext,
        uuid: str,
    ) -> FlavorPermissionRule:
        db_rule = cls._get_by_uuid_from_db(context, uuid)
        return cls._from_db_object(context, cls(context), db_rule)

    @base.remotable
    def create(self) -> None:
        if not self.obj_attr_is_set('uuid'):
            self.uuid = uuidutils.generate_uuid()
        updates = self.obj_get_changes()
        db_rule = self._create_in_db(self._context, updates)
        self._from_db_object(self._context, self, db_rule)

    @base.remotable
    def destroy(self) -> None:
        self._destroy_in_db(self._context, self.id)

    @base.remotable
    def save(self) -> None:
        updates = self.obj_get_changes()
        if updates:
            db_rule = self._save(self._context, self.id, updates)
            # Refresh updated_at.
            self._from_db_object(self._context, self, db_rule)


@base.NovaObjectRegistry.register
class FlavorPermissionRuleList(base.ObjectListBase, base.NovaObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    fields = {
        'objects': fields.ListOfObjectsField('FlavorPermissionRule'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objects = []
        self.obj_reset_changes()

    @staticmethod
    @db_utils.require_context
    @api_db_api.context_manager.reader
    def _get_from_db(
        context: nova_context.RequestContext,
        filter_domain_rules_by_context_domain: bool = True,
        filter_project_rules_by_context_domain: bool = False,
        filter_project_rules_by_context_project: bool = True,
        domain_ids: set[str] | None = None,
        project_ids: set[str] | None = None,
        scope: str | None = None,
        effect: str | None = None,
        flavor_ids: set[int | None] | None = None,
        limit: int | None = None,
        marker: str | None = None,
    ) -> list[api_models.FlavorPermissionRule]:
        """Get flavor permission rules from the database.

        :param filter_domain_rules_by_context_domain: If True, restrict
            domain-scope rules to 'context.project_domain_id'.
        :param filter_project_rules_by_context_domain: If True, restrict
            project-scope rules to 'context.project_domain_id'. Ignored if
            'filter_project_rules_by_context_project' is True.
        :param filter_project_rules_by_context_project: If True, restrict
            project-scope rules to 'context.project_id'.
        :param domain_ids: Filter all rules by 'domain_id'. None means no
            filter.
        :param project_ids: Filter all rules by 'project_id'. None means no
            filter.
        :param scope: Filter by scope ('domain' or 'project').
        :param effect: Filter by effect ('allow' or 'deny').
        :param flavor_ids: Filter all rules by 'flavor_id'. None means no
            filter. None inside the set matches the domains' and projects'
            default rules for all flavors.
        :param limit: Maximum number of rules to return.
        :param marker: UUID of the last rule in the previous page.
        """
        FPR = api_models.FlavorPermissionRule
        query = context.session.query(FPR)

        # Build scope-aware OR conditions to filter by context project domain
        # and/or context project.
        scoped_filters = []

        # Perform domain-scope filtering
        if scope != fields.FlavorPermissionRuleScope.PROJECT:
            if not filter_domain_rules_by_context_domain:
                # Disable domain-scope filtering
                scoped_filters.append(FPR.project_id == '')
            elif context.project_domain_id:
                # Filter domain-scope rules by context project domain
                scoped_filters.append(sa.and_(
                    FPR.project_id == '',
                    FPR.domain_id == context.project_domain_id))
            else:
                # Exclude domain-scope rules since the context has no project
                # domain
                pass

        # Perform project-scope filtering
        if scope != fields.FlavorPermissionRuleScope.DOMAIN:
            if filter_project_rules_by_context_project:
                if context.project_id:
                    # Filter project-scope rules by context project
                    scoped_filters.append(
                        FPR.project_id == context.project_id)
                else:
                    # Exclude project-scope rules since the context has no
                    # project
                    pass
            elif filter_project_rules_by_context_domain:
                if context.project_domain_id:
                    # Filter project-scope rules by context project domain
                    scoped_filters.append(sa.and_(
                        FPR.project_id != '',
                        FPR.domain_id == context.project_domain_id))
                else:
                    # Exclude project-scope rules since the context has no
                    # project domain
                    pass
            else:
                # Disable project-scope filtering
                scoped_filters.append(FPR.project_id != '')

        if not scoped_filters:
            # Both domain-scope and project-scope rules are filtered out
            return []

        query = query.filter(sa.or_(*scoped_filters))
        if domain_ids is not None:
            query = query.filter(FPR.domain_id.in_(domain_ids))
        if project_ids is not None:
            query = query.filter(FPR.project_id.in_(project_ids))
        if effect is not None:
            query = query.filter(FPR.effect == effect)
        if flavor_ids is not None:
            if not flavor_ids:
                return []

            # Translate None (default rules) to the DB sentinel -1
            db_flavor_ids = {-1 if f is None else f for f in flavor_ids}
            query = query.filter(FPR.flavor_id.in_(db_flavor_ids))

        marker_rule = None
        if marker is not None:
            marker_query = context.session.query(FPR).filter_by(uuid=marker)
            marker_rule = marker_query.first()
            if not marker_rule:
                raise exception.MarkerNotFound(marker=marker)

        query = paginate_query(query, FPR, limit, ['id'], marker=marker_rule)
        return query.all()

    @base.remotable_classmethod
    def get_all(
        cls,
        context: nova_context.RequestContext,
        filter_domain_rules_by_context_domain: bool = True,
        filter_project_rules_by_context_domain: bool = False,
        filter_project_rules_by_context_project: bool = True,
        domain_ids: set[str] | None = None,
        project_ids: set[str] | None = None,
        scope: str | None = None,
        effect: str | None = None,
        flavor_ids: set[int | None] | None = None,
        limit: int | None = None,
        marker: str | None = None,
    ) -> FlavorPermissionRuleList:
        """Get flavor permission rules.

        :param filter_domain_rules_by_context_domain: If True, restrict
            domain-scope rules to 'context.project_domain_id'.
        :param filter_project_rules_by_context_domain: If True, restrict
            project-scope rules to 'context.project_domain_id'. Ignored if
            'filter_project_rules_by_context_project' is True.
        :param filter_project_rules_by_context_project: If True, restrict
            project-scope rules to 'context.project_id'.
        :param domain_ids: Filter all rules by 'domain_id'. None means no
            filter.
        :param project_ids: Filter all rules by 'project_id'. None means no
            filter.
        :param scope: Filter by scope ('domain' or 'project').
        :param effect: Filter by effect ('allow' or 'deny').
        :param flavor_ids: Filter all rules by 'flavor_id'. None means no
            filter. None inside the set matches the domains' and projects'
            default rules for all flavors.
        :param limit: Maximum number of rules to return.
        :param marker: UUID of the last rule in the previous page.
        """
        db_rules = cls._get_from_db(
            context,
            filter_domain_rules_by_context_domain=(
                filter_domain_rules_by_context_domain),
            filter_project_rules_by_context_domain=(
                filter_project_rules_by_context_domain),
            filter_project_rules_by_context_project=(
                filter_project_rules_by_context_project),
            domain_ids=domain_ids, project_ids=project_ids, scope=scope,
            effect=effect, flavor_ids=flavor_ids, limit=limit,
            marker=marker)
        return base.obj_make_list(
            context, cls(context), FlavorPermissionRule, db_rules
        )
