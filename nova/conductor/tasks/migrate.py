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

from oslo_log import log as logging
from oslo_serialization import jsonutils
import oslo_messaging as messaging
import six
import nova.conf
from nova.compute import power_state
from nova import availability_zones
from nova.conductor.tasks import base
from nova import exception
from nova.i18n import _
from nova import objects
from nova.scheduler import client as scheduler_client
from nova.scheduler import utils as scheduler_utils
from nova import utils

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF

def replace_allocation_with_migration(context, instance, migration):
    """Replace instance's allocation with one for a migration.

    :returns: (source_compute_node, migration_allocation)
    """
    try:
        source_cn = objects.ComputeNode.get_by_host_and_nodename(
            context, instance.host, instance.node)
    except exception.ComputeHostNotFound:
        LOG.error('Unable to find record for source '
                  'node %(node)s on %(host)s',
                  {'host': instance.host, 'node': instance.node},
                  instance=instance)
        # A generic error like this will just error out the migration
        # and do any rollback required
        raise

    schedclient = scheduler_client.SchedulerClient()
    reportclient = schedclient.reportclient

    orig_alloc = reportclient.get_allocations_for_consumer_by_provider(
        context, source_cn.uuid, instance.uuid)
    if not orig_alloc:
        LOG.debug('Unable to find existing allocations for instance on '
                  'source compute node: %s. This is normal if you are not '
                  'using the FilterScheduler.', source_cn.uuid,
                  instance=instance)
        return None, None

    # FIXME(danms): This method is flawed in that it asssumes allocations
    # against only one provider. So, this may overwite allocations against
    # a shared provider, if we had one.
    success = reportclient.set_and_clear_allocations(
        context, source_cn.uuid, migration.uuid, orig_alloc,
        instance.project_id, instance.user_id, consumer_to_clear=instance.uuid)
    if not success:
        LOG.error('Unable to replace resource claim on source '
                  'host %(host)s node %(node)s for instance',
                  {'host': instance.host,
                   'node': instance.node},
                  instance=instance)
        # Mimic the "no space" error that could have come from the
        # scheduler. Once we have an atomic replace operation, this
        # would be a severe error.
        raise exception.NoValidHost(
            reason=_('Unable to replace instance claim on source'))
    else:
        LOG.debug('Created allocations for migration %(mig)s on %(rp)s',
                  {'mig': migration.uuid, 'rp': source_cn.uuid})

    return source_cn, orig_alloc


def revert_allocation_for_migration(context, source_cn, instance, migration,
                                    orig_alloc):
    """Revert an allocation made for a migration back to the instance."""

    schedclient = scheduler_client.SchedulerClient()
    reportclient = schedclient.reportclient

    # FIXME(danms): This method is flawed in that it asssumes allocations
    # against only one provider. So, this may overwite allocations against
    # a shared provider, if we had one.
    success = reportclient.set_and_clear_allocations(
        context, source_cn.uuid, instance.uuid, orig_alloc,
        instance.project_id, instance.user_id,
        consumer_to_clear=migration.uuid)
    if not success:
        LOG.error('Unable to replace resource claim on source '
                  'host %(host)s node %(node)s for instance',
                  {'host': instance.host,
                   'node': instance.node},
                  instance=instance)
    else:
        LOG.debug('Created allocations for instance %(inst)s on %(rp)s',
                  {'inst': instance.uuid, 'rp': source_cn.uuid})


def should_do_migration_allocation(context):
    minver = objects.Service.get_minimum_version_multi(context,
                                                       ['nova-compute'])
    return minver >= 23


class MigrationTask(base.TaskBase):
    def __init__(self, context, instance, flavor,
                 request_spec, clean_shutdown, compute_rpcapi,
                 scheduler_client, host_list):
        super(MigrationTask, self).__init__(context, instance)
        self.clean_shutdown = clean_shutdown
        self.request_spec = request_spec
        self.flavor = flavor

        self.compute_rpcapi = compute_rpcapi
        self.scheduler_client = scheduler_client
        self.reportclient = scheduler_client.reportclient
        self.host_list = host_list

        # Persist things from the happy path so we don't have to look
        # them up if we need to roll back
        self._migration = None
        self._held_allocations = None
        self._source_cn = None

    def _preallocate_migration(self):
        if not should_do_migration_allocation(self.context):
            # NOTE(danms): We can't pre-create the migration since we have
            # old computes. Let the compute do it (legacy behavior).
            return None

        # If this is a rescheduled migration, don't create a new record.
        migration_type = ("resize" if self.instance.flavor.id != self.flavor.id
                else "migration")
        filters = {"instance_uuid": self.instance.uuid,
                   "migration_type": migration_type,
                   "status": "pre-migrating"}
        migrations = objects.MigrationList.get_by_filters(self.context,
                filters).objects
        if migrations:
            migration = migrations[0]
        else:
            migration = objects.Migration(context=self.context.elevated())
            migration.old_instance_type_id = self.instance.flavor.id
            migration.new_instance_type_id = self.flavor.id
            migration.status = 'pre-migrating'
            migration.instance_uuid = self.instance.uuid
            migration.source_compute = self.instance.host
            migration.source_node = self.instance.node
            migration.migration_type = migration_type
            migration.create()

        self._migration = migration

        self._source_cn, self._held_allocations = (
            replace_allocation_with_migration(self.context,
                                              self.instance,
                                              self._migration))

        return migration

    def _execute(self):
        # TODO(sbauza): Remove that once prep_resize() accepts a  RequestSpec
        # object in the signature and all the scheduler.utils methods too
        legacy_spec = self.request_spec.to_legacy_request_spec_dict()
        legacy_props = self.request_spec.to_legacy_filter_properties_dict()
        scheduler_utils.setup_instance_group(self.context, self.request_spec)
        # If a target host is set in a requested destination,
        # 'populate_retry' need not be executed.
        if not ('requested_destination' in self.request_spec and
                    self.request_spec.requested_destination and
                        'host' in self.request_spec.requested_destination):
            scheduler_utils.populate_retry(legacy_props,
                                           self.instance.uuid)

        # NOTE(sbauza): Force_hosts/nodes needs to be reset
        # if we want to make sure that the next destination
        # is not forced to be the original host
        self.request_spec.reset_forced_destinations()

        # NOTE(danms): Right now we only support migrate to the same
        # cell as the current instance, so request that the scheduler
        # limit thusly.
        instance_mapping = objects.InstanceMapping.get_by_instance_uuid(
            self.context, self.instance.uuid)
        LOG.debug('Requesting cell %(cell)s while migrating',
                  {'cell': instance_mapping.cell_mapping.identity},
                  instance=self.instance)
        if ('requested_destination' in self.request_spec and
                self.request_spec.requested_destination):
            self.request_spec.requested_destination.cell = (
                instance_mapping.cell_mapping)
            # NOTE(takashin): In the case that the target host is specified,
            # if the migration is failed, it is not necessary to retry
            # the cold migration to the same host. So make sure that
            # reschedule will not occur.
            if 'host' in self.request_spec.requested_destination:
                legacy_props.pop('retry', None)
                self.request_spec.retry = None
        else:
            self.request_spec.requested_destination = objects.Destination(
                cell=instance_mapping.cell_mapping)

        # Once _preallocate_migration() is done, the source node allocation is
        # moved from the instance consumer to the migration record consumer,
        # and the instance consumer doesn't have any allocations. If this is
        # the first time through here (not a reschedule), select_destinations
        # below will allocate resources on the selected destination node for
        # the instance consumer. If we're rescheduling, host_list is not None
        # and we'll call claim_resources for the instance and the selected
        # alternate. If we exhaust our alternates and raise MaxRetriesExceeded,
        # the rollback() method should revert the allocation swaparoo and move
        # the source node allocation from the migration record back to the
        # instance record.
        migration = self._preallocate_migration()

        self.request_spec.ensure_project_id(self.instance)
        # On an initial call to migrate, 'self.host_list' will be None, so we
        # have to call the scheduler to get a list of acceptable hosts to
        # migrate to. That list will consist of a selected host, along with
        # zero or more alternates. On a reschedule, though, the alternates will
        # be passed to this object and stored in 'self.host_list', so we can
        # pop the first alternate from the list to use for the destination, and
        # pass the remaining alternates to the compute.
        if self.host_list is None:
            selection_lists = self.scheduler_client.select_destinations(
                    self.context, self.request_spec, [self.instance.uuid],
                    return_objects=True, return_alternates=True)
            # Since there is only ever one instance to migrate per call, we
            # just need the first returned element.
            selection_list = selection_lists[0]
            # The selected host is the first item in the list, with the
            # alternates being the remainder of the list.
            selection, self.host_list = selection_list[0], selection_list[1:]
        else:
            # This is a reschedule that will use the supplied alternate hosts
            # in the host_list as destinations. Since the resources on these
            # alternates may have been consumed and might not be able to
            # support the migrated instance, we need to first claim the
            # resources to verify the host still has sufficient availabile
            # resources.
            elevated = self.context.elevated()
            host_available = False
            while self.host_list and not host_available:
                selection = self.host_list.pop(0)
                if selection.allocation_request:
                    alloc_req = jsonutils.loads(selection.allocation_request)
                else:
                    alloc_req = None
                if alloc_req:
                    # If this call succeeds, the resources on the destination
                    # host will be claimed by the instance.
                    host_available = scheduler_utils.claim_resources(
                            elevated, self.reportclient, self.request_spec,
                            self.instance.uuid, alloc_req,
                            selection.allocation_request_version)
                else:
                    # Some deployments use different schedulers that do not
                    # use Placement, so they will not have an
                    # allocation_request to claim with. For those cases,
                    # there is no concept of claiming, so just assume that
                    # the host is valid.
                    host_available = True
            # There are no more available hosts. Raise a MaxRetriesExceeded
            # exception in that case.
            if not host_available:
                reason = ("Exhausted all hosts available for retrying build "
                          "failures for instance %(instance_uuid)s." %
                          {"instance_uuid": self.instance.uuid})
                raise exception.MaxRetriesExceeded(reason=reason)

        scheduler_utils.populate_filter_properties(legacy_props, selection)
        # context is not serializable
        legacy_props.pop('context', None)

        (host, node) = (selection.service_host, selection.nodename)

        self.instance.availability_zone = (
            availability_zones.get_host_availability_zone(
                self.context, host))

        # FIXME(sbauza): Serialize/Unserialize the legacy dict because of
        # oslo.messaging #1529084 to transform datetime values into strings.
        # tl;dr: datetimes in dicts are not accepted as correct values by the
        # rpc fake driver.
        legacy_spec = jsonutils.loads(jsonutils.dumps(legacy_spec))

        LOG.debug("Calling prep_resize with selected host: %s; "
                  "Selected node: %s; Alternates: %s", host, node,
                  self.host_list, instance=self.instance)
        # RPC cast to the destination host to start the migration process.
        self.compute_rpcapi.prep_resize(
            self.context, self.instance, legacy_spec['image'],
            self.flavor, host, migration,
            request_spec=legacy_spec, filter_properties=legacy_props,
            node=node, clean_shutdown=self.clean_shutdown,
            host_list=self.host_list)

    def rollback(self):
        if self._migration:
            self._migration.status = 'error'
            self._migration.save()

        if not self._held_allocations:
            return

        # NOTE(danms): We created new-style migration-based
        # allocations for the instance, but failed before we kicked
        # off the migration in the compute. Normally the latter would
        # do that cleanup but we never got that far, so do it here and
        # now.

        revert_allocation_for_migration(self.context, self._source_cn,
                                        self.instance, self._migration,
                                        self._held_allocations)

class MigrateWithVolumeTask(base.TaskBase):
    def __init__(self, context, instance, destination,
                 block_migration, disk_over_commit, migration, compute_rpcapi,
                 servicegroup_api, scheduler_client, request_spec=None):
        super(MigrateWithVolumeTask, self).__init__(context, instance)
        self.destination = destination
        self.block_migration = block_migration
        self.disk_over_commit = disk_over_commit
        self.migration = migration
        self.source = instance.host
        self.migrate_data = None

        self.compute_rpcapi = compute_rpcapi
        self.servicegroup_api = servicegroup_api
        self.scheduler_client = scheduler_client
        self.request_spec = request_spec
        self._source_cn = None
        self._held_allocations = None

    def _execute(self):
        self._check_instance_is_active()
        self._check_host_is_up(self.source)

        if should_do_migration_allocation(self.context):
            self._source_cn, self._held_allocations = (
                # NOTE(danms): This may raise various exceptions, which will
                # propagate to the API and cause a 500. This is what we
                # want, as it would indicate internal data structure corruption
                # (such as missing migrations, compute nodes, etc).
                replace_allocation_with_migration(self.context,
                                                          self.instance,
                                                          self.migration))

        if not self.destination:
            # Either no host was specified in the API request and the user
            # wants the scheduler to pick a destination host, or a host was
            # specified but is not forcing it, so they want the scheduler
            # filters to run on the specified host, like a scheduler hint.
            self.destination, dest_node = self._find_destination()
        else:
            # This is the case that the user specified the 'force' flag when
            # live migrating with a specific destination host so the scheduler
            # is bypassed. There are still some minimal checks performed here
            # though.
            source_node, dest_node = self._check_requested_destination()
            # Now that we're semi-confident in the force specified host, we
            # need to copy the source compute node allocations in Placement
            # to the destination compute node. Normally select_destinations()
            # in the scheduler would do this for us, but when forcing the
            # target host we don't call the scheduler.
            # TODO(mriedem): In Queens, call select_destinations() with a
            # skip_filters=True flag so the scheduler does the work of claiming
            # resources on the destination in Placement but still bypass the
            # scheduler filters, which honors the 'force' flag in the API.
            # This raises NoValidHost which will be handled in
            # ComputeTaskManager.
            scheduler_utils.claim_resources_on_destination(
                self.context, self.scheduler_client.reportclient,
                self.instance, source_node, dest_node,
                source_node_allocations=self._held_allocations)

            # dest_node is a ComputeNode object, so we need to get the actual
            # node name off it to set in the Migration object below.
            dest_node = dest_node.hypervisor_hostname

        self.migration.source_node = self.instance.node
        self.migration.dest_node = dest_node
        self.migration.dest_compute = self.destination
        self.migration.save()

        # TODO(johngarbutt) need to move complexity out of compute manager
        # TODO(johngarbutt) disk_over_commit?
        return self.compute_rpcapi.cold_migration_with_volumes(self.context,
                host=self.source,
                instance=self.instance,
                dest=self.destination,
                block_migration=self.block_migration,
                migration=self.migration,
                migrate_data=self.migrate_data)

    def rollback(self):
        # TODO(johngarbutt) need to implement the clean up operation
        # but this will make sense only once we pull in the compute
        # calls, since this class currently makes no state changes,
        # except to call the compute method, that has no matching
        # rollback call right now.
        if self._held_allocations:
            self.revert_allocation_for_migration(self.context,
                                                    self._source_cn,
                                                    self.instance,
                                                    self.migration,
                                                    self._held_allocations)

    def _check_instance_is_active(self):
        if self.instance.power_state not in (power_state.RUNNING,
                                             power_state.PAUSED):
            raise exception.InstanceInvalidState(
                    instance_uuid=self.instance.uuid,
                    attr='power_state',
                    state=self.instance.power_state,
                    method='live migrate')

    def _check_host_is_up(self, host):
        service = objects.Service.get_by_compute_host(self.context, host)

        if not self.servicegroup_api.service_is_up(service):
            raise exception.ComputeServiceUnavailable(host=host)

    def _check_requested_destination(self):
        """Performs basic pre-live migration checks for the forced host.

        :returns: tuple of (source ComputeNode, destination ComputeNode)
        """
        self._check_destination_is_not_source()
        self._check_host_is_up(self.destination)
        self._check_destination_has_enough_memory()
        source_node, dest_node = self._check_compatible_with_source_hypervisor(
            self.destination)
        self._call_coldm_checks_on_host(self.destination)
        # Make sure the forced destination host is in the same cell that the
        # instance currently lives in.
        # NOTE(mriedem): This can go away if/when the forced destination host
        # case calls select_destinations.
        source_cell_mapping = self._get_source_cell_mapping()
        dest_cell_mapping = self._get_destination_cell_mapping()
        if source_cell_mapping.uuid != dest_cell_mapping.uuid:
            raise exception.MigrationPreCheckError(
                reason=(_('Unable to force live migrate instance %s '
                          'across cells.') % self.instance.uuid))
        return source_node, dest_node

    def _check_destination_is_not_source(self):
        if self.destination == self.source:
            raise exception.UnableToMigrateToSelf(
                    instance_id=self.instance.uuid, host=self.destination)

    def _check_destination_has_enough_memory(self):
        # TODO(mriedem): This method can be removed when the forced host
        # scenario is calling select_destinations() in the scheduler because
        # Placement will be used to filter allocation candidates by MEMORY_MB.
        # We likely can't remove it until the CachingScheduler is gone though
        # since the CachingScheduler does not use Placement.
        compute = self._get_compute_info(self.destination)
        free_ram_mb = compute.free_ram_mb
        total_ram_mb = compute.memory_mb
        mem_inst = self.instance.memory_mb
        # NOTE(sbauza): Now the ComputeNode object reports an allocation ratio
        # that can be provided by the compute_node if new or by the controller
        ram_ratio = compute.ram_allocation_ratio

        # NOTE(sbauza): Mimic the RAMFilter logic in order to have the same
        # ram validation
        avail = total_ram_mb * ram_ratio - (total_ram_mb - free_ram_mb)
        if not mem_inst or avail <= mem_inst:
            instance_uuid = self.instance.uuid
            dest = self.destination
            reason = _("Unable to migrate %(instance_uuid)s to %(dest)s: "
                       "Lack of memory(host:%(avail)s <= "
                       "instance:%(mem_inst)s)")
            raise exception.MigrationPreCheckError(reason=reason % dict(
                    instance_uuid=instance_uuid, dest=dest, avail=avail,
                    mem_inst=mem_inst))

    def _get_compute_info(self, host):
        return objects.ComputeNode.get_first_node_by_host_for_old_compat(
            self.context, host)

    def _check_compatible_with_source_hypervisor(self, destination):
        source_info = self._get_compute_info(self.source)
        destination_info = self._get_compute_info(destination)

        source_type = source_info.hypervisor_type
        destination_type = destination_info.hypervisor_type
        if source_type != destination_type:
            raise exception.InvalidHypervisorType()

        source_version = source_info.hypervisor_version
        destination_version = destination_info.hypervisor_version
        if source_version > destination_version:
            raise exception.DestinationHypervisorTooOld()
        return source_info, destination_info

    def _call_coldm_checks_on_host(self, destination):
        try:
            self.migrate_data = self.compute_rpcapi.\
                check_can_cold_migrate_destination(self.context, self.instance,
                    destination, self.block_migration, self.disk_over_commit)
        except messaging.MessagingTimeout:
            msg = _("Timeout while checking if we can live migrate to host: "
                    "%s") % destination
            raise exception.MigrationPreCheckError(msg)

    def _get_source_cell_mapping(self):
        """Returns the CellMapping for the cell in which the instance lives

        :returns: nova.objects.CellMapping record for the cell where
            the instance currently lives.
        :raises: MigrationPreCheckError - in case a mapping is not found
        """
        try:
            return objects.InstanceMapping.get_by_instance_uuid(
                self.context, self.instance.uuid).cell_mapping
        except exception.InstanceMappingNotFound:
            raise exception.MigrationPreCheckError(
                reason=(_('Unable to determine in which cell '
                          'instance %s lives.') % self.instance.uuid))

    def _get_destination_cell_mapping(self):
        """Returns the CellMapping for the destination host

        :returns: nova.objects.CellMapping record for the cell where
            the destination host is mapped.
        :raises: MigrationPreCheckError - in case a mapping is not found
        """
        try:
            return objects.HostMapping.get_by_host(
                self.context, self.destination).cell_mapping
        except exception.HostMappingNotFound:
            raise exception.MigrationPreCheckError(
                reason=(_('Unable to determine in which cell '
                          'destination host %s lives.') % self.destination))

    def _get_request_spec_for_select_destinations(self, attempted_hosts=None):
        """Builds a RequestSpec that can be passed to select_destinations

        Used when calling the scheduler to pick a destination host for live
        migrating the instance.

        :param attempted_hosts: List of host names to ignore in the scheduler.
            This is generally at least seeded with the source host.
        :returns: nova.objects.RequestSpec object
        """
        image = utils.get_image_from_system_metadata(
            self.instance.system_metadata)
        filter_properties = {'ignore_hosts': attempted_hosts}
        if not self.request_spec:
            # NOTE(sbauza): We were unable to find an original RequestSpec
            # object - probably because the instance is old.
            # We need to mock that the old way
            request_spec = objects.RequestSpec.from_components(
                self.context, self.instance.uuid, image,
                self.instance.flavor, self.instance.numa_topology,
                self.instance.pci_requests,
                filter_properties, None, self.instance.availability_zone
            )
        else:
            request_spec = self.request_spec
            # NOTE(sbauza): Force_hosts/nodes needs to be reset
            # if we want to make sure that the next destination
            # is not forced to be the original host
            request_spec.reset_forced_destinations()
        scheduler_utils.setup_instance_group(self.context, request_spec)

        # We currently only support live migrating to hosts in the same
        # cell that the instance lives in, so we need to tell the scheduler
        # to limit the applicable hosts based on cell.
        cell_mapping = self._get_source_cell_mapping()
        LOG.debug('Requesting cell %(cell)s while live migrating',
                  {'cell': cell_mapping.identity},
                  instance=self.instance)
        if ('requested_destination' in request_spec and
                request_spec.requested_destination):
            request_spec.requested_destination.cell = cell_mapping
        else:
            request_spec.requested_destination = objects.Destination(
                cell=cell_mapping)

        request_spec.ensure_project_id(self.instance)

        return request_spec

    def _find_destination(self):
        # TODO(johngarbutt) this retry loop should be shared
        attempted_hosts = [self.source]
        request_spec = self._get_request_spec_for_select_destinations(
            attempted_hosts)

        host = None
        while host is None:
            self._check_not_over_max_retries(attempted_hosts)
            request_spec.ignore_hosts = attempted_hosts
            try:
                selection_lists = self.scheduler_client.select_destinations(
                        self.context, request_spec, [self.instance.uuid],
                        return_objects=True, return_alternates=False)
                # We only need the first item in the first list, as there is
                # only one instance, and we don't care about any alternates.
                selection = selection_lists[0][0]
                host = selection.service_host
            except messaging.RemoteError as ex:
                # TODO(ShaoHe Feng) There maybe multi-scheduler, and the
                # scheduling algorithm is R-R, we can let other scheduler try.
                # Note(ShaoHe Feng) There are types of RemoteError, such as
                # NoSuchMethod, UnsupportedVersion, we can distinguish it by
                # ex.exc_type.
                raise exception.MigrationSchedulerRPCError(
                    reason=six.text_type(ex))
            try:
                self._check_compatible_with_source_hypervisor(host)
                self._call_coldm_checks_on_host(host)
            except (exception.Invalid, exception.MigrationPreCheckError) as e:
                LOG.debug("Skipping host: %(host)s because: %(e)s",
                    {"host": host, "e": e})
                attempted_hosts.append(host)
                # The scheduler would have created allocations against the
                # selected destination host in Placement, so we need to remove
                # those before moving on.
                self._remove_host_allocations(host, selection.nodename)
                host = None
        return selection.service_host, selection.nodename

    def _remove_host_allocations(self, host, node):
        """Removes instance allocations against the given host from Placement

        :param host: The name of the host.
        :param node: The name of the node.
        """
        # Get the compute node object since we need the UUID.
        # TODO(mriedem): If the result of select_destinations eventually
        # returns the compute node uuid, we wouldn't need to look it
        # up via host/node and we can save some time.
        try:
            compute_node = objects.ComputeNode.get_by_host_and_nodename(
                self.context, host, node)
        except exception.ComputeHostNotFound:
            # This shouldn't happen, but we're being careful.
            LOG.info('Unable to remove instance allocations from host %s '
                     'and node %s since it was not found.', host, node,
                     instance=self.instance)
            return

        # Calculate the resource class amounts to subtract from the allocations
        # on the node based on the instance flavor.
        resources = scheduler_utils.resources_from_flavor(
            self.instance, self.instance.flavor)

        # Now remove the allocations for our instance against that node.
        # Note that this does not remove allocations against any other node
        # or shared resource provider, it's just undoing what the scheduler
        # allocated for the given (destination) node.
        self.scheduler_client.reportclient.\
            remove_provider_from_instance_allocation(
                self.context, self.instance.uuid, compute_node.uuid,
                self.instance.user_id, self.instance.project_id, resources)

    def _check_not_over_max_retries(self, attempted_hosts):
        if CONF.migrate_max_retries == -1:
            return

        retries = len(attempted_hosts) - 1
        if retries > CONF.migrate_max_retries:
            if self.migration:
                self.migration.status = 'failed'
                self.migration.save()
            msg = (_('Exceeded max scheduling retries %(max_retries)d for '
                     'instance %(instance_uuid)s during live migration')
                   % {'max_retries': retries,
                      'instance_uuid': self.instance.uuid})
            raise exception.MaxRetriesExceeded(reason=msg)
