# Copyright (c) 2013 Hewlett-Packard Development Company, L.P.
# Copyright (c) 2012 VMware, Inc.
# Copyright (c) 2011 Citrix Systems, Inc.
# Copyright 2011 OpenStack Foundation
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

"""
Class for VM tasks like spawn, snapshot, suspend, resume etc.
"""

import collections
import copy
import decorator
import math
import os
import re
import six
import time

from oslo_concurrency import lockutils
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import excutils
from oslo_utils import strutils
from oslo_utils import timeutils
from oslo_utils import units
from oslo_utils import uuidutils
from oslo_vmware import exceptions as vexc
from oslo_vmware.objects import datastore as ds_obj
from oslo_vmware import vim_util as vutil

from nova.api.metadata import base as instance_metadata
from nova import compute
from nova.compute import power_state
from nova.compute import task_states
from nova.compute import utils as compute_utils
import nova.conf
from nova.console import type as ctype
from nova import context as nova_context
from nova import exception
from nova import network
from nova import objects
from nova.objects import fields
from nova import utils
from nova import version
from nova.virt import configdrive
from nova.virt import driver
from nova.virt import hardware
from nova.virt.vmwareapi import cluster_util
from nova.virt.vmwareapi import constants
from nova.virt.vmwareapi import ds_util
from nova.virt.vmwareapi import error_util
from nova.virt.vmwareapi import imagecache
from nova.virt.vmwareapi import images
from nova.virt.vmwareapi import special_spawning
from nova.virt.vmwareapi import vif as vmwarevif
from nova.virt.vmwareapi import vim_util
from nova.virt.vmwareapi import vm_util

CONF = nova.conf.CONF

LOG = logging.getLogger(__name__)

RESIZE_TOTAL_STEPS = 7


class VirtualMachineInstanceConfigInfo(object):
    """Parameters needed to create and configure a new instance."""

    def __init__(self, instance, image_info, datastore, dc_info, image_cache,
                 extra_specs=None):

        # Some methods called during spawn take the instance parameter purely
        # for logging purposes.
        # TODO(vui) Clean them up, so we no longer need to keep this variable
        self.instance = instance

        self.ii = image_info
        self.root_gb = instance.flavor.root_gb
        self.datastore = datastore
        self.dc_info = dc_info
        self._image_cache = image_cache
        self._extra_specs = extra_specs

    @property
    def cache_image_folder(self):
        if self.ii.image_id is None:
            return
        return self._image_cache.get_image_cache_folder(
                   self.datastore, self.ii.image_id)

    @property
    def cache_image_path(self):
        if self.ii.image_id is None:
            return
        cached_image_file_name = "%s.%s" % (self.ii.image_id,
                                            self.ii.file_type)
        return self.cache_image_folder.join(cached_image_file_name)


# Note(vui): See https://bugs.launchpad.net/nova/+bug/1363349
# for cases where mocking time.sleep() can have unintended effects on code
# not under test. For now, unblock the affected test cases by providing
# a wrapper function to work around needing to mock time.sleep()
def _time_sleep_wrapper(delay):
    time.sleep(delay)


@decorator.decorator
def retry_if_task_in_progress(f, *args, **kwargs):
    retries = max(CONF.vmware.api_retry_count, 1)
    delay = 1
    for attempt in range(1, retries + 1):
        if attempt != 1:
            _time_sleep_wrapper(delay)
            delay = min(2 * delay, 60)
        try:
            f(*args, **kwargs)
            return
        except vexc.TaskInProgress:
            pass


class VMwareVMOps(object):
    """Management class for VM-related tasks."""

    def __init__(self, session, virtapi, volumeops, cluster=None,
                 datastore_regex=None):
        """Initializer."""
        self.compute_api = compute.API()
        self._session = session
        self._virtapi = virtapi
        self._volumeops = volumeops
        self._cluster = cluster
        self._property_collector = None
        self._property_collector_version = ''
        self._root_resource_pool = vm_util.get_res_pool_ref(self._session,
                                                            self._cluster)
        self._datastore_regex = datastore_regex
        self._base_folder = self._get_base_folder()
        self._tmp_folder = 'vmware_temp'
        self._datastore_browser_mapping = {}
        self._imagecache = imagecache.ImageCacheManager(self._session,
                                                        self._base_folder)
        self._network_api = network.API()

        # set when our driver's init_host() is called
        self._compute_host = None

        # pre-warm the cache, so we don't have to do extra queries per instance
        self.update_vmref_cache()

    def _get_base_folder(self):
        # Enable more than one compute node to run on the same host
        if CONF.vmware.cache_prefix:
            base_folder = '%s%s' % (CONF.vmware.cache_prefix,
                                    CONF.image_cache_subdirectory_name)
        # Ensure that the base folder is unique per compute node
        elif CONF.remove_unused_base_images:
            base_folder = '%s%s' % (CONF.my_ip,
                                    CONF.image_cache_subdirectory_name)
        else:
            # Aging disable ensures backward compatibility
            base_folder = CONF.image_cache_subdirectory_name
        return base_folder

    def _extend_virtual_disk(self, instance, requested_size, name, dc_ref):
        service_content = self._session.vim.service_content
        LOG.debug("Extending root virtual disk to %s", requested_size)
        vmdk_extend_task = self._session._call_method(
                self._session.vim,
                "ExtendVirtualDisk_Task",
                service_content.virtualDiskManager,
                name=name,
                datacenter=dc_ref,
                newCapacityKb=requested_size,
                eagerZero=False)
        try:
            self._session._wait_for_task(vmdk_extend_task)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                LOG.error('Extending virtual disk failed with error: %s',
                          e)
                # Clean up files created during the extend operation
                files = [name.replace(".vmdk", "-flat.vmdk"), name]
                for file in files:
                    ds_path = ds_obj.DatastorePath.parse(file)
                    self._delete_datastore_file(ds_path, dc_ref)

        LOG.debug("Extended root virtual disk")

    def _delete_datastore_file(self, datastore_path, dc_ref):
        try:
            ds_util.file_delete(self._session, datastore_path, dc_ref)
        except (vexc.CannotDeleteFileException,
                vexc.FileFaultException,
                vexc.FileLockedException,
                vexc.FileNotFoundException):
            LOG.debug("Unable to delete %(ds)s. There may be more than "
                      "one process or thread trying to delete the file",
                      {'ds': datastore_path},
                      exc_info=True)

    def _extend_if_required(self, dc_info, image_info, instance,
                            root_vmdk_path):

        root_vmdk = ds_obj.DatastorePath.parse(root_vmdk_path.replace(
            ".vmdk", "-flat.vmdk"))

        datastore = ds_util.get_datastore(self._session,
                              dc_info.ref,
                              re.compile(r"^{}$".format(root_vmdk.datastore)))
        ds_browser = self._get_ds_browser(datastore.ref)
        actual_file_size = ds_util.file_size(self._session,
                                             ds_browser,
                                             root_vmdk.parent,
                                             root_vmdk.basename)
        if instance.flavor.root_gb * units.Gi > actual_file_size:
            size_in_kb = instance.flavor.root_gb * units.Mi
            self._extend_virtual_disk(instance, size_in_kb,
                                      root_vmdk_path, dc_info.ref)

    def _configure_config_drive(self, context, instance, vm_ref, dc_info,
                                datastore, injected_files, admin_password,
                                network_info):
        session_vim = self._session.vim
        cookies = session_vim.client.options.transport.cookiejar
        dc_path = vutil.get_inventory_path(session_vim, dc_info.ref)
        uploaded_iso_path = self._create_config_drive(context,
                                                      instance,
                                                      injected_files,
                                                      admin_password,
                                                      network_info,
                                                      datastore.name,
                                                      dc_path,
                                                      instance.uuid,
                                                      cookies)
        uploaded_iso_path = datastore.build_path(uploaded_iso_path)
        self._attach_cdrom_to_vm(
            vm_ref, instance,
            datastore.ref,
            str(uploaded_iso_path))

    def _get_instance_metadata(self, context, instance, flavor=None):
        if not flavor:
            flavor = instance.flavor
        metadata = [('name', instance.display_name),
                    ('userid', context.user_id),
                    ('username', context.user_name),
                    ('projectid', context.project_id),
                    ('projectname', context.project_name),
                    ('flavor:name', flavor.name),
                    ('flavor:memory_mb', flavor.memory_mb),
                    ('flavor:vcpus', flavor.vcpus),
                    ('flavor:ephemeral_gb', flavor.ephemeral_gb),
                    ('flavor:root_gb', flavor.root_gb),
                    ('flavor:swap', flavor.swap),
                    ('imageid', instance.image_ref),
                    ('package', version.version_string_with_package())]
        # NOTE: formatted as lines like this: 'name:NAME\nuserid:ID\n...'
        return ''.join(['%s:%s\n' % (k, v) for k, v in metadata])

    def _create_folders(self, parent_folder, folder_path):
        folders = folder_path.split('/')
        path_list = []
        for folder in folders:
            path_list.append(folder)
            folder_path = '/'.join(path_list)
            folder_ref = vm_util.folder_ref_cache_get(folder_path)
            if not folder_ref:
                folder_ref = vm_util.create_folder(self._session,
                                                   parent_folder,
                                                   folder)
                vm_util.folder_ref_cache_update(folder_path, folder_ref)
            parent_folder = folder_ref
        return folder_ref

    def _get_folder_name(self, name, id):
        # Maximum folder length must be less than 80 characters.
        # The 'id' length is 36. The maximum prefix for name is 40.
        # We cannot truncate the 'id' as this is unique across OpenStack.
        return '%s (%s)' % (name[:40], id[:36])

    def _get_project_folder_path(self, project_id, type_):
        folder_name = self._get_folder_name('Project', project_id)
        folder_path = 'OpenStack/%s/%s' % (folder_name, type_)

        return folder_path

    def _get_project_folder(self, dc_info, project_id=None, type_=None):
        folder_path = self._get_project_folder_path(project_id, type_)
        return self._create_folders(dc_info.vmFolder, folder_path)

    def _get_vm_config_spec(self, instance, image_info,
                            datastore, network_info, extra_specs,
                            metadata, vm_name=None):
        vif_infos = vmwarevif.get_vif_info(self._session,
                                           self._cluster,
                                           utils.is_neutron(),
                                           image_info.vif_model,
                                           network_info)
        LOG.debug('Instance VIF info %s', vif_infos)

        if extra_specs.storage_policy:
            profile_spec = vm_util.get_storage_profile_spec(
                self._session, extra_specs.storage_policy)
        else:
            profile_spec = None
        # Get the create vm config spec
        client_factory = self._session.vim.client.factory
        config_spec = vm_util.get_vm_create_spec(client_factory,
                                                 instance,
                                                 datastore.name,
                                                 vif_infos,
                                                 extra_specs,
                                                 image_info.os_type,
                                                 profile_spec=profile_spec,
                                                 metadata=metadata,
                                                 vm_name=vm_name)

        return config_spec

    def build_virtual_machine(self, instance, context, image_info, datastore,
                              network_info, extra_specs, metadata, vm_folder,
                              vm_name=None):
        config_spec = self._get_vm_config_spec(instance,
                                               image_info,
                                               datastore,
                                               network_info,
                                               extra_specs,
                                               metadata,
                                               vm_name=vm_name)

        # Create the VM
        vm_ref = vm_util.create_vm(self._session, instance, vm_folder,
                                   config_spec, self._root_resource_pool)

        return vm_ref

    def _get_extra_specs(self, flavor, image_meta=None):
        image_meta = image_meta or objects.ImageMeta.from_dict({})
        extra_specs = vm_util.ExtraSpecs()
        for resource in ['cpu', 'memory', 'disk_io', 'vif']:
            for (key, type) in (('limit', int),
                                ('reservation', int),
                                ('shares_level', str),
                                ('shares_share', int)):
                value = flavor.extra_specs.get('quota:' + resource + '_' + key)
                if value:
                    setattr(getattr(extra_specs, resource + '_limits'),
                            key, type(value))
        if CONF.vmware.reserve_all_memory \
                or utils.is_big_vm(int(flavor.memory_mb), flavor):
            extra_specs.memory_limits.reservation = int(flavor.memory_mb)
        else:
            try:
                memory_reserved_mb = int(flavor.extra_specs[
                    utils.MEMORY_RESERVABLE_MB_RESOURCE_SPEC_KEY])
                if memory_reserved_mb > 0:
                    extra_specs.memory_limits.reservation = min(
                        int(flavor.memory_mb), memory_reserved_mb)
            except (ValueError, KeyError):
                pass
        extra_specs.cpu_limits.validate()
        extra_specs.memory_limits.validate()
        extra_specs.disk_io_limits.validate()
        extra_specs.vif_limits.validate()
        hw_firmware_type = image_meta.properties.get('hw_firmware_type')
        if hw_firmware_type == fields.FirmwareType.UEFI:
            extra_specs.firmware = 'efi'
        elif hw_firmware_type == fields.FirmwareType.BIOS:
            extra_specs.firmware = 'bios'
        hw_version = flavor.extra_specs.get('vmware:hw_version',
                                            CONF.vmware.default_hw_version)
        hv_enabled = flavor.extra_specs.get('vmware:hv_enabled')
        extra_specs.hv_enabled = hv_enabled
        extra_specs.hw_version = hw_version
        if utils.is_big_vm(int(flavor.memory_mb), flavor):
            extra_specs.numa_prefer_ht = 'TRUE'
        else:
            # empty value should delete the option. we need that for resizes.
            extra_specs.numa_prefer_ht = ''

        video_ram = image_meta.properties.get('hw_video_ram', 0)
        max_vram = int(flavor.extra_specs.get('hw_video:ram_max_mb', 0))

        if video_ram > max_vram:
            raise exception.RequestedVRamTooHigh(req_vram=video_ram,
                                                 max_vram=max_vram)
        if video_ram and video_ram:
            extra_specs.hw_video_ram = video_ram * units.Mi / units.Ki

        storage_policy = self._get_storage_policy(flavor)
        if storage_policy:
            extra_specs.storage_policy = storage_policy
        topology = hardware.get_best_cpu_topology(flavor, image_meta,
                                                  allow_threads=False)
        extra_specs.cores_per_socket = topology.cores
        return extra_specs

    def _get_storage_policy(self, flavor):
        if CONF.vmware.pbm_enabled:
            return flavor.extra_specs.get('vmware:storage_policy',
                                          CONF.vmware.pbm_default_policy)
        return None

    def _get_esx_host_and_cookies(self, datastore, dc_path, file_path):
        hosts = datastore.get_connected_hosts(self._session)
        host = ds_obj.Datastore.choose_host(hosts)
        host_name = self._session._call_method(vutil, 'get_object_property',
                                               host, 'name')
        url = ds_obj.DatastoreURL('https', host_name, file_path, dc_path,
                                  datastore.name)
        cookie_header = url.get_transfer_ticket(self._session, 'PUT')
        name, value = cookie_header.split('=')
        # TODO(rgerganov): this is a hack to emulate cookiejar until we fix
        # oslo.vmware to accept plain http headers
        Cookie = collections.namedtuple('Cookie', ['name', 'value'])
        return host_name, [Cookie(name, value)]

    def _fetch_vsphere_image(self, context, vi, image_ds_loc):
        """Fetch image which is located on a vSphere datastore."""
        location = vi.ii.vsphere_location
        LOG.debug("Using vSphere location: %s", location)

        LOG.debug("Copying image file data %(image_id)s to "
                  "%(file_path)s on the data store "
                  "%(datastore_name)s",
                  {'image_id': vi.ii.image_id,
                   'file_path': image_ds_loc,
                   'datastore_name': vi.datastore.name})

        location_url = ds_obj.DatastoreURL.urlparse(location)
        datacenter_path = location_url.datacenter_path
        datacenter_moref = ds_util.get_datacenter_ref(
            self._session, datacenter_path)

        datastore_name = location_url.datastore_name
        src_path = ds_obj.DatastorePath(datastore_name, location_url.path)
        ds_util.file_copy(
            self._session, str(src_path), datacenter_moref,
            str(image_ds_loc), vi.dc_info.ref)

        LOG.debug("Copied image file data %(image_id)s to "
                  "%(file_path)s on the data store "
                  "%(datastore_name)s",
                  {'image_id': vi.ii.image_id,
                   'file_path': image_ds_loc,
                   'datastore_name': vi.datastore.name})

    def _fetch_image_as_file(self, context, vi, image_ds_loc):
        """Download image as an individual file to host via HTTP PUT."""
        session = self._session

        LOG.debug("Downloading image file data %(image_id)s to "
                  "%(file_path)s on the data store "
                  "%(datastore_name)s",
                  {'image_id': vi.ii.image_id,
                   'file_path': image_ds_loc,
                   'datastore_name': vi.datastore.name})

        # try to get esx cookie to upload
        try:
            dc_path = 'ha-datacenter'
            host, cookies = self._get_esx_host_and_cookies(vi.datastore,
                dc_path, image_ds_loc.rel_path)
        except Exception as e:
            LOG.warning("Get esx cookies failed: %s", e)
            dc_path = vutil.get_inventory_path(session.vim, vi.dc_info.ref)

            host = self._session._host
            cookies = session.vim.client.options.transport.cookiejar

        images.fetch_image(
            context,
            vi.instance,
            host,
            session._port,
            dc_path,
            vi.datastore.name,
            image_ds_loc.rel_path,
            cookies=cookies)

    def _get_image_template_vm_name(self, image_id, datastore_name):
        templ_vm_name = '%s (%s)' % (image_id, datastore_name)
        return templ_vm_name

    def _fetch_image_as_vapp(self, context, vi, image_ds_loc):
        """Download stream optimized image to host as a vApp."""

        vm_name = self._get_image_template_vm_name(vi.ii.image_id,
                                                   vi.datastore.name)

        LOG.debug("Downloading stream optimized image %(image_id)s to "
                  "%(vm_name)s on the data store "
                  "%(datastore_name)s as vApp",
                  {'image_id': vi.ii.image_id,
                   'vm_name': vm_name,
                   'datastore_name': vi.datastore.name})

        image_size, src_folder_ds_path = images.fetch_image_stream_optimized(
            context,
            vi.instance,
            self._session,
            vm_name,
            vi.datastore.name,
            self._get_project_folder(vi.dc_info,
                                     project_id=vi.ii.owner,
                                     type_='Images'),
            self._root_resource_pool,
            image_id=vi.ii.image_id)
        # The size of the image is different from the size of the virtual disk.
        # We want to use the latter. On vSAN this is the only way to get this
        # size because there is no VMDK descriptor.
        vi.ii.file_size = image_size
        self._cache_vm_image(vi, src_folder_ds_path)

    def _fetch_image_as_ova(self, context, vi, image_ds_loc):
        """Download root disk of an OVA image as streamOptimized."""

        vm_name = self._get_image_template_vm_name(vi.ii.image_id,
                                                   vi.datastore.name)

        image_size, src_folder_ds_path = images.fetch_image_ova(context,
                               vi.instance,
                               self._session,
                               vm_name,
                               vi.datastore.name,
                               self._get_project_folder(vi.dc_info,
                                        project_id=vi.ii.owner,
                                        type_='Images'),
                               self._root_resource_pool)

        # The size of the image is different from the size of the virtual disk.
        # We want to use the latter. On vSAN this is the only way to get this
        # size because there is no VMDK descriptor.
        vi.ii.file_size = image_size
        self._cache_vm_image(vi, src_folder_ds_path)

    def _prepare_sparse_image(self, vi):
        tmp_dir_loc = vi.datastore.build_path(
                self._tmp_folder, uuidutils.generate_uuid())
        tmp_image_ds_loc = tmp_dir_loc.join(
                vi.ii.image_id, "tmp-sparse.vmdk")
        ds_util.mkdir(self._session, tmp_image_ds_loc.parent, vi.dc_info.ref)
        return tmp_dir_loc, tmp_image_ds_loc

    def _prepare_flat_image(self, vi):
        tmp_dir_loc = vi.datastore.build_path(
                self._tmp_folder, uuidutils.generate_uuid())
        tmp_image_ds_loc = tmp_dir_loc.join(
                vi.ii.image_id, vi.cache_image_path.basename)
        ds_util.mkdir(self._session, tmp_image_ds_loc.parent, vi.dc_info.ref)
        vm_util.create_virtual_disk(
                self._session, vi.dc_info.ref,
                vi.ii.adapter_type,
                vi.ii.disk_type,
                str(tmp_image_ds_loc),
                vi.ii.file_size_in_kb)
        flat_vmdk_name = vi.cache_image_path.basename.replace('.vmdk',
                                                              '-flat.vmdk')
        flat_vmdk_ds_loc = tmp_dir_loc.join(vi.ii.image_id, flat_vmdk_name)
        self._delete_datastore_file(str(flat_vmdk_ds_loc), vi.dc_info.ref)
        return tmp_dir_loc, flat_vmdk_ds_loc

    def _prepare_stream_optimized_image(self, vi):
        vm_name = "%s_%s" % (constants.IMAGE_VM_PREFIX,
                             uuidutils.generate_uuid())
        tmp_dir_loc = vi.datastore.build_path(vm_name)
        tmp_image_ds_loc = tmp_dir_loc.join("%s.vmdk" % tmp_dir_loc.basename)
        return tmp_dir_loc, tmp_image_ds_loc

    def _prepare_iso_image(self, vi):
        tmp_dir_loc = vi.datastore.build_path(
                self._tmp_folder, uuidutils.generate_uuid())
        tmp_image_ds_loc = tmp_dir_loc.join(
                vi.ii.image_id, vi.cache_image_path.basename)
        return tmp_dir_loc, tmp_image_ds_loc

    def _move_to_cache(self, dc_ref, src_folder_ds_path, dst_folder_ds_path):
        try:
            ds_util.file_move(self._session, dc_ref,
                              src_folder_ds_path, dst_folder_ds_path)
        except vexc.FileAlreadyExistsException:
            # Folder move has failed. This may be due to the fact that a
            # process or thread has already completed the operation.
            # Since image caching is synchronized, this can only happen
            # due to action external to the process.
            # In the event of a FileAlreadyExists we continue,
            # all other exceptions will be raised.
            LOG.warning("Destination %s already exists! Concurrent moves "
                        "can lead to unexpected results.",
                        dst_folder_ds_path)

    def _cache_sparse_image(self, vi, tmp_image_ds_loc):
        tmp_dir_loc = tmp_image_ds_loc.parent.parent
        converted_image_ds_loc = tmp_dir_loc.join(
                vi.ii.image_id, vi.cache_image_path.basename)
        # converts fetched image to preallocated disk
        vm_util.copy_virtual_disk(
                self._session,
                vi.dc_info.ref,
                str(tmp_image_ds_loc),
                str(converted_image_ds_loc))

        self._delete_datastore_file(str(tmp_image_ds_loc), vi.dc_info.ref)

        self._move_to_cache(vi.dc_info.ref,
                            tmp_image_ds_loc.parent,
                            vi.cache_image_folder)

    def _cache_flat_image(self, vi, tmp_image_ds_loc):
        self._move_to_cache(vi.dc_info.ref,
                            tmp_image_ds_loc.parent,
                            vi.cache_image_folder)

    def _cache_vm_image(self, vi, tmp_image_ds_loc):
        dst_path = vi.cache_image_folder.join("%s.vmdk" % vi.ii.image_id)
        try:
            ds_util.mkdir(self._session, vi.cache_image_folder, vi.dc_info.ref)
        except vexc.FileAlreadyExistsException:
            pass
        try:
            ds_util.disk_copy(self._session, vi.dc_info.ref,
                              tmp_image_ds_loc, dst_path)
        except vexc.FileAlreadyExistsException:
            pass

    def _unregister_template_vm(self, templ_vm_ref, instance=None):
        """Unregister a template VM

        We need to Unregister instead of Destroy, if the datastore path does
        not exist anymore.
        """
        try:
            LOG.debug("Unregistering the template VM %s",
                      templ_vm_ref.value)
            self._session._call_method(self._session.vim,
                                       "UnregisterVM", templ_vm_ref)
            LOG.debug("Unregistered the template VM")
        except Exception as excep:
            LOG.warning("got this exception while un-registering a ",
                        "template VM: %s",
                        excep)

    def _cache_vm_image_from_template(self, vi, templ_vm_ref):
        LOG.debug("Caching VDMK from template VM")
        vm_name = self._get_image_template_vm_name(vi.ii.image_id,
                                                   vi.datastore.name)
        vmdk = vm_util.get_vmdk_info(self._session, templ_vm_ref, vm_name)
        # The size of the image is different from the size of the virtual disk.
        # We want to use the latter. On vSAN this is the only way to get this
        # size because there is no VMDK descriptor.
        vi.ii.file_size = vmdk.capacity_in_bytes
        try:
            self._cache_vm_image(vi, vmdk.path)
        except vexc.FileNotFoundException:
            LOG.warning("Could not find files for template VM %s",
                        templ_vm_ref.value)
            self._unregister_template_vm(templ_vm_ref, vi.instance)
            return False

        LOG.debug("Cached VDMK from template VM")
        return True

    def _cache_stream_optimized_image(self, vi, tmp_image_ds_loc):
        dst_path = vi.cache_image_folder.join("%s.vmdk" % vi.ii.image_id)
        ds_util.mkdir(self._session, vi.cache_image_folder, vi.dc_info.ref)
        try:
            ds_util.disk_move(self._session, vi.dc_info.ref,
                              tmp_image_ds_loc, dst_path)
        except vexc.FileAlreadyExistsException:
            pass

    def _cache_iso_image(self, vi, tmp_image_ds_loc):
        self._move_to_cache(vi.dc_info.ref,
                            tmp_image_ds_loc.parent,
                            vi.cache_image_folder)

    def _get_vm_config_info(self, instance, image_info, extra_specs):
        """Captures all relevant information from the spawn parameters."""

        boot_from_volume = compute_utils.is_volume_backed_instance(
                                                instance._context, instance)
        if (instance.flavor.root_gb != 0 and
                not boot_from_volume and
                image_info.file_size > instance.flavor.root_gb * units.Gi):
            reason = _("Image disk size greater than requested disk size")
            raise exception.InstanceUnacceptable(instance_id=instance.uuid,
                                                 reason=reason)
        allowed_ds_types = ds_util.get_allowed_datastore_types(
            image_info.disk_type)
        datastore = ds_util.get_datastore(self._session,
                                          self._cluster,
                                          self._datastore_regex,
                                          extra_specs.storage_policy,
                                          allowed_ds_types)
        dc_info = self.get_datacenter_ref_and_name(datastore.ref)

        return VirtualMachineInstanceConfigInfo(instance,
                                                image_info,
                                                datastore,
                                                dc_info,
                                                self._imagecache,
                                                extra_specs)

    def _get_image_callbacks(self, vi):
        disk_type = vi.ii.disk_type

        if vi.ii.is_ova:
            image_fetch = self._fetch_image_as_ova
        elif disk_type == constants.DISK_TYPE_STREAM_OPTIMIZED:
            image_fetch = self._fetch_image_as_vapp
        elif vi.ii.vsphere_location:
            image_fetch = self._fetch_vsphere_image
        else:
            image_fetch = self._fetch_image_as_file

        if vi.ii.is_ova or disk_type == constants.DISK_TYPE_STREAM_OPTIMIZED:
            image_prepare = lambda vi: (None, None)
            image_cache = lambda vi, image_loc: None
        elif vi.ii.is_iso:
            image_prepare = self._prepare_iso_image
            image_cache = self._cache_iso_image
        elif disk_type == constants.DISK_TYPE_SPARSE:
            image_prepare = self._prepare_sparse_image
            image_cache = self._cache_sparse_image
        elif disk_type == constants.DISK_TYPE_STREAM_OPTIMIZED:
            image_prepare = self._prepare_stream_optimized_image
            image_cache = self._cache_stream_optimized_image
        elif disk_type in constants.SUPPORTED_FLAT_VARIANTS:
            image_prepare = self._prepare_flat_image
            image_cache = self._cache_flat_image
        else:
            reason = _("disk type '%s' not supported") % disk_type
            raise exception.InvalidDiskInfo(reason=reason)
        return image_prepare, image_fetch, image_cache

    def _fetch_image_from_other_datastores(self, vi):
        dc_all_datastores = ds_util.get_available_datastores(self._session,
                                                    dc_ref=vi.dc_info.ref)
        dc_other_datastores = [ds for ds in dc_all_datastores if dict(
                                    ds.ref) != dict(vi.datastore.ref)]

        client_factory = self._session.vim.client.factory
        tmp_vi = copy.copy(vi)
        for ds in dc_other_datastores:
            tmp_vi.datastore = ds
            other_templ_vm_ref = self._find_image_template_vm(tmp_vi)
            if other_templ_vm_ref:
                rel_spec = vm_util.relocate_vm_spec(client_factory,
                        res_pool=self._root_resource_pool,
                        disk_move_type="moveAllDiskBackingsAndDisallowSharing",
                        datastore=vi.datastore.ref)
                clone_spec = vm_util.clone_vm_spec(client_factory,
                                                   rel_spec,
                                                   template=True)
                templ_vm_clone_task = self._session._call_method(
                    self._session.vim,
                    "CloneVM_Task",
                    other_templ_vm_ref,
                    folder=self._get_project_folder(vi.dc_info,
                                        project_id=vi.ii.owner,
                                        type_='Images'),
                    name=self._get_image_template_vm_name(vi.ii.image_id,
                                                          vi.datastore.name),
                    spec=clone_spec)
                try:
                    task_info = \
                        self._session._wait_for_task(templ_vm_clone_task)
                except vexc.FileNotFoundException:
                    LOG.warning("Could not find files for template VM %s",
                                other_templ_vm_ref.value)
                    continue
                except vexc.VimFaultException as e:
                    if 'VirtualHardwareVersionNotSupported' in e.fault_list:
                        LOG.debug('Could not clone image-template from '
                                  'incompatible hardware platform')
                    else:
                        LOG.warning('Could not clone image-template from '
                                    'other datastore.')
                    continue

                templ_vm_ref = task_info.result
                return templ_vm_ref

    def _fetch_image_if_missing(self, context, vi):
        image_prepare, image_fetch, image_cache = self._get_image_callbacks(vi)
        LOG.debug("Processing image %s", vi.ii.image_id)

        with lockutils.lock(str(vi.cache_image_path),
                            lock_file_prefix='nova-vmware-fetch_image'):
            self.check_cache_folder(vi.datastore.name, vi.datastore.ref)
            ds_browser = self._get_ds_browser(vi.datastore.ref)

            image_available = False
            if not CONF.vmware.image_as_template:
                # normal deployments can work with a VMDK only
                image_available = ds_util.file_exists(
                    self._session,
                    ds_browser,
                    vi.cache_image_folder,
                    vi.cache_image_path.basename)

            if not image_available:
                # no matter if image_as_template is set, we can use the
                # template_vm in any case
                LOG.debug("Trying to find template VM")
                templ_vm_ref = self._find_image_template_vm(vi)
                image_available = (templ_vm_ref is not None)
                if image_available and not CONF.vmware.image_as_template:
                    image_available = \
                        self._cache_vm_image_from_template(vi, templ_vm_ref)

            if (not image_available and
                    CONF.vmware.fetch_image_from_other_datastores):
                # fetching from another DS is still faster
                LOG.debug("Trying to find template VM on other DS")
                templ_vm_ref = self._fetch_image_from_other_datastores(vi)
                image_available = (templ_vm_ref is not None)
                if image_available and not CONF.vmware.image_as_template:
                    image_available = \
                        self._cache_vm_image_from_template(vi, templ_vm_ref)

            if not image_available:
                # we didn't find it anywhere. upload it
                LOG.debug("Preparing fetch location")
                tmp_dir_loc, tmp_image_ds_loc = image_prepare(vi)
                LOG.debug("Fetch image to %s", tmp_image_ds_loc)
                image_fetch(context, vi, tmp_image_ds_loc)
                LOG.debug("Caching image")
                image_cache(vi, tmp_image_ds_loc)
                LOG.debug("Cleaning up location %s", str(tmp_dir_loc))
                if tmp_dir_loc:
                    self._delete_datastore_file(str(tmp_dir_loc),
                                                vi.dc_info.ref)

            # The size of the sparse image is different from the size of the
            # virtual disk. We want to use the latter.
            if vi.ii.disk_type == constants.DISK_TYPE_SPARSE:
                self._update_image_size(vi)

    def _create_and_attach_thin_disk(self, instance, vm_ref, dc_info, size,
                                     adapter_type, path):
        disk_type = constants.DISK_TYPE_THIN
        vm_util.create_virtual_disk(
                self._session, dc_info.ref,
                adapter_type,
                disk_type,
                path,
                size)

        self._volumeops.attach_disk_to_vm(
                vm_ref, instance,
                adapter_type, disk_type,
                path, size, False)

    def _create_ephemeral(self, bdi, instance, vm_ref, dc_info,
                          datastore, folder, adapter_type):
        ephemerals = None
        if bdi is not None:
            ephemerals = driver.block_device_info_get_ephemerals(bdi)
            for idx, eph in enumerate(ephemerals):
                size = eph['size'] * units.Mi
                at = eph.get('disk_bus') or adapter_type
                filename = vm_util.get_ephemeral_name(idx)
                path = str(ds_obj.DatastorePath(datastore.name, folder,
                                                filename))
                self._create_and_attach_thin_disk(instance, vm_ref, dc_info,
                                                  size, at, path)

        # There may be block devices defined but no ephemerals. In this case
        # we need to allocate an ephemeral disk if required
        if not ephemerals and instance.flavor.ephemeral_gb:
            size = instance.flavor.ephemeral_gb * units.Mi
            filename = vm_util.get_ephemeral_name(0)
            path = str(ds_obj.DatastorePath(datastore.name, folder,
                                             filename))
            self._create_and_attach_thin_disk(instance, vm_ref, dc_info, size,
                                              adapter_type, path)

    def _create_swap(self, bdi, instance, vm_ref, dc_info, datastore,
                     folder, adapter_type):
        swap = None
        filename = "swap.vmdk"
        path = str(ds_obj.DatastorePath(datastore.name, folder, filename))
        if bdi is not None:
            swap = driver.block_device_info_get_swap(bdi)
            if driver.swap_is_usable(swap):
                size = swap['swap_size'] * units.Ki
                self._create_and_attach_thin_disk(instance, vm_ref, dc_info,
                                                  size, adapter_type, path)
            else:
                # driver.block_device_info_get_swap returns
                # {'device_name': None, 'swap_size': 0} if swap is None
                # in block_device_info.  If block_device_info does not contain
                # a swap device, we need to reset swap to None, so we can
                # extract the swap_size from the instance's flavor.
                swap = None

        size = instance.flavor.swap * units.Ki
        if not swap and size > 0:
            self._create_and_attach_thin_disk(instance, vm_ref, dc_info, size,
                                              adapter_type, path)

    def _update_vnic_index(self, context, instance, network_info):
        if network_info:
            for index, vif in enumerate(network_info):
                self._network_api.update_instance_vnic_index(
                    context, instance, vif, index)

    def _update_image_size(self, vi):
        """Updates the file size of the specified image."""
        # The size of the Glance image is different from the deployed VMDK
        # size for sparse, streamOptimized and OVA images. We need to retrieve
        # the size of the flat VMDK and update the file_size property of the
        # image. This ensures that further operations involving size checks
        # and disk resizing will work as expected.
        ds_browser = self._get_ds_browser(vi.datastore.ref)
        flat_file = "%s-flat.vmdk" % vi.ii.image_id
        new_size = ds_util.file_size(self._session, ds_browser,
                                     vi.cache_image_folder, flat_file)
        if new_size is not None:
            vi.ii.file_size = new_size

    def _create_image_template(self, context, vi, extra_specs):
        metadata = self._get_instance_metadata(context, vi.instance)

        max_attempts = 2
        for i in six.moves.range(max_attempts):
            try:
                vm_folder = self._get_project_folder(vi.dc_info,
                                                     project_id=vi.ii.owner,
                                                     type_='Images')

                templ_vm_ref = self.build_virtual_machine(vi.instance,
                                    context,
                                    vi.ii,
                                    vi.datastore,
                                    None,
                                    extra_specs,
                                    metadata,
                                    vm_folder,
                                    vm_name=self._get_image_template_vm_name(
                                        vi.ii.image_id, vi.datastore.name))

                self._imagecache.enlist_image(
                        vi.ii.image_id, vi.datastore, vi.dc_info.ref)
                self._fetch_image_if_missing(context, vi)

                if vi.ii.is_iso:
                    self._use_iso_image(templ_vm_ref, vi)
                elif vi.ii.linked_clone:
                    self._use_disk_image_as_linked_clone(templ_vm_ref, vi)
                else:
                    self._use_disk_image_as_full_clone(templ_vm_ref, vi)

                vm_util.mark_vm_as_template(self._session,
                                            vi.instance,
                                            templ_vm_ref)

                return templ_vm_ref
            except Exception as create_templ_exc:
                is_last_attempt = (i == max_attempts - 1)
                with excutils.save_and_reraise_exception(
                        reraise=is_last_attempt):
                    LOG.error('Creating VM template for image failed'
                              ' with error: %s',
                              create_templ_exc)
                    try:
                        vm_util.destroy_vm(self._session, vi.instance)
                    except Exception as destroy_templ_exc:
                        LOG.error('Cleaning up VM template for'
                                  ' image failed with error: %s',
                                  destroy_templ_exc)

    def _build_template_vm_inventory_path(self, vi):
        vm_folder_name = self._session._call_method(vutil,
                                                    "get_object_property",
                                                    vi.dc_info.vmFolder,
                                                    "name")
        images_folder_path = self._get_project_folder_path(
                                        vi.ii.owner, 'Images')
        templ_vm_name = self._get_image_template_vm_name(vi.ii.image_id,
                                                         vi.datastore.name)
        templ_vm_inventory_path = '%s/%s/%s/%s' % (vi.dc_info.name,
                                                   vm_folder_name,
                                                   images_folder_path,
                                                   templ_vm_name)
        return templ_vm_inventory_path

    def _find_image_template_vm(self, vi):
        templ_vm_inventory_path = self._build_template_vm_inventory_path(vi)
        templ_vm_ref = vm_util.find_by_inventory_path(self._session,
                                                    templ_vm_inventory_path)

        return templ_vm_ref

    def _get_vm_template_for_image(self, context,
                                   instance,
                                   image_info,
                                   extra_specs):
        templ_instance = copy.deepcopy(instance)
        # Use image UUID instead of instance UUID for creating the VM template.
        templ_instance.uuid = templ_instance.image_ref

        with lockutils.lock(templ_instance.uuid,
                            lock_file_prefix='nova-vmware-image-template'):

            # Create template with the smallest root disk size that
            #  can hold the image so that it can be extended during
            #  instantiation (after clone) to match the flavor
            templ_instance.flavor.root_gb = int(math.ceil(
                float(
                    image_info.file_size) / units.Gi /
                    templ_instance.flavor.root_gb))

            vi = self._get_vm_config_info(templ_instance,
                                          image_info,
                                          extra_specs)

            self._imagecache.enlist_image(
                    vi.ii.image_id, vi.datastore, vi.dc_info.ref)
            self._fetch_image_if_missing(context, vi)

            templ_vm_ref = self._find_image_template_vm(vi)

            if not templ_vm_ref and\
                    CONF.vmware.fetch_image_from_other_datastores:
                templ_vm_ref = self._fetch_image_from_other_datastores(vi)

            if not templ_vm_ref:
                templ_vm_ref = self._create_image_template(context,
                                                           vi,
                                                           extra_specs)

            return templ_vm_ref

    def _create_instance_from_image_template(self, context,
                                             client_factory,
                                             templ_vm_ref, vi,
                                             extra_specs,
                                             network_info):
        rel_spec = vm_util.relocate_vm_spec(client_factory,
                        res_pool=self._root_resource_pool,
                        disk_move_type="moveAllDiskBackingsAndDisallowSharing")
        clone_spec = vm_util.clone_vm_spec(client_factory, rel_spec)
        vm_clone_task = self._session._call_method(
            self._session.vim,
            "CloneVM_Task",
            templ_vm_ref,
            folder=self._get_project_folder(vi.dc_info,
                                    project_id=vi.instance.project_id,
                                    type_='Instances'),
            name=vi.instance.uuid,
            spec=clone_spec)
        task_info = self._session._wait_for_task(vm_clone_task)
        vm_ref = task_info.result

        root_vmdk_info = vm_util.get_vmdk_info(self._session,
                                               vm_ref,
                                               uuid=vi.instance.uuid)
        self._extend_if_required(vi.dc_info,
                                 vi.ii,
                                 vi.instance,
                                 root_vmdk_info.path)

        metadata = self._get_instance_metadata(context, vi.instance)
        reconfig_spec = vm_util.get_vm_resize_spec(client_factory,
                                                   int(vi.instance.vcpus),
                                                   int(vi.instance.memory_mb),
                                                   extra_specs,
                                                   metadata=metadata)
        reconfig_spec.instanceUuid = vi.instance.uuid
        vif_infos = vmwarevif.get_vif_info(self._session,
                                           self._cluster,
                                           utils.is_neutron(),
                                           vi.ii.vif_model,
                                           network_info)
        vm_util.append_vif_infos_to_config_spec(client_factory,
                                                reconfig_spec,
                                                vif_infos,
                                                extra_specs.vif_limits)

        self._append_serial_port_replacement_to_reconfig_spec(client_factory,
                                                              vm_ref,
                                                              reconfig_spec)

        vm_util.reconfigure_vm(self._session, vm_ref, reconfig_spec)

        return vm_ref

    def _append_serial_port_replacement_to_reconfig_spec(self,
                                                         client_factory,
                                                         vm_ref,
                                                         reconfig_spec):
        if not reconfig_spec.deviceChange:
            reconfig_spec.deviceChange = []

        for device in vm_util.get_hardware_devices(self._session, vm_ref):
            if vim_util.is_vim_instance(device, "VirtualSerialPort"):
                removal = client_factory.create('ns0:VirtualDeviceConfigSpec')
                removal.device = device
                removal.operation = 'remove'
                reconfig_spec.deviceChange.append(removal)

        serial_port_spec = vm_util.create_serial_port_spec(client_factory)
        if serial_port_spec:
            reconfig_spec.deviceChange.append(serial_port_spec)

    def update_cluster_placement(self, context, instance, remove=False):
        self.sync_instance_server_group(context, instance)
        self.update_admin_vm_group_membership(instance, remove=remove)

    def sync_instance_server_group(self, context, instance):
        try:
            instance_group_object = objects.instance_group.InstanceGroup
            server_group = instance_group_object.get_by_instance_uuid(
                context, instance.uuid)
            self.sync_server_group(context, server_group.uuid)
        except nova.exception.InstanceGroupNotFound:
            pass

    @staticmethod
    def _get_admin_group_name_for_instance(instance):
        vm_group_name = CONF.vmware.special_spawning_vm_group
        if not vm_group_name:
            return None

        needs_empty_host = utils.vm_needs_special_spawning(
            int(instance.memory_mb), instance.flavor)
        if needs_empty_host:
            return None

        return vm_group_name

    def update_admin_vm_group_membership(self, instance, remove=False):
        vm_group_name = self._get_admin_group_name_for_instance(instance)
        if not vm_group_name:
            return
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        cluster_util.update_vm_group_membership(self._session, self._cluster,
                                                vm_group_name, vm_ref,
                                                remove=remove)

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info, block_device_info=None):

        client_factory = self._session.vim.client.factory
        image_info = images.VMwareImage.from_image(context,
                                                   instance.image_ref,
                                                   image_meta)

        extra_specs = self._get_extra_specs(instance.flavor, image_meta)

        vi = self._get_vm_config_info(instance, image_info,
                                      extra_specs)

        boot_from_volume = compute_utils.is_volume_backed_instance(
                                                instance._context, instance)

        if CONF.vmware.image_as_template and instance.image_ref \
                and not boot_from_volume:
            templ_vm_ref = self._get_vm_template_for_image(context,
                                                           instance,
                                                           image_info,
                                                           extra_specs)
            vm_ref = self._create_instance_from_image_template(context,
                                                           client_factory,
                                                           templ_vm_ref,
                                                           vi,
                                                           extra_specs,
                                                           network_info)
        else:
            metadata = self._get_instance_metadata(context, instance)
            vm_folder = self._get_project_folder(
                vi.dc_info, project_id=instance.project_id, type_='Instances')
            vm_ref = self.build_virtual_machine(instance,
                                                context,
                                                image_info,
                                                vi.datastore,
                                                network_info,
                                                extra_specs,
                                                metadata,
                                                vm_folder)

        # Cache the vm_ref. This saves a remote call to the VC. This uses the
        # instance uuid.
        vm_util.vm_ref_cache_update(instance.uuid, vm_ref)

        # Update all DRS related rules
        self.update_cluster_placement(context, instance)

        # Update the Neutron VNIC index
        self._update_vnic_index(context, instance, network_info)

        # Set the machine.id parameter of the instance to inject
        # the NIC configuration inside the VM
        if CONF.flat_injected:
            self._set_machine_id(client_factory, instance, network_info,
                                vm_ref=vm_ref)

        # Set the vnc configuration of the instance, vnc port starts from 5900
        if CONF.vnc.enabled:
            self._get_and_set_vnc_config(client_factory, instance, vm_ref)

        block_device_mapping = []
        if block_device_info is not None:
            block_device_mapping = driver.block_device_info_get_mapping(
                block_device_info)

        if instance.image_ref and not CONF.vmware.image_as_template \
                and not boot_from_volume:
            self._imagecache.enlist_image(
                    image_info.image_id, vi.datastore, vi.dc_info.ref)
            self._fetch_image_if_missing(context, vi)

            if image_info.is_iso:
                self._use_iso_image(vm_ref, vi)
            elif image_info.linked_clone:
                self._use_disk_image_as_linked_clone(vm_ref, vi)
            else:
                self._use_disk_image_as_full_clone(vm_ref, vi)

        if block_device_mapping:
            msg = "Block device information present: %s" % block_device_info
            # NOTE(mriedem): block_device_info can contain an auth_password
            # so we have to scrub the message before logging it.
            LOG.debug(strutils.mask_password(msg))

            # Before attempting to attach any volume, make sure the
            # block_device_mapping (i.e. disk_bus) is valid
            self._is_bdm_valid(block_device_mapping)

            for disk in block_device_mapping:
                connection_info = disk['connection_info']
                adapter_type = disk.get('disk_bus') or vi.ii.adapter_type

                # TODO(hartsocks): instance is unnecessary, remove it
                # we still use instance in many locations for no other purpose
                # than logging, can we simplify this?
                if disk.get('boot_index') == 0:
                    self._volumeops.attach_root_volume(connection_info,
                        instance, vi.datastore.ref, adapter_type)
                else:
                    self._volumeops.attach_volume(connection_info,
                        instance, adapter_type)

        # Create ephemeral disks
        self._create_ephemeral(block_device_info, instance, vm_ref,
                               vi.dc_info, vi.datastore, instance.uuid,
                               vi.ii.adapter_type)
        self._create_swap(block_device_info, instance, vm_ref, vi.dc_info,
                          vi.datastore, instance.uuid, vi.ii.adapter_type)

        if configdrive.required_by(instance):
            self._configure_config_drive(
                    context, instance, vm_ref, vi.dc_info, vi.datastore,
                    injected_files, admin_password, network_info)

        # Rename the VM. This is done after the spec is created to ensure
        # that all of the files for the instance are under the directory
        # 'uuid' of the instance
        vm_util.rename_vm(self._session, vm_ref, instance)

        # Make sure we don't automatically move around "big" VMs
        self.disable_drs_if_needed(instance)

        vm_util.power_on_instance(self._session, instance, vm_ref=vm_ref)

        self._clean_up_after_special_spawning(context, instance.memory_mb,
                                              instance.flavor)

    def disable_drs_if_needed(self, instance):
        if utils.is_big_vm(int(instance.memory_mb), instance.flavor) or \
                utils.is_large_vm(int(instance.memory_mb), instance.flavor):
            behavior = constants.DRS_BEHAVIOR_PARTIALLY_AUTOMATED
            LOG.debug("Adding DRS override '%s' for big VM.", behavior)
            vm_ref = vm_util.get_vm_ref(self._session, instance)
            cluster_util.update_cluster_drs_vm_override(self._session,
                                                        self._cluster,
                                                        vm_ref,
                                                        operation='add',
                                                        behavior=behavior)

    def _clean_up_after_special_spawning(self, context, instance_memory_mb,
                                         instance_flavor):
        if utils.vm_needs_special_spawning(int(instance_memory_mb),
                                           instance_flavor):
            # we're using a child resource provider, so we don't have to change
            # the drivers' report-code to keep the CUSTOM_BIGVM resource, but
            # instead can independently add it or remove it on a cluster
            placement_client = self._virtapi._compute.reportclient
            cn = self._virtapi._compute._get_compute_info(context, CONF.host)
            parent_rp_uuid = cn.uuid
            parent_tree = placement_client.get_provider_tree_and_ensure_root(
                                                    context, parent_rp_uuid)
            rp_name = '{}-{}'.format(CONF.bigvm_deployment_rp_name_prefix,
                                     cn.host)
            try:
                rp = parent_tree.data(rp_name)
            except ValueError:
                LOG.warning('Could not find resource-provider %(rp)s for '
                            'reserving resources after (re)starting a big VM.',
                            {'rp': rp_name})
            else:
                # reserve the bigvm resource. this prohibits any further
                # deployment needing a free host on that compute-node.
                inv_data = rp.inventory
                inv_data[special_spawning.BIGVM_RESOURCE]['reserved'] = 1
                placement_client.set_inventory_for_provider(context,
                                        rp.uuid, rp_name, inv_data,
                                        parent_provider_uuid=parent_rp_uuid)

    def _is_bdm_valid(self, block_device_mapping):
        """Checks if the block device mapping is valid."""
        valid_bus = (constants.DEFAULT_ADAPTER_TYPE,
                     constants.ADAPTER_TYPE_BUSLOGIC,
                     constants.ADAPTER_TYPE_IDE,
                     constants.ADAPTER_TYPE_LSILOGICSAS,
                     constants.ADAPTER_TYPE_PARAVIRTUAL)

        for disk in block_device_mapping:
            adapter_type = disk.get('disk_bus')
            if (adapter_type is not None and adapter_type not in valid_bus):
                raise exception.UnsupportedHardware(model=adapter_type,
                                                    virt="vmware")

    def _create_config_drive(self, context, instance, injected_files,
                             admin_password, network_info, data_store_name,
                             dc_name, upload_folder, cookies):
        if CONF.config_drive_format != 'iso9660':
            reason = (_('Invalid config_drive_format "%s"') %
                      CONF.config_drive_format)
            raise exception.InstancePowerOnFailure(reason=reason)

        LOG.info('Using config drive for instance')
        extra_md = {}
        if admin_password:
            extra_md['admin_pass'] = admin_password

        inst_md = instance_metadata.InstanceMetadata(instance,
                                                     content=injected_files,
                                                     extra_md=extra_md,
                                                     network_info=network_info,
                                                     request_context=context)
        try:
            with configdrive.ConfigDriveBuilder(instance_md=inst_md) as cdb:
                with utils.tempdir() as tmp_path:
                    tmp_file = os.path.join(tmp_path, 'configdrive.iso')
                    cdb.make_drive(tmp_file)
                    upload_iso_path = "%s/configdrive.iso" % (
                        upload_folder)
                    images.upload_iso_to_datastore(
                        tmp_file, instance,
                        host=self._session._host,
                        port=self._session._port,
                        data_center_name=dc_name,
                        datastore_name=data_store_name,
                        cookies=cookies,
                        file_path=upload_iso_path)
                    return upload_iso_path
        except Exception as e:
            with excutils.save_and_reraise_exception():
                LOG.error('Creating config drive failed with error: %s',
                          e)

    def _attach_cdrom_to_vm(self, vm_ref, instance,
                            datastore, file_path):
        """Attach cdrom to VM by reconfiguration."""
        client_factory = self._session.vim.client.factory
        devices = vm_util.get_hardware_devices(self._session, vm_ref)
        (controller_key, unit_number,
         controller_spec) = vm_util.allocate_controller_key_and_unit_number(
                                                    client_factory,
                                                    devices,
                                                    constants.ADAPTER_TYPE_IDE)
        cdrom_attach_config_spec = vm_util.get_cdrom_attach_config_spec(
                                    client_factory, datastore, file_path,
                                    controller_key, unit_number)
        if controller_spec:
            cdrom_attach_config_spec.deviceChange.append(controller_spec)

        LOG.debug("Reconfiguring VM instance to attach cdrom %s",
                  file_path)
        vm_util.reconfigure_vm(self._session, vm_ref, cdrom_attach_config_spec)
        LOG.debug("Reconfigured VM instance to attach cdrom %s",
                  file_path)

    def _create_vm_snapshot(self, instance, vm_ref, image_id=None):
        LOG.debug("Creating Snapshot of the VM instance")
        snapshot_task = self._session._call_method(
                    self._session.vim,
                    "CreateSnapshot_Task", vm_ref,
                    name="%s-snapshot" % (image_id or instance.uuid),
                    description="Taking Snapshot of the VM",
                    memory=False,
                    quiesce=True)
        self._session._wait_for_task(snapshot_task)
        LOG.debug("Created Snapshot of the VM instance")
        task_info = self._session._call_method(vutil,
                                               "get_object_property",
                                               snapshot_task,
                                               "info")
        snapshot = task_info.result
        return snapshot

    @retry_if_task_in_progress
    def _delete_vm_snapshot(self, instance, vm_ref, snapshot):
        LOG.debug("Deleting Snapshot of the VM instance")
        delete_snapshot_task = self._session._call_method(
                    self._session.vim,
                    "RemoveSnapshot_Task", snapshot,
                    removeChildren=False, consolidate=True)
        self._session._wait_for_task(delete_snapshot_task)
        LOG.debug("Deleted Snapshot of the VM instance")

    def _create_linked_clone_from_snapshot(self, instance,
                                           vm_ref, snapshot_ref, dc_info):
        """Create linked clone VM to be deployed to same ds as source VM
        """
        client_factory = self._session.vim.client.factory
        rel_spec = vm_util.relocate_vm_spec(
                client_factory,
                datastore=None,
                host=None,
                disk_move_type="createNewChildDiskBacking")
        clone_spec = vm_util.clone_vm_spec(client_factory, rel_spec,
                power_on=False, snapshot=snapshot_ref, template=True)
        vm_name = "%s_%s" % (constants.SNAPSHOT_VM_PREFIX,
                             uuidutils.generate_uuid())

        LOG.debug("Creating linked-clone VM from snapshot")
        vm_clone_task = self._session._call_method(
                                self._session.vim,
                                "CloneVM_Task",
                                vm_ref,
                                folder=dc_info.vmFolder,
                                name=vm_name,
                                spec=clone_spec)
        self._session._wait_for_task(vm_clone_task)
        LOG.info("Created linked-clone VM from snapshot")
        task_info = self._session._call_method(vutil,
                                               "get_object_property",
                                               vm_clone_task,
                                               "info")
        return task_info.result

    def _create_vm_clone(self, instance, vm_ref, snapshot_ref, dc_info,
                         disk_move_type=None, image_id=None, disks=None,
                         volume_mapping=None):
        """Clone VM to be deployed to same ds as source VM
        """
        volume_mapping = volume_mapping or {}

        image_id = image_id or uuidutils.generate_uuid()

        if disks:
            datastore = disks[0].device.backing.datastore
        else:
            if disk_move_type == "createNewChildDiskBacking":
                datastore = None
            else:
                datastore = ds_util.get_datastore(self._session, self._cluster,
                                                  self._datastore_regex)

        vm_name = "%s_%s" % (constants.SNAPSHOT_VM_PREFIX,
                             image_id)
        client_factory = self._session.vim.client.factory
        rel_spec = vm_util.relocate_vm_spec(
            client_factory,
            datastore=datastore,
            host=None,
            disk_move_type=disk_move_type)
        config_spec = client_factory.create('ns0:VirtualMachineConfigSpec')
        config_spec.name = vm_name
        config_spec.annotation = "Created from %s" % (instance.uuid)
        config_spec.numCPUs = 1
        config_spec.numCoresPerSocket = 1
        config_spec.memoryMB = 16
        config_spec.uuid = image_id  # Not instanceUuid,
        # as we need to import the same image in different datastores

        if disks:
            disk_devices = [vmdk_info.device.key for vmdk_info in disks]
            device_change = []
            disk_locators = []
            for device in vm_util.get_hardware_devices(self._session, vm_ref):
                device_is_disk = device.__class__.__name__ == "VirtualDisk"
                if getattr(device, 'macAddress', None) or \
                        device_is_disk and device.key not in disk_devices:
                    removal = client_factory.create(
                                    'ns0:VirtualDeviceConfigSpec')
                    removal.device = device
                    removal.operation = 'remove'
                    if device_is_disk:
                        # specify a profile on the disk as one of the disks
                        # could be associated with a storage IO control enabled
                        # profile and clone then fails without profile even if
                        # we remove the disk
                        data = volume_mapping.get(device.key)
                        if data and data.get('profile_id'):
                            profile_spec = client_factory.create(
                                "ns0:VirtualMachineDefinedProfileSpec")
                            profile_spec.profileId = data.get('profile_id')

                            locator = client_factory.create(
                                "ns0:VirtualMachineRelocateSpecDiskLocator")
                            locator.diskId = device.key
                            locator.datastore = device.backing.datastore
                            locator.profile = [profile_spec]
                            disk_locators.append(locator)
                    device_change.append(removal)

            config_spec.deviceChange = device_change
            rel_spec.disk = disk_locators

        clone_spec = vm_util.clone_vm_spec(client_factory,
                                           rel_spec,
                                           power_on=False,
                                           snapshot=snapshot_ref,
                                           template=True,
                                           config=config_spec)

        LOG.debug("Cloning VM %s", vm_name)
        vm_clone_task = self._session._call_method(
            self._session.vim,
            "CloneVM_Task",
            vm_ref,
            folder=self._get_project_folder(dc_info,
                                            project_id=instance.project_id,
                                            type_='Images'),
            name=vm_name,
            spec=clone_spec)
        self._session._wait_for_task(vm_clone_task)
        LOG.info("Cloned VM %s", vm_name)
        task_info = self._session._call_method(vutil,
                                               "get_object_property",
                                               vm_clone_task,
                                               "info")
        return task_info.result

    def snapshot(self, context, instance, image_id, update_task_state,
                 volume_mapping):
        """Create snapshot from a running VM instance.

        Steps followed are:

        1. Get the name of the vmdk file which the VM points to right now.
           Can be a chain of snapshots, so we need to know the last in the
           chain.
        2. Create the snapshot. A new vmdk is created which the VM points to
           now. The earlier vmdk becomes read-only.
        3. Creates a linked clone VM from the snapshot
        4. Exports the disk in the link clone VM as a streamOptimized disk.
        5. Delete the linked clone VM
        6. Deletes the snapshot in original instance.
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        def _get_vm_and_vmdk_attribs():
            # Get the vmdk info that the VM is pointing to
            vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                              instance.uuid)
            if not vmdk.path:
                LOG.debug("No root disk defined. Unable to snapshot.")
                raise error_util.NoRootDiskDefined()

            lst_properties = ["datastore", "summary.config.guestId"]
            props = self._session._call_method(vutil,
                                               "get_object_properties_dict",
                                               vm_ref,
                                               lst_properties)
            os_type = props['summary.config.guestId']
            datastores = props['datastore']
            return (vmdk, datastores, os_type)

        vmdk, datastores, os_type = _get_vm_and_vmdk_attribs()
        ds_ref = datastores.ManagedObjectReference[0]
        dc_info = self.get_datacenter_ref_and_name(ds_ref)

        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)

        # TODO(vui): convert to creating plain vm clone and uploading from it
        # instead of using live vm snapshot.

        snapshot_ref = None

        snapshot_vm_ref = None

        try:
            # If we do linked clones, we need to have a snapshot
            if CONF.vmware.clone_from_snapshot or not\
                    CONF.vmware.full_clone_snapshots:
                snapshot_ref = self._create_vm_snapshot(instance, vm_ref,
                                                        image_id=image_id)

            if not CONF.vmware.full_clone_snapshots:
                disk_move_type = "createNewChildDiskBacking"
            else:
                disk_move_type = None

            snapshot_vm_ref = self._create_vm_clone(instance,
                                                vm_ref,
                                                snapshot_ref,
                                                dc_info,
                                                disk_move_type=disk_move_type,
                                                image_id=image_id,
                                                disks=[vmdk],
                                                volume_mapping=volume_mapping)

            update_task_state(task_state=task_states.IMAGE_UPLOADING,
                              expected_state=task_states.IMAGE_PENDING_UPLOAD)
            images.upload_image_stream_optimized(
                context, image_id, instance, self._session, vm=snapshot_vm_ref,
                vmdk_size=vmdk.capacity_in_bytes)
        finally:
            if snapshot_vm_ref:
                try:
                    vm_util.destroy_vm(self._session, instance,
                                       snapshot_vm_ref)
                except Exception:
                    # exception is logged inside the function. we can continue.
                    pass
            # Deleting the snapshot after destroying the temporary VM created
            # based on it allows the instance vm's disks to be consolidated.
            # TODO(vui) Add handling for when vmdk volume is attached.
            if snapshot_ref:
                self._delete_vm_snapshot(instance, vm_ref, snapshot_ref)

    def reboot(self, instance, network_info, reboot_type="SOFT"):
        """Reboot a VM instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        props = self._get_instance_props(vm_ref)
        pwr_state = props.get('runtime.powerState')
        tools_status = props.get('summary.guest.toolsStatus')
        tools_running_status = props.get('summary.guest.toolsRunningStatus')

        # Raise an exception if the VM is not powered On.
        if pwr_state not in ["poweredOn"]:
            reason = _("instance is not powered on")
            raise exception.InstanceRebootFailure(reason=reason)

        # If latest vmware tools are installed in the VM, and that the tools
        # are running, then only do a guest reboot. Otherwise do a hard reset.
        if (tools_status == "toolsOk" and
                tools_running_status == "guestToolsRunning" and
                reboot_type == "SOFT"):
            LOG.debug("Rebooting guest OS of VM")
            self._session._call_method(self._session.vim, "RebootGuest",
                                       vm_ref)
            LOG.debug("Rebooted guest OS of VM")
        else:
            LOG.debug("Doing hard reboot of VM")
            reset_task = self._session._call_method(self._session.vim,
                                                    "ResetVM_Task", vm_ref)
            self._session._wait_for_task(reset_task)
            LOG.debug("Did hard reboot of VM")

    def _destroy_instance(self, context, instance, destroy_disks=True):
        # Destroy a VM instance
        try:
            vm_ref = vm_util.get_vm_ref(self._session, instance)
            lst_properties = ["config.files.vmPathName", "runtime.powerState",
                              "datastore"]
            props = self._session._call_method(vutil,
                                               "get_object_properties_dict",
                                               vm_ref,
                                               lst_properties)
            pwr_state = props['runtime.powerState']

            vm_config_pathname = props.get('config.files.vmPathName')
            vm_ds_path = None
            if vm_config_pathname is not None:
                vm_ds_path = ds_obj.DatastorePath.parse(
                        vm_config_pathname)

            # Power off the VM if it is in PoweredOn state.
            if pwr_state == "poweredOn":
                vm_util.power_off_instance(self._session, instance, vm_ref)

            # Un-register the VM
            try:
                LOG.debug("Unregistering the VM")
                self._session._call_method(self._session.vim,
                                           "UnregisterVM", vm_ref)
                LOG.debug("Unregistered the VM")
            except Exception as excep:
                LOG.warning("In vmwareapi:vmops:_destroy_instance, got "
                            "this exception while un-registering the VM: %s",
                            excep)

            # Delete the folder holding the VM related content on
            # the datastore.
            if destroy_disks and vm_ds_path:
                try:
                    dir_ds_compliant_path = vm_ds_path.parent
                    LOG.debug("Deleting contents of the VM from "
                              "datastore %(datastore_name)s",
                              {'datastore_name': vm_ds_path.datastore})
                    ds_ref_ret = props['datastore']
                    ds_ref = ds_ref_ret.ManagedObjectReference[0]
                    dc_info = self.get_datacenter_ref_and_name(ds_ref)
                    ds_util.file_delete(self._session,
                                        dir_ds_compliant_path,
                                        dc_info.ref)
                    LOG.debug("Deleted contents of the VM from "
                              "datastore %(datastore_name)s",
                              {'datastore_name': vm_ds_path.datastore})
                except Exception:
                    LOG.warning("In vmwareapi:vmops:_destroy_instance, "
                                "exception while deleting the VM contents "
                                "from the disk",
                                exc_info=True)
        except exception.InstanceNotFound:
            LOG.warning('Instance does not exist on backend')
        except Exception:
            LOG.exception(_('Destroy instance failed'))
        finally:
            vm_util.vm_ref_cache_delete(instance.uuid)

    def destroy(self, context, instance, destroy_disks=True):
        """Destroy a VM instance.

        Steps followed for each VM are:
        1. Power off, if it is in poweredOn state.
        2. Un-register.
        3. Delete the contents of the folder holding the VM related data.
        """
        LOG.debug("Destroying instance")
        self._destroy_instance(context, instance, destroy_disks=destroy_disks)
        LOG.debug("Instance destroyed")

    def pause(self, instance):
        msg = _("pause not supported for vmwareapi")
        raise NotImplementedError(msg)

    def unpause(self, instance):
        msg = _("unpause not supported for vmwareapi")
        raise NotImplementedError(msg)

    def suspend(self, instance):
        """Suspend the specified instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        pwr_state = self._session._call_method(vutil,
                                               "get_object_property",
                                               vm_ref,
                                               "runtime.powerState")
        # Only PoweredOn VMs can be suspended.
        if pwr_state == "poweredOn":
            LOG.debug("Suspending the VM")
            suspend_task = self._session._call_method(self._session.vim,
                    "SuspendVM_Task", vm_ref)
            self._session._wait_for_task(suspend_task)
            LOG.debug("Suspended the VM")
        # Raise Exception if VM is poweredOff
        elif pwr_state == "poweredOff":
            reason = _("instance is powered off and cannot be suspended.")
            raise exception.InstanceSuspendFailure(reason=reason)
        else:
            LOG.debug("VM was already in suspended state. So returning "
                      "without doing anything")

    def resume(self, instance):
        """Resume the specified instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        pwr_state = self._session._call_method(vutil,
                                               "get_object_property",
                                               vm_ref,
                                               "runtime.powerState")
        if pwr_state.lower() == "suspended":
            LOG.debug("Resuming the VM")
            suspend_task = self._session._call_method(
                                        self._session.vim,
                                       "PowerOnVM_Task", vm_ref)
            self._session._wait_for_task(suspend_task)
            LOG.debug("Resumed the VM")
        else:
            reason = _("instance is not in a suspended state")
            raise exception.InstanceResumeFailure(reason=reason)

    def _get_rescue_device(self, instance, vm_ref):
        hardware_devices = vm_util.get_hardware_devices(self._session, vm_ref)
        return vm_util.find_rescue_device(hardware_devices,
                                          instance)

    def rescue(self, context, instance, network_info, image_meta):
        """Rescue the specified instance.

        Attach the image that the instance was created from and boot from it.
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        # Get the root disk vmdk object
        vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                     uuid=instance.uuid)
        ds_ref = vmdk.device.backing.datastore
        datastore = ds_obj.get_datastore_by_ref(self._session, ds_ref)
        dc_info = self.get_datacenter_ref_and_name(datastore.ref)

        # Get the image details of the instance
        image_info = images.VMwareImage.from_image(context,
                                                   image_meta.id,
                                                   image_meta)
        vi = VirtualMachineInstanceConfigInfo(instance,
                                              image_info,
                                              datastore,
                                              dc_info,
                                              self._imagecache)
        vm_util.power_off_instance(self._session, instance, vm_ref)

        # Fetch the image if it does not exist in the cache
        self._fetch_image_if_missing(context, vi)

        # Get the rescue disk path
        vm_folder = ds_obj.DatastorePath.parse(vmdk.path).dirname
        rescue_disk_path = datastore.build_path(vm_folder,
                "%s-rescue.%s" % (image_info.image_id, image_info.file_type))

        # Copy the cached image to the be the rescue disk. This will be used
        # as the rescue disk for the instance.
        ds_util.disk_copy(self._session, dc_info.ref,
                          vi.cache_image_path, rescue_disk_path)
        # Attach the rescue disk to the instance
        self._volumeops.attach_disk_to_vm(vm_ref, instance, vmdk.adapter_type,
                                          vmdk.disk_type, rescue_disk_path)
        # Get the rescue device and configure the boot order to
        # boot from this device
        rescue_device = self._get_rescue_device(instance, vm_ref)
        factory = self._session.vim.client.factory
        boot_spec = vm_util.get_vm_boot_spec(factory, rescue_device)
        # Update the VM with the new boot order and power on
        vm_util.reconfigure_vm(self._session, vm_ref, boot_spec)
        vm_util.power_on_instance(self._session, instance, vm_ref=vm_ref)

    def unrescue(self, instance, power_on=True):
        """Unrescue the specified instance."""

        vm_ref = vm_util.get_vm_ref(self._session, instance)
        # Get the rescue device and detach it from the instance.
        try:
            rescue_device = self._get_rescue_device(instance, vm_ref)
        except exception.NotFound:
            with excutils.save_and_reraise_exception():
                LOG.error('Unable to access the rescue disk')
        vm_util.power_off_instance(self._session, instance, vm_ref)
        self._volumeops.detach_disk_from_vm(vm_ref, instance, rescue_device,
                                            destroy_disk=True)
        if power_on:
            vm_util.power_on_instance(self._session, instance, vm_ref=vm_ref)

    def power_off(self, instance, timeout=0, retry_interval=0):
        """Power off the specified instance.

        :param instance: nova.objects.instance.Instance
        :param timeout: How long to wait in seconds for the instance to
                        shutdown
        :param retry_interval: Interval to check if instance is already
                               shutdown in seconds.
        """
        if timeout and self._clean_shutdown(instance,
                                            timeout,
                                            retry_interval):
            return

        vm_util.power_off_instance(self._session, instance)
        self.update_cached_instances()

    def _clean_shutdown(self, instance, timeout, retry_interval):
        """Perform a soft shutdown on the VM.
        :param instance: nova.objects.instance.Instance
        :param timeout: How long to wait in seconds for the instance to
                        shutdown
        :param retry_interval: Interval to check if instance is already
                               shutdown in seconds.
           :return: True if the instance was shutdown within time limit,
                    False otherwise.
        """
        LOG.debug("Performing Soft shutdown on instance")
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        props = self._get_instance_props(vm_ref)

        if props.get("runtime.powerState") != "poweredOn":
            LOG.debug("Instance not in poweredOn state.")
            return False

        if ((props.get("summary.guest.toolsStatus") == "toolsOk") and
            (props.get("summary.guest.toolsRunningStatus") ==
             "guestToolsRunning")):

            LOG.debug("Soft shutdown instance, timeout: %d",
                     timeout)
            self._session._call_method(self._session.vim,
                                       "ShutdownGuest",
                                       vm_ref)

            while timeout > 0:
                wait_time = min(retry_interval, timeout)
                props = self._get_instance_props(vm_ref)

                if props.get("runtime.powerState") == "poweredOff":
                    LOG.info("Soft shutdown succeeded.")
                    return True

                time.sleep(wait_time)
                timeout -= retry_interval

            LOG.warning("Timed out while waiting for soft shutdown.")
        else:
            LOG.debug("VMware Tools not running")

        return False

    def is_instance_in_resource_pool(self, instance):
        try:
            vm_ref = vm_util.get_vm_ref(self._session, instance)
            res_pool = self._session._call_method(vutil, "get_object_property",
                                                  vm_ref, "resourcePool")

            return vutil.get_moref_value(res_pool) == \
                vutil.get_moref_value(self._root_resource_pool)
        except (exception.InstanceNotFound,
                vexc.ManagedObjectNotFoundException):
            LOG.debug("Failed to find instance")
            return False

    def _get_instance_props(self, vm_ref):
        lst_properties = ["config.instanceUuid",
                          "runtime.powerState",
                          "summary.guest.toolsStatus",
                          "summary.guest.toolsRunningStatus",
                         ]

        self.update_cached_instances()
        vm_props = vm_util._VM_VALUE_CACHE.get(vm_ref.value, {})

        if set(vm_props.keys()).issuperset(lst_properties):
            return vm_props
        else:
            return self._session._call_method(
                vutil, "get_object_properties_dict",
                vm_ref, lst_properties)

    def power_on(self, instance):
        vm_util.power_on_instance(self._session, instance)
        self.update_cached_instances()

    def _update_instance_progress(self, context, instance, step, total_steps):
        """Update instance progress percent to reflect current step number
        """
        # Divide the action's workflow into discrete steps and "bump" the
        # instance's progress field as each step is completed.
        #
        # For a first cut this should be fine, however, for large VM images,
        # the clone disk step begins to dominate the equation. A
        # better approximation would use the percentage of the VM image that
        # has been streamed to the destination host.
        progress = round(float(step) / total_steps * 100)
        instance_uuid = instance.uuid
        LOG.debug("Updating instance '%(instance_uuid)s' progress to"
                  " %(progress)d",
                  {'instance_uuid': instance_uuid, 'progress': progress})
        instance.progress = progress
        instance.save()

    def _resize_vm(self, context, instance, vm_ref, flavor, image_meta):
        """Resizes the VM according to the flavor."""
        client_factory = self._session.vim.client.factory
        extra_specs = self._get_extra_specs(flavor, image_meta)
        metadata = self._get_instance_metadata(context, instance, flavor)
        vm_resize_spec = vm_util.get_vm_resize_spec(client_factory,
                                                    int(flavor.vcpus),
                                                    int(flavor.memory_mb),
                                                    extra_specs,
                                                    metadata=metadata)
        vm_util.reconfigure_vm(self._session, vm_ref, vm_resize_spec)

        old_flavor = instance.old_flavor
        old_needs_override = utils.is_big_vm(int(old_flavor.memory_mb),
                                             old_flavor) \
                             or utils.is_large_vm(int(old_flavor.memory_mb),
                                                  old_flavor)
        new_needs_override = utils.is_big_vm(int(flavor.memory_mb), flavor) \
                             or utils.is_large_vm(int(flavor.memory_mb),
                                                  flavor)

        if not old_needs_override and new_needs_override:
            # Make sure we don't automatically move around "big" VMs
            behavior = constants.DRS_BEHAVIOR_PARTIALLY_AUTOMATED
            LOG.debug("Adding DRS override '%s' for big VM.", behavior)
            cluster_util.update_cluster_drs_vm_override(self._session,
                                                        self._cluster,
                                                        vm_ref,
                                                        operation='add',
                                                        behavior=behavior)
        elif old_needs_override and not new_needs_override:
            # remove the old override, if we had one before. make sure we don't
            # error out if it was already deleted another way
            LOG.debug("Removing DRS override for former big VM.")
            try:
                cluster_util.update_cluster_drs_vm_override(self._session,
                                                            self._cluster,
                                                            vm_ref,
                                                            operation='remove')
            except Exception:
                LOG.exception('Could not remove DRS override.')

        self._clean_up_after_special_spawning(context, flavor.memory_mb,
                                              flavor)

    def _resize_disk(self, instance, vm_ref, vmdk, flavor):
        if (flavor.root_gb > instance.old_flavor.root_gb
                and flavor.root_gb > vmdk.capacity_in_bytes / units.Gi):
            root_disk_in_kb = flavor.root_gb * units.Mi
            ds_ref = vmdk.device.backing.datastore
            dc_info = self.get_datacenter_ref_and_name(ds_ref)
            folder = ds_obj.DatastorePath.parse(vmdk.path).dirname
            datastore = ds_obj.DatastorePath.parse(vmdk.path).datastore
            resized_disk = str(ds_obj.DatastorePath(datastore, folder,
                               'resized.vmdk'))
            ds_util.disk_copy(self._session, dc_info.ref, vmdk.path,
                              str(resized_disk))
            self._extend_virtual_disk(instance, root_disk_in_kb, resized_disk,
                                      dc_info.ref)
            self._volumeops.detach_disk_from_vm(vm_ref, instance, vmdk.device)
            original_disk = str(ds_obj.DatastorePath(datastore, folder,
                                'original.vmdk'))
            ds_util.disk_move(self._session, dc_info.ref, vmdk.path,
                              original_disk)
            ds_util.disk_move(self._session, dc_info.ref, resized_disk,
                              vmdk.path)
            self._volumeops.attach_disk_to_vm(vm_ref, instance,
                                              vmdk.adapter_type,
                                              vmdk.disk_type, vmdk.path)

    def _remove_ephemerals_and_swap(self, vm_ref):
        devices = vm_util.get_ephemerals(self._session, vm_ref)
        swap = vm_util.get_swap(self._session, vm_ref)
        if swap is not None:
            devices.append(swap)

        if devices:
            vm_util.detach_devices_from_vm(self._session, vm_ref, devices)

    def _resize_create_ephemerals_and_swap(self, vm_ref, instance,
                                           block_device_info):
        vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                     uuid=instance.uuid)
        if not vmdk.device:
            LOG.debug("No root disk attached!")
            return
        ds_ref = vmdk.device.backing.datastore
        datastore = ds_obj.get_datastore_by_ref(self._session, ds_ref)
        dc_info = self.get_datacenter_ref_and_name(ds_ref)
        folder = ds_obj.DatastorePath.parse(vmdk.path).dirname
        self._create_ephemeral(block_device_info, instance, vm_ref,
                               dc_info, datastore, folder, vmdk.adapter_type)
        self._create_swap(block_device_info, instance, vm_ref, dc_info,
                          datastore, folder, vmdk.adapter_type)

    def migrate_disk_and_power_off(self, context, instance, dest, flavor):
        """Transfers the disk of a running instance in multiple phases, turning
        off the instance before the end.
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                     uuid=instance.uuid)

        boot_from_volume = compute_utils.is_volume_backed_instance(context,
                                                                   instance)

        # Checks if the migration needs a disk resize down.
        if (not boot_from_volume and (
            flavor.root_gb < instance.flavor.root_gb or
            (flavor.root_gb != 0 and
             flavor.root_gb < vmdk.capacity_in_bytes / units.Gi))):
            reason = _("Unable to shrink disk.")
            raise exception.InstanceFaultRollback(
                exception.ResizeError(reason=reason))

        # 0. Zero out the progress to begin
        self._update_instance_progress(context, instance,
                                       step=0,
                                       total_steps=RESIZE_TOTAL_STEPS)

        # 1. Power off the instance
        vm_util.power_off_instance(self._session, instance, vm_ref)
        self._update_instance_progress(context, instance,
                                       step=1,
                                       total_steps=RESIZE_TOTAL_STEPS)

    def confirm_migration(self, migration, instance, network_info):
        """Confirms a resize, destroying the source VM."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                     uuid=instance.uuid)
        if not vmdk.device:
            return
        ds_ref = vmdk.device.backing.datastore
        dc_info = self.get_datacenter_ref_and_name(ds_ref)
        folder = ds_obj.DatastorePath.parse(vmdk.path).dirname
        datastore = ds_obj.DatastorePath.parse(vmdk.path).datastore
        original_disk = ds_obj.DatastorePath(datastore, folder,
                                             'original.vmdk')
        ds_browser = self._get_ds_browser(ds_ref)
        if ds_util.file_exists(self._session, ds_browser,
                               original_disk.parent,
                               original_disk.basename):
            ds_util.disk_delete(self._session, dc_info.ref,
                                str(original_disk))

    def _revert_migration_update_disks(self, vm_ref, instance, vmdk,
                                       block_device_info):
        ds_ref = vmdk.device.backing.datastore
        dc_info = self.get_datacenter_ref_and_name(ds_ref)
        folder = ds_obj.DatastorePath.parse(vmdk.path).dirname
        datastore = ds_obj.DatastorePath.parse(vmdk.path).datastore
        original_disk = ds_obj.DatastorePath(datastore, folder,
                                             'original.vmdk')
        ds_browser = self._get_ds_browser(ds_ref)
        if ds_util.file_exists(self._session, ds_browser,
                               original_disk.parent,
                               original_disk.basename):
            self._volumeops.detach_disk_from_vm(vm_ref, instance,
                                                vmdk.device)
            ds_util.disk_delete(self._session, dc_info.ref, vmdk.path)
            ds_util.disk_move(self._session, dc_info.ref,
                              str(original_disk), vmdk.path)
            self._volumeops.attach_disk_to_vm(vm_ref, instance,
                                              vmdk.adapter_type,
                                              vmdk.disk_type, vmdk.path)
        # Reconfigure ephemerals
        self._remove_ephemerals_and_swap(vm_ref)
        self._resize_create_ephemerals_and_swap(vm_ref, instance,
                                                block_device_info)

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info, power_on=True):
        """Finish reverting a resize."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        # Ensure that the VM is off
        vm_util.power_off_instance(self._session, instance, vm_ref)
        client_factory = self._session.vim.client.factory
        # Reconfigure the VM properties
        extra_specs = self._get_extra_specs(instance.flavor,
                                            instance.image_meta)
        metadata = self._get_instance_metadata(context, instance)
        vm_resize_spec = vm_util.get_vm_resize_spec(
            client_factory,
            int(instance.flavor.vcpus),
            int(instance.flavor.memory_mb),
            extra_specs,
            metadata=metadata)
        vm_util.reconfigure_vm(self._session, vm_ref, vm_resize_spec)

        vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                     uuid=instance.uuid)
        if vmdk.device:
            self._revert_migration_update_disks(vm_ref, instance, vmdk,
                                                block_device_info)
        # Relocate the instance back, if needed
        if instance.uuid not in self.list_instances():
            # Get the root disk vmdk object's adapter type
            adapter_type = vmdk.adapter_type

            self._detach_volumes(instance, block_device_info)
            LOG.debug("Relocating VM for reverting migration")
            try:
                self._relocate_vm(vm_ref, context, instance, network_info)
                LOG.debug("Relocated VM for reverting migration")
            except Exception as e:
                with excutils.save_and_reraise_exception():
                    LOG.error("Relocating the VM failed: %s", e)
            else:
                self.update_cluster_placement(context, instance)
            finally:
                self._attach_volumes(instance, block_device_info, adapter_type)

        if power_on:
            vm_util.power_on_instance(self._session, instance)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance=False,
                         block_device_info=None, power_on=True):
        """Completes a resize, turning on the migrated instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        flavor = instance.flavor
        boot_from_volume = compute_utils.is_volume_backed_instance(context,
                                                                   instance)
        reattach_volumes = False
        # 2. Relocate the VM if necessary
        # If the dest_compute is different from the source_compute, it means we
        # need to relocate the VM here since we are running on the dest_compute
        if migration.source_compute != migration.dest_compute:
            # Get the root disk vmdk object's adapter type
            vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                         uuid=instance.uuid)
            adapter_type = vmdk.adapter_type

            self._detach_volumes(instance, block_device_info)
            reattach_volumes = True
            LOG.debug("Relocating VM for migration to %s",
                      migration.dest_compute)
            try:
                self._relocate_vm(vm_ref, context, instance, network_info,
                                  image_meta)
                LOG.debug("Relocated VM to %s", migration.dest_compute)
            except Exception as e:
                with excutils.save_and_reraise_exception():
                    LOG.error("Relocating the VM failed with error: %s", e)
                    self._attach_volumes(instance, block_device_info,
                                         adapter_type)

            self.update_cluster_placement(context, instance)
            self.disable_drs_if_needed(instance)

        self._update_instance_progress(context, instance,
                                       step=2,
                                       total_steps=RESIZE_TOTAL_STEPS)
        # 3.Reconfigure the VM and disk
        self._resize_vm(context, instance, vm_ref, flavor, image_meta)
        if not boot_from_volume and resize_instance:
            vmdk = vm_util.get_vmdk_info(self._session, vm_ref,
                                         uuid=instance.uuid)
            self._resize_disk(instance, vm_ref, vmdk, flavor)
        self._update_instance_progress(context, instance,
                                       step=3,
                                       total_steps=RESIZE_TOTAL_STEPS)

        # 4. Purge ephemeral and swap disks
        self._remove_ephemerals_and_swap(vm_ref)
        self._update_instance_progress(context, instance,
                                       step=4,
                                       total_steps=RESIZE_TOTAL_STEPS)
        # 5. Update ephemerals
        self._resize_create_ephemerals_and_swap(vm_ref, instance,
                                                block_device_info)
        self._update_instance_progress(context, instance,
                                       step=5,
                                       total_steps=RESIZE_TOTAL_STEPS)

        # 6. Attach the volumes (if necessary)
        if reattach_volumes:
            self._attach_volumes(instance, block_device_info, adapter_type)
        self._update_instance_progress(context, instance,
                                       step=6,
                                       total_steps=RESIZE_TOTAL_STEPS)
        # 7. Start VM
        if power_on:
            vm_util.power_on_instance(self._session, instance, vm_ref=vm_ref)
        self._update_instance_progress(context, instance,
                                       step=7,
                                       total_steps=RESIZE_TOTAL_STEPS)

    def _relocate_vm(self, vm_ref, context, instance, network_info,
                     image_meta=None):
        image_meta = image_meta or instance.image_meta
        storage_policy = self._get_storage_policy(instance.flavor)
        allowed_ds_types = ds_util.get_allowed_datastore_types(
            image_meta.properties.hw_disk_type)
        datastore = ds_util.get_datastore(self._session, self._cluster,
                                          self._datastore_regex,
                                          storage_policy,
                                          allowed_ds_types)
        dc_info = self.get_datacenter_ref_and_name(datastore.ref)
        folder = self._get_project_folder(dc_info, instance.project_id,
                                          'Instances')

        client_factory = self._session.vim.client.factory
        spec = vm_util.relocate_vm_spec(client_factory,
                                        res_pool=self._root_resource_pool,
                                        folder=folder, datastore=datastore.ref)

        # Iterate over the network adapters and update the backing
        if network_info:
            spec.deviceChange = []
            vif_model = image_meta.properties.get('hw_vif_model',
                                                  constants.DEFAULT_VIF_MODEL)
            hardware_devices = vm_util.get_hardware_devices(self._session,
                                                            vm_ref)
            vif_infos = vmwarevif.get_vif_info(self._session,
                                               self._cluster,
                                               utils.is_neutron(),
                                               vif_model,
                                               network_info)
            for vif_info in vif_infos:
                device = vmwarevif.get_network_device(hardware_devices,
                                                      vif_info['mac_address'])
                if not device:
                    msg = _("No device with MAC address %s exists on the "
                            "VM") % vif_info['mac_address']
                    raise exception.NotFound(msg)

                # Update the network device backing
                config_spec = client_factory.create(
                    'ns0:VirtualDeviceConfigSpec')
                vm_util.set_net_device_backing(
                    client_factory, device, vif_info)
                config_spec.operation = "edit"
                config_spec.device = device
                spec.deviceChange.append(config_spec)

        vm_util.relocate_vm(self._session, vm_ref, spec=spec)

    def live_migration(self, instance, migrate_data, volume_mapping):
        defaults = migrate_data.relocate_defaults
        relocate_spec_defaults = defaults["relocate_spec"]
        disk_move_type = relocate_spec_defaults.get("diskMoveType",
            "moveAllDiskBackingsAndDisallowSharing")

        def moref(item):
            v = relocate_spec_defaults[item]
            return vutil.get_moref(v["value"], v["_type"])

        client_factory = self._session.vim.client.factory
        datastore = moref("datastore")
        relocate_spec = vm_util.relocate_vm_spec(client_factory,
            res_pool=moref("pool"),
            datastore=datastore,
            host=moref("host"),
            disk_move_type=disk_move_type,
            folder=moref("folder"))

        service = defaults.get("service")
        if service:
            credentials_dict = service.pop("credentials")
            credentials = client_factory.create(
                "ns0:" + credentials_dict.pop("_type"))

            for k, v in credentials_dict.items():
                setattr(credentials, k, v)

            relocate_spec.service = vm_util.create_service_locator(
                client_factory,
                service["url"],
                service["instance_uuid"],
                credentials,
                service["ssl_thumbprint"],
            )

        vm_ref = vm_util.get_vm_ref(self._session, instance)

        device_config_spec = []
        relocate_spec.deviceChange = device_config_spec
        disks = []
        relocate_spec.disk = disks

        netdevices = []
        for device in vm_util.get_hardware_devices(self._session, vm_ref):
            class_name = device.__class__.__name__
            if class_name in vm_util.ALL_SUPPORTED_NETWORK_DEVICES:
                netdevices.append(device)
            elif class_name == "VirtualDisk":
                locator = client_factory.create(
                    "ns0:VirtualMachineRelocateSpecDiskLocator")
                locator.diskId = device.key
                target = volume_mapping.get(device.key)
                if not target:  # Not a volume
                    locator.datastore = datastore
                else:
                    locator.datastore = target["datastore_ref"]
                    profile_id = target.get("profile_id")
                    if profile_id:
                        profile_spec = client_factory.create(
                            "ns0:VirtualMachineDefinedProfileSpec")
                        profile_spec.profileId = profile_id
                        locator.profile = [profile_spec]
                disks.append(locator)

        for vif_info in migrate_data.vif_infos:
            device = vmwarevif.get_network_device(netdevices,
                                                  vif_info["mac_address"])
            if not device:
                msg = _("No device with MAC address %s exists on the "
                        "VM") % vif_info["mac_address"]
                raise exception.NotFound(msg)

            # Update the network device backing
            config_spec = client_factory.create("ns0:VirtualDeviceConfigSpec")
            vm_util.set_net_device_backing(client_factory, device, vif_info)
            config_spec.operation = "edit"
            config_spec.device = device
            device_config_spec.append(config_spec)

        vm_util.relocate_vm(self._session, vm_ref, spec=relocate_spec)

    def _detach_volumes(self, instance, block_device_info):
        block_devices = driver.block_device_info_get_mapping(block_device_info)
        for disk in block_devices:
            self._volumeops.detach_volume(disk['connection_info'], instance)

    def _attach_volumes(self, instance, block_device_info, adapter_type):
        disks = driver.block_device_info_get_mapping(block_device_info)
        # make sure the disks are attached by the boot_index order (if any)
        for disk in sorted(disks,
                           key=lambda d: d['boot_index']
                           if 'boot_index' in d and d['boot_index'] > -1
                           else len(disks)):
            adapter_type = disk.get('disk_bus') or adapter_type
            self._volumeops.attach_volume(disk['connection_info'], instance,
                                          adapter_type)

    def poll_rebooting_instances(self, timeout, instances):
        """Poll for rebooting instances."""
        ctxt = nova_context.get_admin_context()

        instances_info = dict(instance_count=len(instances),
                timeout=timeout)

        if instances_info["instance_count"] > 0:
            LOG.info("Found %(instance_count)d hung reboots "
                     "older than %(timeout)d seconds", instances_info)

        for instance in instances:
            LOG.info("Automatically hard rebooting")
            self.compute_api.reboot(ctxt, instance, "HARD")

    def get_info(self, instance):
        """Return data about the VM instance."""
        powerstate_property = 'runtime.powerState'

        if not vm_util._VM_VALUE_CACHE:
            self.update_cached_instances()

        @vm_util.vm_ref_cache_heal_from_instance
        def _get_vm_props(session, instance):
            vm_ref = vm_util.get_vm_ref(self._session, instance)
            vm_props = vm_util._VM_VALUE_CACHE.get(vm_ref.value, {})
            if vm_props and powerstate_property in vm_props:
                return vm_props

            if CONF.vmware.use_property_collector:
                LOG.debug("VM instance data was not found on the cache.")

            return session._call_method(
                vutil, "get_object_properties_dict",
                vm_ref, [powerstate_property])

        try:
            vm_props = _get_vm_props(self._session, instance)
        except vexc.ManagedObjectNotFoundException:
            raise exception.InstanceNotFound(instance_id=instance.uuid)

        return hardware.InstanceInfo(
            state=constants.POWER_STATES[vm_props[powerstate_property]])

    def _get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        lst_properties = ["summary.config",
                          "summary.quickStats",
                          "summary.runtime"]
        vm_props = self._session._call_method(vutil,
                                              "get_object_properties_dict",
                                              vm_ref,
                                              lst_properties)
        data = {}
        # All of values received are objects. Convert them to dictionaries
        for value in vm_props.values():
            prop_dict = vim_util.object_to_dict(value, list_depth=1)
            data.update(prop_dict)
        return data

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        data = self._get_diagnostics(instance)
        # Add a namespace to all of the diagnostsics
        return {'vmware:' + k: v for k, v in data.items()}

    def get_instance_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        data = self._get_diagnostics(instance)
        state = data.get('powerState')
        if state:
            state = power_state.STATE_MAP[constants.POWER_STATES[state]]
        uptime = data.get('uptimeSeconds', 0)
        config_drive = configdrive.required_by(instance)
        diags = objects.Diagnostics(state=state,
                                    driver='vmwareapi',
                                    config_drive=config_drive,
                                    hypervisor_os='esxi',
                                    uptime=uptime)
        diags.memory_details = objects.MemoryDiagnostics(
            maximum = data.get('memorySizeMB', 0),
            used=data.get('guestMemoryUsage', 0))
        # TODO(garyk): add in cpu, nic and disk stats
        return diags

    def _get_vnc_console_connection(self, instance):
        """Return connection info for a vnc console."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        opt_value = self._session._call_method(vutil,
                                               'get_object_property',
                                               vm_ref,
                                               vm_util.VNC_CONFIG_KEY)
        if opt_value:
            port = int(opt_value.value)
        else:
            raise exception.ConsoleTypeUnavailable(console_type='vnc')

        return {'port': port,
                'internal_access_path': None}

    @staticmethod
    def _get_machine_id_str(network_info):
        machine_id_str = ''
        for vif in network_info:
            # TODO(vish): add support for dns2
            # TODO(sateesh): add support for injection of ipv6 configuration
            network = vif['network']
            ip_v4 = netmask_v4 = gateway_v4 = broadcast_v4 = dns = None
            subnets_v4 = [s for s in network['subnets'] if s['version'] == 4]
            if len(subnets_v4) > 0:
                if len(subnets_v4[0]['ips']) > 0:
                    ip_v4 = subnets_v4[0]['ips'][0]
                if len(subnets_v4[0]['dns']) > 0:
                    dns = subnets_v4[0]['dns'][0]['address']

                netmask_v4 = str(subnets_v4[0].as_netaddr().netmask)
                gateway_v4 = subnets_v4[0]['gateway']['address']
                broadcast_v4 = str(subnets_v4[0].as_netaddr().broadcast)

            interface_str = ";".join([vif['address'],
                                      ip_v4 and ip_v4['address'] or '',
                                      netmask_v4 or '',
                                      gateway_v4 or '',
                                      broadcast_v4 or '',
                                      dns or ''])
            machine_id_str = machine_id_str + interface_str + '#'
        return machine_id_str

    def _set_machine_id(self, client_factory, instance, network_info,
                        vm_ref=None):
        """Set the machine id of the VM for guest tools to pick up
        and reconfigure the network interfaces.
        """
        if vm_ref is None:
            vm_ref = vm_util.get_vm_ref(self._session, instance)

        machine_id_change_spec = vm_util.get_machine_id_change_spec(
                                 client_factory,
                                 self._get_machine_id_str(network_info))

        LOG.debug("Reconfiguring VM instance to set the machine id")
        vm_util.reconfigure_vm(self._session, vm_ref, machine_id_change_spec)
        LOG.debug("Reconfigured VM instance to set the machine id")

    @utils.synchronized('vmware.get_and_set_vnc_port')
    def _get_and_set_vnc_config(self, client_factory, instance, vm_ref):
        """Set the vnc configuration of the VM."""
        port = vm_util.get_vnc_port(self._session)
        vnc_config_spec = vm_util.get_vnc_config_spec(
                                      client_factory, port)

        LOG.debug("Reconfiguring VM instance to enable vnc on "
                  "port - %(port)s", {'port': port})
        vm_util.reconfigure_vm(self._session, vm_ref, vnc_config_spec)
        LOG.debug("Reconfigured VM instance to enable vnc on "
                  "port - %(port)s", {'port': port})

    def _get_ds_browser(self, ds_ref):
        ds_browser = self._datastore_browser_mapping.get(ds_ref.value)
        if not ds_browser:
            ds_browser = self._session._call_method(vutil,
                                                    "get_object_property",
                                                    ds_ref,
                                                    "browser")
            self._datastore_browser_mapping[ds_ref.value] = ds_browser
        return ds_browser

    def _create_folder_if_missing(self, ds_name, ds_ref, folder):
        """Create a folder if it does not exist.

        Currently there are two folder that are required on the datastore
         - base folder - the folder to store cached images
         - temp folder - the folder used for snapshot management and
                         image uploading
        This method is aimed to be used for the management of those
        folders to ensure that they are created if they are missing.
        The ds_util method mkdir will be used to check if the folder
        exists. If this throws and exception 'FileAlreadyExistsException'
        then the folder already exists on the datastore.
        """
        path = ds_obj.DatastorePath(ds_name, folder)
        dc_info = self.get_datacenter_ref_and_name(ds_ref)
        try:
            ds_util.mkdir(self._session, path, dc_info.ref)
            LOG.debug("Folder %s created.", path)
        except vexc.FileAlreadyExistsException:
            # NOTE(hartsocks): if the folder already exists, that
            # just means the folder was prepped by another process.
            pass

    def check_cache_folder(self, ds_name, ds_ref):
        """Check that the cache folder exists."""
        self._create_folder_if_missing(ds_name, ds_ref, self._base_folder)

    def check_temp_folder(self, ds_name, ds_ref):
        """Check that the temp folder exists."""
        self._create_folder_if_missing(ds_name, ds_ref, self._tmp_folder)

    def inject_network_info(self, instance, network_info):
        """inject network info for specified instance."""
        # Set the machine.id parameter of the instance to inject
        # the NIC configuration inside the VM
        client_factory = self._session.vim.client.factory
        self._set_machine_id(client_factory, instance, network_info)

    def manage_image_cache(self, context, instances):
        if not CONF.remove_unused_base_images:
            LOG.debug("Image aging disabled. Aging will not be done.")
            return

        datastores = ds_util.get_available_datastores(self._session,
                                                      self._cluster,
                                                      self._datastore_regex)
        datastores_info = []
        for ds in datastores:
            dc_info = self.get_datacenter_ref_and_name(ds.ref)
            datastores_info.append((ds, dc_info))
        self._imagecache.update(context, instances, datastores_info)

        self._age_cached_image_templates(dc_info)

    def _age_cached_image_templates(self, dc_info):
        images_folders = self._get_all_images_folders(dc_info)
        for folder_ref in images_folders:
            self._destroy_expired_image_templates(folder_ref)

    def _get_all_images_folders(self, dc_info):
        """Return all Folder morefs containing image templates

        folder structure is
         OpenStack
           -> Project (<uuid>)
             -> Images
           -> Project (<uuid>)
           ....
        """
        os_folder_moref = None
        image_folder_to_parent = {}
        prj_folder_to_parent = {}
        retr_res = vim_util.get_objects(self._session.vim, 'Folder',
                                        properties_to_collect=['name',
                                                               'parent'])
        with vutil.WithRetrieval(self._session.vim, retr_res) as retr_objects:
            for obj_content in retr_objects:
                prop_dict = vutil.propset_dict(obj_content.propSet)
                parent = prop_dict.get('parent', None)
                if not parent:
                    continue
                parent = parent.value
                name = prop_dict['name']
                moref = obj_content.obj.value
                if name == 'OpenStack' and parent == dc_info.vmFolder.value:
                    os_folder_moref = moref
                elif name == 'Images':
                    image_folder_to_parent[moref] = parent
                elif re.match('Project \([0-9a-f]+\)', name):
                    prj_folder_to_parent[moref] = parent

        images_folders = []
        if os_folder_moref:
            # find all "Project (<uuid>)" having "OpenStack" as parent
            prj_folders = [prj_ref for prj_ref in prj_folder_to_parent
                           if prj_folder_to_parent[prj_ref] == os_folder_moref]
            # find all "Images" folders of above's folders
            images_folders = [vutil.get_moref(img_ref, 'Folder')
                for img_ref in image_folder_to_parent
                if image_folder_to_parent[img_ref] in prj_folders]

        return images_folders

    def _get_image_template_vms(self, templ_vm_folder_ref):
        try:
            all_vms_retr_res = vim_util.get_inner_objects(self._session.vim,
                templ_vm_folder_ref, 'childEntity',
                'VirtualMachine', properties_to_collect=['name'])
            uuid_ptrn = '-'.join(5 * ['[0-9a-f]{{{}}}']).format(8, 4, 4, 4, 12)
            if self._datastore_regex is not None:
                ds_regex = re.sub(r'[\^\$]', '', self._datastore_regex.pattern)
            else:
                ds_regex = '[^)]+'
            img_templ_ptrn = r'^{} \({}\)$'.format(uuid_ptrn, ds_regex)
            templ_vms = []
            with vutil.WithRetrieval(self._session.vim,
                                     all_vms_retr_res) as retr_objects:
                for oc in retr_objects:
                    vm_name = oc.propSet[0].val
                    if re.match(img_templ_ptrn, vm_name):
                        templ_vms.append((oc.obj, vm_name))
            return templ_vms
        except vexc.VimFaultException as excep:
            if vexc.NOT_AUTHENTICATED in excep.fault_list:
                # Check if session is active to decide if NotAuthenticated
                # indicates empty result returned by RetrievePropertiesEx (as
                # implemented in oslo.vmware) or it's a real exception.
                if self._session.is_current_session_active():
                    return []
                else:
                    raise

    def _destroy_expired_image_templates(self, templ_vm_folder_ref):
        templ_vms = self._get_image_template_vms(templ_vm_folder_ref)
        if not templ_vms:
            return
        expired_templ_vms = {moref.value: (moref, name)
                             for moref, name in templ_vms}

        client_factory = self._session.vim.client.factory
        task_filter_spec = client_factory.create('ns0:TaskFilterSpec')
        task_filter_spec.entity = client_factory.create(
                                        'ns0:TaskFilterSpecByEntity')
        task_filter_spec.entity.entity = templ_vm_folder_ref
        task_filter_spec.entity.recursion = "children"

        templ_tasks = vm_util.TaskHistoryCollectorItems(
            self._session, task_filter_spec, reverse_page_order=True)

        for ti in templ_tasks:
            # Look for template creation or clone from template
            if ti.descriptionId in ["ResourcePool.ImportVAppLRO",
                                    "VirtualMachine.clone"]:
                templ_vm_ref = ti.entity
                if timeutils.is_older_than(ti.queueTime,
                        CONF.remove_unused_original_minimum_age_seconds):
                    break
                else:
                    expired_templ_vms.pop(templ_vm_ref.value, None)
                    if not expired_templ_vms:
                        break

        for templ_vm_ref, templ_vm_name in expired_templ_vms.values():
            msg = "Destroying expired image-template VM {}"
            LOG.debug(msg.format(templ_vm_name))
            try:
                vm_util.destroy_vm(self._session, None, templ_vm_ref)
            except vexc.VimFaultException as e:
                with excutils.save_and_reraise_exception() as ctx:
                    if 'InvalidArgument' in e.fault_list \
                            and 'ConfigSpec.files.vmPathName' \
                                 in six.text_type(e):
                        ctx.reraise = False
                        # the datastore path for the template VM got lost. we
                        # unregister instead of destroying then, because we
                        # can't use it anymore anyways.
                        self._unregister_template_vm(templ_vm_ref)

    def _get_valid_vms_from_retrieve_result(self, retrieve_result,
                                            return_properties=False,
                                            include_moref=False):
        """Returns list of valid vms from RetrieveResult object.

        If `return_properties` is True, it will also return the properties of
        these VMs, thus returning a tuple (vm_uuid, properties).

        If `include_moref' is True, it will return a tuple as above and the
        properties will include a special "obj" key containing the moref.
        """
        lst_vms = []

        with vutil.WithRetrieval(self._session.vim, retrieve_result) \
                as objects:
            for vm in objects:
                prop_set = getattr(vm, 'propSet', None)
                if not prop_set:
                    continue
                vm_uuid = None
                conn_state = None
                props = {}
                for prop in vm.propSet:
                    if prop.name == "runtime.connectionState":
                        conn_state = prop.val
                    elif prop.name == 'config.extraConfig["nvp.vm-uuid"]':
                        vm_uuid = prop.val.value
                    props[prop.name] = prop.val
                # Ignore VM's that do not have nvp.vm-uuid defined
                if not vm_uuid:
                    continue
                # Ignoring the orphaned or inaccessible VMs
                if conn_state in ["orphaned", "inaccessible"]:
                    continue

                if include_moref:
                    props['obj'] = vm.obj

                if return_properties:
                    lst_vms.append((vm_uuid, props))
                else:
                    lst_vms.append(vm_uuid)

        return lst_vms

    def instance_exists(self, instance):
        try:
            vm_util.get_vm_ref(self._session, instance)
            return True
        except exception.InstanceNotFound:
            return False

    def attach_interface(self, context, instance, image_meta, vif):
        """Attach an interface to the instance."""
        vif_model = image_meta.properties.get('hw_vif_model',
                                              constants.DEFAULT_VIF_MODEL)
        vif_model = vm_util.convert_vif_model(vif_model)
        vif_info = vmwarevif.get_vif_dict(self._session, self._cluster,
                                          vif_model, utils.is_neutron(), vif)
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        # Ensure that there is not a race with the port index management
        with lockutils.lock(instance.uuid,
                            lock_file_prefix='nova-vmware-hot-plug'):
            port_index = vm_util.get_attach_port_index(self._session, vm_ref)
            client_factory = self._session.vim.client.factory
            extra_specs = self._get_extra_specs(instance.flavor)

            attach_config_spec = vm_util.get_network_attach_config_spec(
                                        client_factory, vif_info, port_index,
                                        extra_specs.vif_limits)
            LOG.debug("Reconfiguring VM to attach interface")
            try:
                vm_util.reconfigure_vm(self._session, vm_ref,
                                       attach_config_spec)
            except Exception as e:
                LOG.error('Attaching network adapter failed. Exception: %s',
                          e)
                raise exception.InterfaceAttachFailed(
                        instance_uuid=instance.uuid)

            self._network_api.update_instance_vnic_index(
                context, instance, vif, port_index)

        LOG.debug("Reconfigured VM to attach interface")

    def detach_interface(self, context, instance, vif):
        """Detach an interface from the instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        # Ensure that there is not a race with the port index management
        with lockutils.lock(instance.uuid,
                            lock_file_prefix='nova-vmware-hot-plug'):
            port_index = vm_util.get_vm_detach_port_index(self._session,
                                                          vm_ref,
                                                          vif['id'])
            if port_index is None:
                msg = _("No device with interface-id %s exists on "
                        "VM") % vif['id']
                raise exception.NotFound(msg)

            hardware_devices = vm_util.get_hardware_devices(self._session,
                                                            vm_ref)
            device = vmwarevif.get_network_device(hardware_devices,
                                                  vif['address'])
            if device is None:
                msg = _("No device with MAC address %s exists on the "
                        "VM") % vif['address']
                raise exception.NotFound(msg)

            self._network_api.update_instance_vnic_index(
                context, instance, vif, None)

            client_factory = self._session.vim.client.factory
            detach_config_spec = vm_util.get_network_detach_config_spec(
                                        client_factory, device, port_index)
            LOG.debug("Reconfiguring VM to detach interface")
            try:
                vm_util.reconfigure_vm(self._session, vm_ref,
                                       detach_config_spec)
            except Exception as e:
                LOG.error('Detaching network adapter failed. Exception: %s',
                          e)
                raise exception.InterfaceDetachFailed(
                        instance_uuid=instance.uuid)
        LOG.debug("Reconfigured VM to detach interface")

    def _use_disk_image_as_full_clone(self, vm_ref, vi):
        """Uses cached image disk by copying it into the VM directory."""

        instance_folder = vi.instance.uuid
        root_disk_name = "%s.vmdk" % vi.instance.uuid
        root_disk_ds_loc = vi.datastore.build_path(instance_folder,
                                                   root_disk_name)

        vm_util.copy_virtual_disk(
                self._session,
                vi.dc_info.ref,
                str(vi.cache_image_path),
                str(root_disk_ds_loc))

        self._extend_if_required(
                vi.dc_info, vi.ii, vi.instance, str(root_disk_ds_loc))

        self._volumeops.attach_disk_to_vm(
                vm_ref, vi.instance,
                vi.ii.adapter_type, vi.ii.disk_type,
                str(root_disk_ds_loc),
                vi.root_gb * units.Mi, False,
                disk_io_limits=vi._extra_specs.disk_io_limits)

    def _sized_image_exists(self, sized_disk_ds_loc, ds_ref):
        ds_browser = self._get_ds_browser(ds_ref)
        return ds_util.file_exists(
                self._session, ds_browser, sized_disk_ds_loc.parent,
                sized_disk_ds_loc.basename)

    def _use_disk_image_as_linked_clone(self, vm_ref, vi):
        """Uses cached image as parent of a COW child in the VM directory."""

        sized_image_disk_name = "%s.vmdk" % vi.ii.image_id
        if vi.root_gb > 0:
            sized_image_disk_name = "%s.%s.vmdk" % (vi.ii.image_id, vi.root_gb)
        sized_disk_ds_loc = vi.cache_image_folder.join(sized_image_disk_name)

        # Ensure only a single thread extends the image at once.
        # We do this by taking a lock on the name of the extended
        # image. This allows multiple threads to create resized
        # copies simultaneously, as long as they are different
        # sizes. Threads attempting to create the same resized copy
        # will be serialized, with only the first actually creating
        # the copy.
        #
        # Note that the object is in a per-nova cache directory,
        # so inter-nova locking is not a concern. Consequently we
        # can safely use simple thread locks.

        with lockutils.lock(str(sized_disk_ds_loc),
                            lock_file_prefix='nova-vmware-image'):

            if not self._sized_image_exists(sized_disk_ds_loc,
                                            vi.datastore.ref):
                LOG.debug("Copying root disk of size %sGb", vi.root_gb,
                          instance=vi.instance)
                try:
                    vm_util.copy_virtual_disk(
                            self._session,
                            vi.dc_info.ref,
                            str(vi.cache_image_path),
                            str(sized_disk_ds_loc))
                except Exception as e:
                    LOG.warning("Root disk file creation failed - %s",
                                e, instance=vi.instance)
                    with excutils.save_and_reraise_exception():
                        LOG.error('Failed to copy cached image %(source)s to '
                                  '%(dest)s for resize: %(error)s',
                                  {'source': vi.cache_image_path,
                                   'dest': sized_disk_ds_loc,
                                   'error': e},
                                  instance=vi.instance)
                        try:
                            ds_util.file_delete(self._session,
                                                sized_disk_ds_loc,
                                                vi.dc_info.ref)
                        except vexc.FileNotFoundException:
                            # File was never created: cleanup not
                            # required
                            pass

                # Resize the copy to the appropriate size. No need
                # for cleanup up here, as _extend_virtual_disk
                # already does it
                self._extend_if_required(
                        vi.dc_info, vi.ii, vi.instance, str(sized_disk_ds_loc))

        # Associate the sized image disk to the VM by attaching to the VM a
        # COW child of said disk.
        self._volumeops.attach_disk_to_vm(
                vm_ref, vi.instance,
                vi.ii.adapter_type, vi.ii.disk_type,
                str(sized_disk_ds_loc),
                vi.root_gb * units.Mi, vi.ii.linked_clone,
                disk_io_limits=vi._extra_specs.disk_io_limits)

    def _use_iso_image(self, vm_ref, vi):
        """Uses cached image as a bootable virtual cdrom."""

        self._attach_cdrom_to_vm(
                vm_ref, vi.instance, vi.datastore.ref,
                str(vi.cache_image_path))

        # Optionally create and attach blank disk
        if vi.root_gb > 0:
            instance_folder = vi.instance.uuid
            root_disk_name = "%s.vmdk" % vi.instance.uuid
            root_disk_ds_loc = vi.datastore.build_path(instance_folder,
                                                       root_disk_name)

            # It is pointless to COW a blank disk
            linked_clone = False

            vm_util.create_virtual_disk(
                    self._session, vi.dc_info.ref,
                    vi.ii.adapter_type,
                    vi.ii.disk_type,
                    str(root_disk_ds_loc),
                    vi.root_gb * units.Mi)

            self._volumeops.attach_disk_to_vm(
                    vm_ref, vi.instance,
                    vi.ii.adapter_type, vi.ii.disk_type,
                    str(root_disk_ds_loc),
                    vi.root_gb * units.Mi, linked_clone,
                    disk_io_limits=vi._extra_specs.disk_io_limits)

    def get_datacenter_ref_and_name(self, ds_ref):
        """Get the datacenter name and the reference."""
        return ds_util.get_dc_info(self._session, ds_ref)

    def list_instances(self):
        if not CONF.vmware.use_property_collector:
            lst_vm_names = self._list_instances_in_cluster()
        else:
            self.update_cached_instances()
            lst_vm_names = [item["config.instanceUuid"]
                            for item in vm_util._VM_VALUE_CACHE.values()
                            if "config.instanceUuid" in item and item.get(
                                                    "config.managedBy")]

        LOG.debug("Got total of %s instances", str(len(lst_vm_names)))

        return lst_vm_names

    def _list_instances_in_cluster(self, additional_properties=None,
                                   include_moref=False):
        """Lists the VM instances that are registered with vCenter cluster."""
        properties = ['runtime.connectionState',
                      'config.extraConfig["nvp.vm-uuid"]']
        if additional_properties is not None:
            properties.extend(additional_properties)
        LOG.debug("Getting list of instances from cluster %s",
                  vutil.get_moref_value(self._cluster))
        vms = []
        if self._root_resource_pool:
            vms = self._session._call_method(
                vim_util, 'get_inner_objects', self._root_resource_pool, 'vm',
                'VirtualMachine', properties)
        return_properties = additional_properties is not None or include_moref
        lst_vm_names = self._get_valid_vms_from_retrieve_result(vms,
                                        return_properties=return_properties,
                                        include_moref=include_moref)

        return lst_vm_names

    def get_vnc_console(self, instance):
        """Return connection info for a vnc console using vCenter logic."""

        # vCenter does not run virtual machines and does not run
        # a VNC proxy. Instead, you need to tell OpenStack to talk
        # directly to the ESX host running the VM you are attempting
        # to connect to via VNC.

        vnc_console = self._get_vnc_console_connection(instance)
        host_name = vm_util.get_host_name_for_vm(
                        self._session,
                        instance)
        vnc_console['host'] = host_name

        # NOTE: VM can move hosts in some situations. Debug for admins.
        LOG.debug("VM %(uuid)s is currently on host %(host_name)s",
                  {'uuid': instance.uuid, 'host_name': host_name})
        return ctype.ConsoleVNC(**vnc_console)

    def get_mks_console(self, instance):
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        ticket = self._session._call_method(self._session.vim,
                                            'AcquireTicket',
                                            vm_ref,
                                            ticketType='mks')
        thumbprint = ticket.sslThumbprint.replace(':', '').lower()
        mks_auth = {'ticket': ticket.ticket,
                    'cfgFile': ticket.cfgFile,
                    'thumbprint': thumbprint}
        internal_access_path = jsonutils.dumps(mks_auth)
        return ctype.ConsoleMKS(ticket.host, ticket.port, internal_access_path)

    @staticmethod
    def _parse_change_set(change_set):
        for change in change_set:
            if change.op not in ("assign", "modify"):
                continue

            val = getattr(change, "val", None)
            if change.name == "config.managedBy":
                yield change.name, val and getattr(val, "extensionKey")\
                      == constants.EXTENSION_KEY
            else:
                yield change.name, val

    @utils.synchronized("vmware.update_cache")
    def update_cached_instances(self):
        if not CONF.vmware.use_property_collector:
            return

        vim = self._session.vim
        options = self._session.vim.client.factory.create("ns0:WaitOptions")
        options.maxWaitSeconds = 0

        if self._property_collector is None:
            pc = vim.service_content.propertyCollector
            self._property_collector = self._session._call_method(
                self._session.vim,
                "CreatePropertyCollector", pc)
            vim.CreateFilter(self._property_collector,
                spec=self._get_vm_monitor_spec(vim),
                partialUpdates=False)

        update_set = vim.WaitForUpdatesEx(self._property_collector,
            version=self._property_collector_version,
            options=options)

        while update_set:
            self._property_collector_version = update_set.version
            if update_set.filterSet and update_set.filterSet[0].objectSet:
                for update in update_set.filterSet[0].objectSet:
                    vm_ref = update.obj
                    if vm_ref["_type"] != "VirtualMachine":
                        continue
                    values = vm_util._VM_VALUE_CACHE[vm_ref.value]

                    if update.kind == "leave":
                        instance_uuid = values.get("config.instanceUuid")
                        vm_util.vm_ref_cache_delete(instance_uuid)
                        vm_util.vm_value_cache_delete(vm_ref)
                        LOG.debug("Removed instance %s (%s) from cache...",
                                  instance_uuid, vm_ref.value)
                    else:
                        changes = dict(self._parse_change_set(
                                                update.changeSet))
                        LOG.debug("%s.%s.%s: %s", vm_ref["_type"], update.kind,
                                  vm_ref.value, changes)
                        if not (changes.get("config.managedBy") or
                                values.get("config.managedBy")):
                            LOG.debug("%s Not managed by nova", vm_ref.value)
                            continue

                        instance_uuid = changes.get("config.instanceUuid")

                        if update.kind == "enter":
                            vm_util.vm_ref_cache_update(instance_uuid, vm_ref)
                        elif update.kind == "modify":
                            old_instance_uuid = values.get(
                                        "config.instanceUuid")
                            new_instance_uuid = changes.get(
                                        "config.instanceUuid")

                            if old_instance_uuid != new_instance_uuid:
                                vm_util.vm_ref_cache_delete(
                                    old_instance_uuid)
                                vm_util.vm_ref_cache_update(
                                    instance_uuid, vm_ref)

                        values.update(changes)
                        LOG.debug("%s.%s.%s -> %s", vm_ref["_type"],
                                  update.kind, vm_ref.value, values)

            update_set = vim.WaitForUpdatesEx(self._property_collector,
                version=self._property_collector_version,
                options=options)

    def _get_vm_monitor_spec(self, vim):
        client_factory = vim.client.factory
        traversal_spec_vm = vutil.build_traversal_spec(
            client_factory,
            "h_to_vm",
            "HostSystem",
            "vm",
            False,
            [])
        traversal_spec = vutil.build_traversal_spec(
            client_factory,
            "c_to_h",
            "ComputeResource",
            "host",
            False,
            [traversal_spec_vm])
        object_spec = vutil.build_object_spec(
            client_factory,
            self._cluster,
            [traversal_spec])
        property_spec = vutil.build_property_spec(
            client_factory,
            "HostSystem",
            ["vm"])
        property_spec_vm = vutil.build_property_spec(
            client_factory,
            "VirtualMachine",
            ["config.instanceUuid",
             "config.managedBy",
             "runtime.powerState",
             "summary.guest.toolsStatus",
             "summary.guest.toolsRunningStatus",
            ])
        property_filter_spec = vutil.build_property_filter_spec(
            client_factory,
            [property_spec, property_spec_vm],
            [object_spec])
        return property_filter_spec

    def update_vmref_cache(self):
        """List all VMs in the cluster and cache their morefs"""
        props = ['config.instanceUuid']
        instances = self._list_instances_in_cluster(props, include_moref=True)
        for vm_uuid, props in instances:
            if vm_uuid != props.get('config.instanceUuid'):
                continue

            vm_util.vm_ref_cache_update(vm_uuid, props['obj'])

    def set_compute_host(self, compute_host):
        """Called by the driver on init_host() so we know the compute host"""
        self._compute_host = compute_host

    def sync_server_group(self, context, sg_uuid):
        """Sync a server group by its uuid for the current host/cluster
        """
        # we have to ignore instances currently in a volatitle state, where
        # either VMware cannot support them being in a DRS rule or we expect
        # them to go away during the syncing process, which could lead to
        # errors. Therefore, we explicitly remove those members from the list
        # of expected members of a rule, which also removes them in the
        # cluster.
        STATES_EXCLUDING_MEMBERS_FROM_DRS_RULES = [
            task_states.DELETING,
            task_states.SHELVING,
            task_states.REBUILDING,
            task_states.REBUILD_BLOCK_DEVICE_MAPPING,
        ]
        LOG.debug('Starting sync for server-group %s', sg_uuid)

        @utils.synchronized('vmware-server-group-{}'.format(sg_uuid))
        def _sync_sync_server_group(context, sg_uuid):
            rule_prefix = '{}{}'.format(constants.DRS_PREFIX, sg_uuid)

            # retrieve the server-group with its members
            try:
                sg = objects.instance_group.InstanceGroup.get_by_uuid(context,
                                                                      sg_uuid)
            except exception.InstanceGroupNotFound:
                LOG.info('Server-group %s cannot be found in DB', sg_uuid)
                # check if we have it in the vCenter. if yes, it's an orphan
                # and needs deletion
                rules = cluster_util.get_rules_by_prefix(
                    self._session, self._cluster, rule_prefix)
                for rule in rules:
                    LOG.debug('Deleting DRS rule %s as orphan', rule.name)
                    cluster_util.delete_rule(
                        self._session, self._cluster, rule)
                    LOG.info('Deleted rule %s as orphan', rule.name)
                LOG.debug('Sync for server-group %s done', sg_uuid)
                return

            # First we check for all instances, which have ongoing migrations
            # on the given host, either as source or destination

            # Decision matrix for migrations:
            # Mig-Status Action/Host
            #            Source  Dest
            # Preparing  Remove  N/A
            # Running    Remove  Add
            # (Other states are consistent with default behaviour)
            #
            # So the Instance.host will always be the source of the migration,
            # and we want to remove the rules.
            # We only need to handle specially the case a running migration
            # on the destination host, and add it

            MigrationList = objects.migration.MigrationList
            filters = {
                "host": self._compute_host,
                "instance_uuid": sg.members,
                "status": ["preparing", "running"],
            }

            expected_members = {}

            migrations_by_instance_uuid = {}
            for migration in MigrationList.get_by_filters(context, filters):
                instance_uuid = migration.instance_uuid
                if migration.source_compute == self._compute_host:
                    # The host is the source of a migration
                    # That means the instance will be part of the instance list
                    # So we have to remember that instance to be removed from
                    # the DRS rule-set
                    migrations_by_instance_uuid[instance_uuid] = \
                        migration
                else:
                    # We now handle the destination side
                    if migration.status == "preparing":
                        # Not even started, we can ignore that one
                        continue

                    # Polling the cache, as vm_util.get_vm_ref is very slow
                    # for the negative search.
                    # We just have to ensure, that the cache holds a value
                    # before syncing the server group on the destination host
                    # Race conditions are averted by this functions lock
                    moref = vm_util.vm_ref_cache_get(instance_uuid)
                    if moref:
                        expected_members[instance_uuid] = moref

            # retrieve the instances, because sg.members contains all members
            # and we need to filter them for our host
            InstanceList = objects.instance.InstanceList
            filters = {'host': self._compute_host, 'uuid': sg.members,
                       'deleted': False}
            instances = InstanceList.get_by_filters(context, filters,
                                                    expected_attrs=[])

            for instance in instances:
                task_state = instance.task_state
                if task_state in STATES_EXCLUDING_MEMBERS_FROM_DRS_RULES:
                    LOG.debug("Excluding member %s of server-group %s, "
                              "because it's in task_state %s.",
                              instance.uuid, sg.uuid, task_state)
                    continue

                migration = migrations_by_instance_uuid.get(instance.uuid)
                if migration:
                    LOG.debug("Excluding member %s of server-group %s, "
                              "due to being on the source side of "
                              "ongoing migration %s.",
                              instance.uuid, sg.uuid, migration.uuid)
                    continue

                try:
                    moref = vm_util.get_vm_ref(self._session, instance)
                except exception.InstanceNotFound:
                    LOG.warning('Could not find moref for instance %s. '
                                'Ignoring member of server-group %s',
                                instance.uuid, sg.uuid)
                    continue
                expected_members[instance.uuid] = moref

            rule_name = '{}-{}'.format(rule_prefix, sg.policy)
            rule = cluster_util.get_rule(
                self._session, self._cluster, rule_name)

            if not rule:
                if len(expected_members) < 2 or sg.policy == 'soft-affinity':
                    LOG.debug('Sync for server-group %s done', sg_uuid)
                    return
                # we have to create a new rule
                LOG.debug('Creating missing DRS rule %s with members %s',
                          rule_name, ', '.join(expected_members))
                client_factory = self._session.vim.client.factory
                rule = cluster_util.create_vm_rule(
                    client_factory, rule_name, list(expected_members.values()),
                    policy=sg.policy)
                cluster_util.add_rule(
                    self._session, self._cluster, rule)
                LOG.info('Created missing DRS rule %s with members %s',
                         rule_name, ', '.join(expected_members))
                LOG.debug('Sync for server-group %s done', sg_uuid)
                return

            if sg.policy == 'soft-affinity':
                LOG.debug('Deleting DRS rule %s with policy soft-affinity',
                          rule_name)
                cluster_util.delete_rule(
                    self._session, self._cluster, rule)
                LOG.info('Deleted DRS rule %s with policy soft-affinity.',
                          rule_name)
                LOG.debug('Sync for server-group %s done', sg_uuid)
                return

            if len(expected_members) < 2:
                # we have to delete the rule
                LOG.debug('Deleting DRS rule %s with < 2 members.', rule_name)
                cluster_util.delete_rule(
                    self._session, self._cluster, rule)
                LOG.info('Deleted DRS rule %s with < 2 members.', rule_name)
                LOG.debug('Sync for server-group %s done', sg_uuid)
                return

            expected_moref_values = set(vutil.get_moref_value(m)
                                        for m in expected_members.values())
            existing_moref_values = set(vutil.get_moref_value(m)
                                        for m in rule.vm)
            if expected_moref_values == existing_moref_values:
                LOG.debug('Sync for server-group %s done', sg_uuid)
                return

            # we have to update the DRS rule to contain the right members
            rule.vm = list(expected_members.values())
            LOG.debug('Updating DRS rule %s with members %s',
                      rule_name, ', '.join(expected_members))
            cluster_util.update_rule(self._session, self._cluster, rule)
            LOG.info('Updated DRS rule %s with members %s',
                     rule_name, ', '.join(expected_members))
            LOG.debug('Sync for server-group %s done', sg_uuid)

        _sync_sync_server_group(context, sg_uuid)

    def place_vm(self, context, instance):
        # We currently only fill the bare-minimum to get a placement.
        # The datastore for the VM is selected as on instance creation,
        # instead of allowing placevm to decide it (which may be better)
        # We also do not pass the information about the NICs and other
        # attached disks, which may give the placement a better information
        client_factory = self._session.vim.client.factory
        flavor = instance.flavor
        image_meta = instance.image_meta
        image_info = images.VMwareImage.from_image(context,
                                                instance.image_ref,
                                                image_meta)

        extra_specs = self._get_extra_specs(flavor, image_meta)

        vi = self._get_vm_config_info(instance, image_info, extra_specs)

        vm_folder = self._get_project_folder(vi.dc_info,
            project_id=instance.project_id, type_='Instances')

        relocate_spec = vm_util.relocate_vm_spec(
            client_factory,
            res_pool=self._root_resource_pool,
            datastore=vi.datastore.ref,
            disk_move_type="moveAllDiskBackingsAndDisallowSharing",
            folder=vm_folder)

        placement_spec = client_factory.create("ns0:PlacementSpec")
        placement_spec.placementType = "relocate"
        # Maybe we want to allow that? Default is True
        # placement_spec.disallowPrerequisiteMoves = False
        placement_spec.relocateSpec = relocate_spec
        # So we do not place the vm on a failover host
        placement_spec.hosts, _ = \
            vm_util.get_hosts_and_reservations_for_cluster(
                self._session, self._cluster)

        # Sets cpuAllocation, memoryAllocation, numCPUs, memoryMB
        config_spec = vm_util.get_vm_resize_spec(client_factory,
                                                 int(flavor.vcpus),
                                                 int(flavor.memory_mb),
                                                 extra_specs)
        # The last mandatory field for config_spec per doc
        config_spec.version = extra_specs.hw_version
        placement_spec.configSpec = config_spec

        vm_group_name = self._get_admin_group_name_for_instance(instance)
        if vm_group_name:
            placement_rules = []
            placement_spec.rules = placement_rules
            cluster_rules = cluster_util.fetch_cluster_rules(self._session,
                                                             self._cluster)
            for rule in six.itervalues(cluster_rules):
                if getattr(rule, "vmGroupName", None) == vm_group_name:
                    placement_rules.append(rule)

        result = self._session._call_method(self._session.vim, "PlaceVm",
            self._cluster, placementSpec=placement_spec)
        return result
