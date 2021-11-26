# Copyright (c) 2014 Cisco Systems, Inc.
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

"""The Server Group API Extension."""

import collections
import itertools

from oslo_log import log as logging
import webob
from webob import exc

from nova.api.openstack import api_version_request
from nova.api.openstack import common
from nova.api.openstack.compute.schemas import server_groups as schema
from nova.api.openstack import wsgi
from nova.api import validation
from nova import compute
import nova.conf
from nova import context as nova_context
import nova.exception
from nova.i18n import _
from nova import objects
from nova.objects import service
from nova.policies import server_groups as sg_policies

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF


GROUP_POLICY_OBJ_MICROVERSION = "2.64"


def _authorize_context(req, action):
    context = req.environ['nova.context']
    context.can(sg_policies.POLICY_ROOT % action)
    return context


def _get_not_deleted(context, uuids, not_deleted_inst=None):
    if not_deleted_inst:
        # short-cut if we already pre-built a list of not deleted instances to
        # be more efficient
        return {u: not_deleted_inst[u] for u in uuids
                if u in not_deleted_inst}

    mappings = objects.InstanceMappingList.get_by_instance_uuids(
        context, uuids)
    inst_by_cell = collections.defaultdict(list)
    cell_mappings = {}
    found_inst = {}

    # Get a master list of cell mappings, and a list of instance
    # uuids organized by cell
    for im in mappings:
        if not im.cell_mapping:
            # Not scheduled yet, so just throw it in the final list
            # and move on
            found_inst[im.instance_uuid] = None
            continue
        if im.cell_mapping.uuid not in cell_mappings:
            cell_mappings[im.cell_mapping.uuid] = im.cell_mapping
        inst_by_cell[im.cell_mapping.uuid].append(im.instance_uuid)

    # Query each cell for the instances that are inside, building
    # a list of non-deleted instance uuids.
    for cell_uuid, cell_mapping in cell_mappings.items():
        inst_uuids = inst_by_cell[cell_uuid]
        LOG.debug('Querying cell %(cell)s for %(num)i instances',
                  {'cell': cell_mapping.identity, 'num': len(uuids)})
        filters = {'uuid': inst_uuids, 'deleted': False}
        with nova_context.target_cell(context, cell_mapping) as ctx:
            instances = objects.InstanceList.get_by_filters(
                            ctx, filters=filters)
            found_inst.update({inst.uuid: inst.host for inst in instances})

    return found_inst


def _should_enable_custom_max_server_rules(context, rules):
    if rules and int(rules.get('max_server_per_host', 1)) > 1:
        minver = service.get_minimum_version_all_cells(
            context, ['nova-compute'])
        if minver < 33:
            return False
    return True


class ServerGroupController(wsgi.Controller):
    """The Server group API controller for the OpenStack API."""

    def __init__(self, **kwargs):
        super(ServerGroupController, self).__init__(**kwargs)
        self.compute_api = compute.API()

    def _format_server_group(self, context, group, req,
                             not_deleted_inst=None):
        """Format ServerGroup according to API version.

        Displays only not-deleted members.

        :param:not_deleted_inst: Pre-built dict of instance-uuid: host for
                                 multiple server-groups that are found to be
                                 not deleted.
        """
        # the id field has its value as the uuid of the server group
        # There is no 'uuid' key in server_group seen by clients.
        # In addition, clients see policies as a ["policy-name"] list;
        # and they see members as a ["server-id"] list.
        server_group = {}
        server_group['id'] = group.uuid
        server_group['name'] = group.name
        if api_version_request.is_supported(
                req, min_version=GROUP_POLICY_OBJ_MICROVERSION):
            server_group['policy'] = group.policy
            server_group['rules'] = group.rules
        else:
            server_group['policies'] = group.policies or []
            # NOTE(yikun): Before v2.64, a empty metadata is exposed to the
            # user, and it is removed since v2.64.
            server_group['metadata'] = {}
        members = []
        if group.members:
            # Display the instances that are not deleted.
            members = list(_get_not_deleted(context, group.members,
                           not_deleted_inst))
        server_group['members'] = members
        # Add project id information to the response data for
        # API version v2.13
        if api_version_request.is_supported(req, min_version="2.13"):
            server_group['project_id'] = group.project_id
            server_group['user_id'] = group.user_id
        return server_group

    @wsgi.expected_errors(404)
    def show(self, req, id):
        """Return data about the given server group."""
        context = _authorize_context(req, 'show')
        try:
            sg = objects.InstanceGroup.get_by_uuid(context, id)
        except nova.exception.InstanceGroupNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())
        return {'server_group': self._format_server_group(context, sg, req)}

    @wsgi.response(204)
    @wsgi.expected_errors(404)
    def delete(self, req, id):
        """Delete a server group."""
        context = _authorize_context(req, 'delete')
        try:
            sg = objects.InstanceGroup.get_by_uuid(context, id)
        except nova.exception.InstanceGroupNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())
        try:
            sg.destroy()
        except nova.exception.InstanceGroupNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())

    @wsgi.expected_errors(())
    @validation.query_schema(schema.server_groups_query_param)
    def index(self, req):
        """Returns a list of server groups."""
        context = _authorize_context(req, 'index')
        project_id = context.project_id
        if 'all_projects' in req.GET and context.is_admin:
            sgs = objects.InstanceGroupList.get_all(context)
        else:
            sgs = objects.InstanceGroupList.get_by_project_id(
                    context, project_id)
        limited_list = common.limited(sgs.objects, req)

        members = list(itertools.chain.from_iterable(sg.members
                                                     for sg in limited_list
                                                     if sg.members))
        not_deleted = _get_not_deleted(context, members)
        result = [self._format_server_group(context, group, req, not_deleted)
                  for group in limited_list]
        return {'server_groups': result}

    @wsgi.Controller.api_version("2.1")
    @wsgi.expected_errors((400, 403, 409))
    @validation.schema(schema.create, "2.0", "2.14")
    @validation.schema(schema.create_v215, "2.15", "2.63")
    @validation.schema(schema.create_v264, GROUP_POLICY_OBJ_MICROVERSION)
    def create(self, req, body):
        """Creates a new server group."""
        context = _authorize_context(req, 'create')

        try:
            objects.Quotas.check_deltas(context, {'server_groups': 1},
                                        context.project_id, context.user_id)
        except nova.exception.OverQuota:
            msg = _("Quota exceeded, too many server groups.")
            raise exc.HTTPForbidden(explanation=msg)

        vals = body['server_group']

        if api_version_request.is_supported(
                req, GROUP_POLICY_OBJ_MICROVERSION):
            policy = vals['policy']
            rules = vals.get('rules', {})
            if policy != 'anti-affinity' and rules:
                msg = _("Only anti-affinity policy supports rules.")
                raise exc.HTTPBadRequest(explanation=msg)
            # NOTE(yikun): This should be removed in Stein version.
            if not _should_enable_custom_max_server_rules(context, rules):
                msg = _("Creating an anti-affinity group with rule "
                        "max_server_per_host > 1 is not yet supported.")
                raise exc.HTTPConflict(explanation=msg)
            sg = objects.InstanceGroup(context, policy=policy,
                                       rules=rules)
        else:
            policies = vals.get('policies')
            sg = objects.InstanceGroup(context, policy=policies[0])
        try:
            sg.name = vals.get('name')
            sg.project_id = context.project_id
            sg.user_id = context.user_id
            sg.create()
        except ValueError as e:
            raise exc.HTTPBadRequest(explanation=e)

        # NOTE(melwitt): We recheck the quota after creating the object to
        # prevent users from allocating more resources than their allowed quota
        # in the event of a race. This is configurable because it can be
        # expensive if strict quota limits are not required in a deployment.
        if CONF.quota.recheck_quota:
            try:
                objects.Quotas.check_deltas(context, {'server_groups': 0},
                                            context.project_id,
                                            context.user_id)
            except nova.exception.OverQuota:
                sg.destroy()
                msg = _("Quota exceeded, too many server groups.")
                raise exc.HTTPForbidden(explanation=msg)

        return {'server_group': self._format_server_group(context, sg, req)}

    @wsgi.Controller.api_version("2.64")
    @validation.schema(schema.update)
    @wsgi.expected_errors((400, 404))
    def update(self, req, id, body):
        """Update a server-group's members

        Striving for idempotency, we accept already removed or already
        contained members.

        We always remove first and then check if we can add the requested
        members. That way, removing an instance for a host and adding another
        one works in one request.

        We do all requested changes or no change.
        """
        context = _authorize_context(req, 'update')
        try:
            sg = objects.InstanceGroup.get_by_uuid(context, id)
        except nova.exception.InstanceGroupNotFound as e:
            raise webob.exc.HTTPNotFound(explanation=e.format_message())

        members_to_remove = set(body.get('remove_members', []))
        members_to_add = set(body.get('add_members', []))
        LOG.info('Called update for server-group %s with add_members: %s and '
                 'remove_members %s',
                 id, ', '.join(members_to_add), ', '.join(members_to_remove))

        overlap = members_to_remove & members_to_add
        if overlap:
            msg = ('Parameters "add_members" and "remove_members" are '
                  'overlapping in {}'.format(', '.join(overlap)))
            raise exc.HTTPBadRequest(explanation=msg)

        if not members_to_remove and not members_to_add:
            LOG.info("No update requested.")
            formatted_sg = self._format_server_group(context, sg, req)
            return {'server_group': formatted_sg}

        # don't do work if it's not necessary. we might be able to get a fast
        # way out if this request is already fulfilled
        members_to_remove = members_to_remove & set(sg.members)
        members_to_add = members_to_add - set(sg.members)

        if not members_to_remove and not members_to_add:
            LOG.info("State already satisfied.")
            formatted_sg = self._format_server_group(context, sg, req)
            return {'server_group': formatted_sg}

        # retrieve all the instances to add, failing if one doesn't exist,
        # because we need to check the hosts against the policy and adding
        # non-existent instances doesn't make sense
        members_to_search = members_to_add | members_to_remove
        found_instances_hosts = _get_not_deleted(context, members_to_search)
        missing_uuids = members_to_add - set(found_instances_hosts)
        if missing_uuids:
            msg = ("One or more members in add_members cannot be found: {}"
                   .format(', '.join(missing_uuids)))
            raise exc.HTTPBadRequest(explanation=msg)

        # check if (some of) the VMs are already members of another
        # instance_group. We cannot support this as they might contradict.
        found_server_groups = \
            objects.InstanceGroupList.get_by_instance_uuids(context,
                                                            members_to_add)
        other_server_groups = [_x.uuid for _x in found_server_groups
                               if _x.uuid != id]
        if other_server_groups:
            msg = ("One ore more members in add_members is already assigned "
                   "to another server group. Server groups: {}"
                   .format(', '.join(other_server_groups)))
            raise exc.HTTPBadRequest(explanation=msg)

        # check if the policy is still valid with these changes
        if sg.policy in ('affinity', 'anti-affinity'):
            current_members_hosts = _get_not_deleted(context, sg.members)
            current_hosts = set(h for u, h in current_members_hosts.items()
                                if u not in members_to_remove)
            if sg.policy == 'affinity':
                outliers = [u for u, h in found_instances_hosts.items()
                            if h and h not in current_hosts]
            elif sg.policy == 'anti-affinity':
                outliers = [u for u, h in found_instances_hosts.items()
                            if h and h in current_hosts]
            else:
                outliers = None
                LOG.warning('server-group update check not implemented for '
                            'policy %s', sg.policy)
            if outliers:
                LOG.info('Update of server-group %s with policy %s aborted: '
                         'policy violation by %s',
                         id, sg.policy, ', '.join(outliers))
                msg = ("Adding instance(s) {} would violate policy '{}'."
                       .format(', '.join(outliers), sg.policy))
                raise exc.HTTPBadRequest(explanation=msg)

        # update the server group and save it
        if members_to_remove:
            objects.InstanceGroup.remove_members(context, sg.id,
                                                 members_to_remove, sg.uuid)
        if members_to_add:
            try:
                objects.InstanceGroup.add_members(context, id, members_to_add)
            except Exception:
                LOG.exception('Failed to add members.')
                if members_to_remove:
                    LOG.info('Trying to add removed members again after '
                             'error.')
                    objects.InstanceGroup.add_members(context, id,
                                                      members_to_remove)
                raise

        LOG.info("Changed server-group %s in DB.", id)

        # refresh InstanceGroup object, because we changed it directly in the
        # DB.
        sg.refresh()

        # update the request-specs of the updated members
        for member_uuid in found_instances_hosts:
            request_spec = \
                objects.RequestSpec.get_by_instance_uuid(context, member_uuid)
            if member_uuid in members_to_add:
                request_spec.instance_group = sg
            else:
                request_spec.instance_group = None
            request_spec.save()

        # tell the compute hosts about the update, so they can sync if
        # necessary
        hosts_to_update = set(h for h in found_instances_hosts.values() if h)
        self.compute_api.sync_server_group(context, hosts_to_update, id)

        return {'server_group': self._format_server_group(context, sg, req)}
