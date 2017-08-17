# Copyright (c) 2013 Hewlett-Packard Development Company, L.P.
# Copyright (c) 2012 VMware, Inc.
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
Management class for host-related functions (start, reboot, etc).
"""

from oslo_utils import units
from oslo_utils import versionutils

from nova.compute import arch
from nova.compute import hv_type
from nova.compute import vm_mode
from nova import exception
from nova.virt.vmwareapi import ds_util
from nova.virt.vmwareapi import vim_util
from nova.virt.vmwareapi import vm_util
from oslo_log import log as logging
from oslo_vmware import vim_util as vutil

def _get_ds_capacity_and_freespace(session, cluster=None,
                                   datastore_regex=None):
    capacity = 0
    freespace = 0
    try:
        for ds in ds_util.get_available_datastores(session, cluster, datastore_regex):
            capacity += ds.capacity
            freespace += ds.freespace
    except exception.DatastoreNotFound:
        pass

    return capacity, freespace


class VCState(object):
    """Manages information about the vCenter cluster"""
    def __init__(self, session, host_name, cluster, datastore_regex):
        super(VCState, self).__init__()
        self._session = session
        self._host_name = host_name
        self._cluster = cluster
        self._datastore_regex = datastore_regex
        self._stats = {}
        self.update_status()

    def get_host_stats(self, refresh=False):
        """Return the current state of the cluster. If 'refresh' is
        True, run the update first.
        """
        if refresh or not self._stats:
            self.update_status()
        return self._stats

    def update_status(self):
        """Update the current state of the cluster."""
        capacity, freespace = _get_ds_capacity_and_freespace(self._session,
            self._cluster, self._datastore_regex)

        # Get cpu, memory stats from the cluster
        stats = vm_util.get_stats_from_cluster(self._session, self._cluster)
        about_info = self._session._call_method(vim_util, "get_about_info")
        data = {}
        data["vcpus"] = stats['vcpus']
        data["disk_total"] = capacity / units.Gi
        data["disk_available"] = freespace / units.Gi
        data["disk_used"] = data["disk_total"] - data["disk_available"]
        data["host_memory_total"] = stats['mem']['total']
        data["host_memory_free"] = stats['mem']['free']
        data["hypervisor_type"] = about_info.name
        data["hypervisor_version"] = versionutils.convert_version_to_int(
                str(about_info.version))
        data["hypervisor_hostname"] = self._host_name
        data["supported_instances"] = [
            (arch.I686, hv_type.VMWARE, vm_mode.HVM),
            (arch.X86_64, hv_type.VMWARE, vm_mode.HVM)]
        data["cpu_model"] = self.to_cpu_model(self._cluster)

        self._stats = data
        return data

    def to_cpu_model(self, cluster_obj):
        max_objects = 100
        vim = self._session.vim
        property_collector = vim.service_content.propertyCollector

        traversal_spec = vutil.build_traversal_spec(
            vim.client.factory,
            "c_to_h",
            "ComputeResource",
            "host",
            False,
            [])

        object_spec = vutil.build_object_spec(
            vim.client.factory,
            cluster_obj,
            [traversal_spec])
        property_spec = vutil.build_property_spec(
            vim.client.factory,
            "HostSystem",
            ["hardware", "config"])

        property_filter_spec = vutil.build_property_filter_spec(
            vim.client.factory,
            [property_spec],
            [object_spec])
        options = vim.client.factory.create('ns0:RetrieveOptions')
        options.maxObjects = max_objects

        pc_result = vim.RetrievePropertiesEx(property_collector, specSet=[property_filter_spec],options=options)

        result = []
        topology = dict()

        """ Retrieving needed hardware properties from ESX hosts """
        with vutil.WithRetrieval(vim, pc_result) as pc_objects:
            for objContent in pc_objects:
                processor_type = []
                cpu_vendor = []
                features = []

                props = {prop.name: prop.val for prop in objContent.propSet}

                for t in props["hardware"].cpuPkg:
                    processor_type.append(t.description)
                    cpu_vendor.append(t.vendor)

                for feature in props["config"].featureCapability:
                    if feature.featureName.startswith("cpuid."):
                        if  feature.value == "1":
                            features.append(feature.featureName.split(".", 1)[1].lower())

                features.sort()
                props["model"] = processor_type
                props["vendor"] = cpu_vendor[0].title()
                topology["cores"] = props["hardware"].cpuInfo.numCpuCores
                topology["sockets"] = props["hardware"].cpuInfo.numCpuPackages
                topology["threads"] = props["hardware"].cpuInfo.numCpuThreads
                props["topology"] = topology
                props["features"] = features
                del props["config"]
                del props["hardware"]

                result.append(props)

        equal = True

        """ Compare found ESX hosts """
        if result.__len__() > 1:
            for i in range(result.__len__() - 1):
                if result[i] == result[i + 1]:
                    continue
                else:
                    equal = False
                    break

        if not equal:
            return "CPU's for this cluster have different values!"

        return result[0]