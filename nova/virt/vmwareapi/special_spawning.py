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
from oslo_log import log as logging
import oslo_messaging as messaging
from oslo_vmware import vim_util as vutil

from nova.compute import rpcapi
import nova.conf
from nova import exception
from nova import profiler
from nova import rpc
from nova.virt.vmwareapi import cluster_util
from nova.virt.vmwareapi.vm_util import propset_dict

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF
FREE_HOST_STATE_DONE = 'done'
FREE_HOST_STATE_ERROR = 'error'
FREE_HOST_STATE_STARTED = 'started'


@profiler.trace_cls("rpc")
class SpecialVmSpawningInterface(object):
    """RPC client foundr calling _SpecialVmSpawningServer"""

    def __init__(self):
        super(SpecialVmSpawningInterface, self).__init__()
        target = messaging.Target(topic=rpcapi.RPC_TOPIC, version='5.0')
        self.router = rpc.ClientRouter(rpc.get_client(target))

    def remove_host_from_hostgroup(self, ctxt, compute_host_name):
        version = '5.0'
        cctxt = self.router.client(ctxt).prepare(
                server=compute_host_name, version=version)
        return cctxt.call(ctxt, 'remove_host_from_hostgroup')

    def free_host(self, ctxt, compute_host_name):
        version = '5.0'
        cctxt = self.router.client(ctxt).prepare(
                server=compute_host_name, version=version)
        return cctxt.call(ctxt, 'free_host')


class _SpecialVmSpawningServer(object):
    """Additional RPC interface for handling special spawning needs."""

    target = messaging.Target(version='5.0')

    def __init__(self, driver):
        self._driver = driver
        self._session = driver._session
        self._cluster = driver._cluster_ref
        self._vmops = driver._vmops

    def _get_group(self, cluster_config=None):
        """Return the hostgroup or None if not found."""
        if cluster_config is None:
            cluster_config = self._session._call_method(
                vutil, "get_object_property", self._cluster, "configurationEx")
        if not cluster_config:
            # that should never happen. we should not precede with whatever
            # called us
            msg = 'Cluster {} must have an attribute "configurationEx".'
            raise exception.ValidationError(msg.format(self._cluster))

        group_ret = getattr(cluster_config, 'group', None)
        if not group_ret:
            return None

        hg_name = CONF.vmware.bigvm_deployment_free_host_hostgroup_name
        if not hg_name:
            raise exception.ValidationError('Function for special spawning '
                'were called, but the setting '
                '"bigvm_deployment_free_host_hostgroup_name" is unconfigured.')

        for group in group_ret:
            # we only look for hostgroups
            if not hasattr(group, 'host'):
                continue

            # we are only instered in one special group
            if group.name == hg_name:
                return group

    def _get_vms_on_host(self, host_ref):
        """Return a list of VMs uuids with their memory size and state"""
        vm_data = []
        vm_ret = self._session._call_method(vutil,
                                            "get_object_property",
                                            host_ref,
                                            "vm")
        # if there are no VMs on the host, we don't need to look further
        if not vm_ret:
            return vm_data

        vm_mors = vm_ret.ManagedObjectReference
        result = self._session._call_method(vutil,
                            "get_properties_for_a_collection_of_objects",
                            "VirtualMachine", vm_mors,
                            ["config.instanceUuid", "runtime.powerState",
                             "config.hardware.memoryMB"])
        for obj in result.objects:
            vm_props = propset_dict(obj.propSet)
            vm_data.append((
                vm_props['config.instanceUuid'],
                vm_props['config.hardware.memoryMB'],
                vm_props['runtime.powerState']))
        return vm_data

    def remove_host_from_hostgroup(self, context):
        """Search for the host in the special spawning hostgroup and remove
        that group, because emptying it seems not to work.
        """
        group = self._get_group()
        # no group -> nothing to do
        if group is None:
            return True

        # if there are no hosts in the group, we're done
        if not getattr(group, 'host', None):
            return True

        client_factory = self._session.vim.client.factory

        group_spec = client_factory.create('ns0:ClusterGroupSpec')
        group_spec.operation = 'remove'
        group_spec.removeKey = group.name

        config_spec = client_factory.create('ns0:ClusterConfigSpecEx')
        config_spec.groupSpec = [group_spec]
        cluster_util.reconfigure_cluster(self._session, self._cluster,
                                         config_spec)

        return True

    def free_host(self, context):
        """Find a host that doesn't have a bigvm and put it into the special
        hostgroup. If that's already the case, return whether there are running
        VMs left on the host, i.e. the process is finished.
        """
        cluster_config = self._session._call_method(
            vutil, "get_object_property", self._cluster, "configurationEx")
        # get the group
        group = self._get_group(cluster_config)

        if group is None or not getattr(group, 'host', None):
            # find a host to free
            # get all the vms in a cluster, because we need to find a host
            # without big VMs. Take the one with least memory used.
            props = ['config.hardware.memoryMB', 'runtime.host',
                     'runtime.powerState']
            cluster_vms = self._vmops._list_instances_in_cluster(props)
            if not cluster_vms:
                LOG.error('Got no VMs for freeing a host for spawning. '
                          'Treating as error instead of continuing, as an '
                          'empty cluster is unlikely.')
                return FREE_HOST_STATE_ERROR

            vms_per_host = {}
            for vm_uuid, vm_props in cluster_vms:
                props = (vm_props.get('config.hardware.memoryMB', 0),
                         vm_props.get('runtime.powerState', 'poweredOff'))
                vms_per_host.setdefault(vm_props.get('runtime.host'), []). \
                        append(props)

            # filter for hosts without big VMs
            # FIXME ask david if we use big VMs here or the smaller special
            # spawning vms
            vms_per_host = {h: vms for h, vms in vms_per_host.items()
                            if all(mem < CONF.bigvm_mb for mem, state in vms)}

            if not vms_per_host:
                LOG.warning('No suitable host found for freeing a host for '
                            'spawning.')
                return FREE_HOST_STATE_ERROR

            mem_per_host = {h: sum(mem for mem, state in vms
                                   if state != 'poweredOff')
                            for h, vms in vms_per_host.items()}

            # take the one with least memory used
            host_ref, _ = sorted(mem_per_host.items(), key=lambda (x, y): y)[0]

            client_factory = self._session.vim.client.factory
            config_spec = client_factory.create('ns0:ClusterConfigSpecEx')

            # we need to either create the group from scratch or at least add a
            # host to it
            operation = 'add' if group is None else 'edit'
            group_spec = cluster_util._create_host_group_spec(client_factory,
                CONF.vmware.bigvm_deployment_free_host_hostgroup_name,
                [host_ref], operation, group)
            config_spec.groupSpec = [group_spec]
            cluster_util.reconfigure_cluster(self._session, self._cluster,
                                             config_spec)
        else:
            if len(group.host) > 1:
                LOG.warning('Found more than 1 host in spawning hostgroup.')
            host_ref = group.host[0]

        # check if there are running VMs on that host
        running_vms = [u for u, h, state in self._get_vms_on_host(host_ref)
                        if state != 'poweredOff']
        if running_vms:
            LOG.debug('Freeing up %(host)s for spawning in progress.',
                      {'host': host_ref.value})
            return FREE_HOST_STATE_STARTED

        LOG.info('Done freeing up %(host)s for spawning.',
                 {'host': host_ref.value})
        return FREE_HOST_STATE_DONE
