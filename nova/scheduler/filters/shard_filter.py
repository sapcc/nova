# Copyright (c) 2019 SAP SE
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
from oslo_log import log as logging

import nova.conf
from nova import context as nova_context
from nova.objects.build_request import BuildRequest
from nova.objects.instance import Instance
from nova.scheduler import filters
from nova.scheduler.mixins import ProjectTagMixin
from nova.scheduler import utils
from nova import utils as nova_utils
from nova.virt.vmwareapi import shard_util

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

GARDENER_PREFIX = "kubernetes.io-cluster-"
KKS_PREFIX = "kubernikus:kluster"
HANA_PREFIX = "hana_"
VMWARE_HV_TYPE = 'VMware vCenter Server'


class ShardFilter(filters.BaseHostFilter, ProjectTagMixin):
    """Filter hosts based on the vcenter-shard configured in their aggregate
    and the vcenter-shards configured in the project's tags in keystone. They
    have to overlap for a host to pass this filter.

    Alternatively the project may have the "sharding_enabled" tag set, which
    enables the project for hosts in all shards.

    Implements `filter_all` directly instead of `host_passes`
    """

    _ALL_SHARDS = "sharding_enabled"
    _SHARD_PREFIX = 'vc-'
    _PROJECT_TAG_TAGS = [_ALL_SHARDS]
    _PROJECT_TAG_PREFIX = _SHARD_PREFIX

    def _get_shards(self, project_id):
        """Return a set of shards for a project or None"""
        # NOTE(jkulik): We wrap _get_tags() here to change the name to
        # _get_shards() so it's clear what we return
        return self._get_tags(project_id)

    def _get_k8s_shard(self, spec_obj):
        """Returns the dominant shard of a K8S cluster.

        Returns None in any of the following scenarios:
        - the request is not for an instance that's part of a K8S cluster
        - this is the first instance of a new cluster
        - the request is for a HANA flavor
        - the request is for a resize/migration
        """
        if (spec_obj.flavor.name.startswith(HANA_PREFIX) or
                utils.request_is_resize(spec_obj)):
            return None
        elevated = nova_context.get_admin_context()
        build_request = None
        instance = None

        def _get_tags():
            return build_request.tags if build_request \
                else instance.tags

        def _get_metadata():
            return build_request.instance.metadata if build_request \
                else instance.metadata

        check_type = spec_obj.get_scheduler_hint('_nova_check_type')
        if not check_type:
            build_request = BuildRequest.get_by_instance_uuid(
                elevated, spec_obj.instance_uuid)
        if not build_request:
            instance = Instance.get_by_uuid(
                elevated, spec_obj.instance_uuid,
                expected_attrs=['tags', 'metadata'])
        if not instance and not build_request:
            LOG.warning("There were no build_request and no instance "
                        "for the uuid %s", spec_obj.instance_uuid)
            return

        k8s_shard_aggrs = shard_util.get_sorted_k8s_shard_aggregates(
            elevated, _get_metadata(), _get_tags(), spec_obj.availability_zone)

        if not k8s_shard_aggrs:
            return None

        return k8s_shard_aggrs[0].name

    def filter_all(self, filter_obj_list, spec_obj):
        # Only VMware
        if utils.is_non_vmware_spec(spec_obj):
            LOG.debug("ShardFilter is not applicable for this non-vmware "
                      "request")
            return filter_obj_list

        k8s_shard = self._get_k8s_shard(spec_obj)

        return [host_state for host_state in filter_obj_list
                if self._host_passes(host_state, spec_obj, k8s_shard)]

    def _host_passes(self, host_state, spec_obj, k8s_shard):
        host_shard_aggrs = [aggr for aggr in host_state.aggregates
                            if aggr.name.startswith(self._SHARD_PREFIX)]

        host_shard_names = set(aggr.name for aggr in host_shard_aggrs)
        if not host_shard_names:
            log_method = (LOG.debug if nova_utils.is_baremetal_host(host_state)
                          else LOG.error)
            log_method('%(host_state)s is not in an aggregate starting with '
                       '%(shard_prefix)s.',
                       {'host_state': host_state,
                        'shard_prefix': self._SHARD_PREFIX})
            return False

        project_id = spec_obj.project_id

        shards = self._get_shards(project_id)
        if shards is None:
            LOG.error('Failure retrieving shards for project %(project_id)s.',
                      {'project_id': project_id})
            return False

        if not len(shards):
            LOG.error('Project %(project_id)s is not assigned to any shard.',
                      {'project_id': project_id})
            return False

        if self._ALL_SHARDS in shards:
            LOG.debug('project enabled for all shards %(project_shards)s.',
                      {'project_shards': shards})
        elif host_shard_names & set(shards):
            LOG.debug('%(host_state)s shard %(host_shard)s found in project '
                      'shards %(project_shards)s.',
                      {'host_state': host_state,
                       'host_shard': host_shard_names,
                       'project_shards': shards})
        else:
            LOG.debug('%(host_state)s shard %(host_shard)s not found in '
                      'project shards %(project_shards)s.',
                      {'host_state': host_state,
                       'host_shard': host_shard_names,
                       'project_shards': shards})
            return False

        if k8s_shard:
            if k8s_shard not in host_shard_names:
                LOG.debug("%(host_state)s is not part of the K8S "
                          "cluster's shard '%(k8s_shard)s'",
                          {'host_state': host_state,
                           'k8s_shard': k8s_shard})
                return False

        return True
