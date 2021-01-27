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
from nova.virt.vmwareapi import constants
from nova.virt.vmwareapi import vim_util
from nova.virt.vmwareapi.vm_util import propset_dict

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF
FREE_HOST_STATE_DONE = 'done'
FREE_HOST_STATE_ERROR = 'error'
FREE_HOST_STATE_STARTED = 'started'

BIGVM_RESOURCE = 'CUSTOM_BIGVM'


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
            # we are only interested in one special group
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
            if 'config.instanceUuid' not in vm_props:
                # sometimes, the vCenter finds a file it thinks is a VM and it
                # doesn't even have a config attribute ... instead of crashing
                # with a KeyError, we assume this VM is not running and totally
                # doesn't matter as nova also will not be able to handle it
                continue

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

        # check if DRS is enabled, so freeing up can work
        if cluster_config.drsConfig.defaultVmBehavior != \
                constants.DRS_BEHAVIOR_FULLY_AUTOMATED:
            LOG.error('DRS set to %(actual)s, expected %(expected)s.',
                      {'actual': cluster_config.drsConfig.defaultVmBehavior,
                       'expected': constants.DRS_BEHAVIOR_FULLY_AUTOMATED})
            return FREE_HOST_STATE_ERROR

        # get the group
        group = self._get_group(cluster_config)

        failover_hosts = []
        policy = cluster_config.dasConfig.admissionControlPolicy
        if policy and hasattr(policy, 'failoverHosts'):
            failover_hosts = set(h.value for h in policy.failoverHosts)

        if group is None or not getattr(group, 'host', None):
            # find a host to free

            # get all the vms in a cluster, because we need to find a host
            # without big VMs.
            props = ['config.hardware.memoryMB', 'runtime.host',
                     'runtime.powerState',
                     'summary.quickStats.hostMemoryUsage']
            cluster_vms = self._vmops._list_instances_in_cluster(props)
            if not cluster_vms:
                LOG.error('Got no VMs for freeing a host for spawning. '
                          'Treating as error instead of continuing, as an '
                          'empty cluster is unlikely.')
                return FREE_HOST_STATE_ERROR

            host_objs = {}
            vms_per_host = {}
            for vm_uuid, vm_props in cluster_vms:
                props = (vm_props.get('config.hardware.memoryMB', 0),
                         vm_props.get('runtime.powerState', 'poweredOff'),
                         vm_props.get('summary.quickStats.hostMemoryUsage', 0))
                # every host_obj is differnt, even though the value, which
                # really matters, is the same
                host_obj = vm_props.get('runtime.host')
                if not host_obj:
                    continue

                host = host_obj.value
                host_objs.setdefault(host, host_obj)
                vms_per_host.setdefault(host, []). \
                        append(props)

            # filter for hosts without big VMs
            # TODO(jkulik) Filter for hosts having no VM with DRS
            # partiallyAutomated
            vms_per_host = {h: vms for h, vms in vms_per_host.items()
                            if all(mem < CONF.largevm_mb
                                   for mem, state, used_mem in vms)}

            if not vms_per_host:
                LOG.warning('No suitable host found for freeing a host for '
                            'spawning (bigvm filter).')
                return FREE_HOST_STATE_ERROR

            # filter hosts which are failover hosts
            vms_per_host = {h: vms for h, vms in vms_per_host.items()
                            if h not in failover_hosts}

            if not vms_per_host:
                LOG.warning('No suitable host found for freeing a host for '
                            'spawning (failover host filter).')
                return FREE_HOST_STATE_ERROR

            # filter hosts which are in a wrong state
            result = self._session._call_method(vim_util,
                         "get_properties_for_a_collection_of_objects",
                         "HostSystem",
                         [host_objs[h] for h in vms_per_host],
                         ['summary.runtime'])
            host_states = {}
            for obj in result.objects:
                host_props = propset_dict(obj.propSet)
                runtime_summary = host_props['summary.runtime']
                host_states[obj.obj.value] = (
                    runtime_summary.inMaintenanceMode is False and
                    runtime_summary.connectionState == "connected")

            vms_per_host = {h: vms for h, vms in vms_per_host.items()
                            if host_states[h]}

            if not vms_per_host:
                LOG.warning('No suitable host found for freeing a host for '
                            'spawning (host state filter).')
                return FREE_HOST_STATE_ERROR

            mem_per_host = {h: sum(used_mem for mem, state, used_mem in vms)
                            for h, vms in vms_per_host.items()}

            # take the one with least memory used
            host, _ = sorted(mem_per_host.items(), key=lambda (x, y): y)[0]
            host_ref = host_objs[host]

            client_factory = self._session.vim.client.factory
            config_spec = client_factory.create('ns0:ClusterConfigSpecEx')

            # we need to either create the group from scratch or at least add a
            # host to it
            operation = 'add' if group is None else 'edit'
            group_spec = cluster_util._create_host_group_spec(client_factory,
                CONF.vmware.bigvm_deployment_free_host_hostgroup_name,
                [host_ref], operation, group)
            config_spec.groupSpec = [group_spec]

            # create the appropriate rule for VMs to leave the host
            if group is None:
                rule_name = '{}_anti-affinity'.format(
                    CONF.vmware.bigvm_deployment_free_host_hostgroup_name)
                rule = cluster_util._get_rule(cluster_config, rule_name)
                rule_spec = cluster_util._create_cluster_group_rules_spec(
                    client_factory, rule_name,
                    CONF.vmware.special_spawning_vm_group,
                    CONF.vmware.bigvm_deployment_free_host_hostgroup_name,
                    'anti-affinity', rule)
                config_spec.rulesSpec = [rule_spec]

            cluster_util.reconfigure_cluster(self._session, self._cluster,
                                             config_spec)
        else:
            if len(group.host) > 1:
                LOG.warning('Found more than 1 host in spawning hostgroup.')
            host_ref = group.host[0]

            # check if the host is still suitable
            if host_ref.value in failover_hosts:
                LOG.warning('Host destined for spawning became a failover '
                            'host.')
                return FREE_HOST_STATE_ERROR

            runtime_summary = self._session._call_method(
                vutil, "get_object_property", host_ref, 'summary.runtime')
            if (runtime_summary.inMaintenanceMode is True or
                    runtime_summary.connectionState != "connected"):
                LOG.warning('Host destined for spawning was set to '
                            'maintenance or became disconnected.')
                return FREE_HOST_STATE_ERROR

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
