# Copyright (c) 2023 SAP SE
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
from collections import defaultdict

from nova import context as nova_context
from nova import exception
from nova.objects.aggregate import AggregateList
from nova.objects.compute_node import ComputeNodeList

GARDENER_PREFIX = "kubernetes.io-cluster-"
KKS_PREFIX = "kubernikus:kluster"
VMWARE_HV_TYPE = 'VMware vCenter Server'
SHARD_PREFIX = 'vc-'


def get_sorted_k8s_shard_aggregates(context, metadata, tags, availability_zone,
                                    skip_instance_uuid=None):
    """Returns the shards of a K8S cluster sorted by the instances count.

    The K8S cluster is determined by Instance's metadata or tags.
    Returns None if the cluster is new (first instance is being spawned there)
    or if the K8S metadata/tags are not set.
    """
    kks_tag = None
    gardener_meta = None
    no_ret = None
    if tags:
        kks_tag = next((t.tag for t in tags
                        if t.tag.startswith(KKS_PREFIX)), None)
    if not kks_tag and metadata:
        gardener_meta = \
            {k: v for k, v in metadata.items()
             if k.startswith(GARDENER_PREFIX)}

    if not kks_tag and not gardener_meta:
        return no_ret

    q_filters = {'hv_type': VMWARE_HV_TYPE}
    if availability_zone:
        q_filters['availability_zone'] = availability_zone
    if skip_instance_uuid:
        q_filters['skip_instance_uuid'] = skip_instance_uuid

    results = None
    if kks_tag:
        results = nova_context.scatter_gather_skip_cell0(
            context, ComputeNodeList.get_k8s_hosts_by_instances_tag,
            kks_tag, filters=q_filters)
    elif gardener_meta:
        (meta_key, meta_value) = next(iter(gardener_meta.items()))
        results = nova_context.scatter_gather_skip_cell0(
            context, ComputeNodeList.get_k8s_hosts_by_instances_metadata,
            meta_key, meta_value, filters=q_filters)

    if not results:
        return no_ret

    # hosts with count of instances from this K8S cluster
    # {host: <count>}
    k8s_hosts = defaultdict(lambda: 0)

    for cell_uuid, cell_result in results.items():
        if nova_context.is_cell_failure_sentinel(cell_result):
            raise exception.NovaException(
                "Unable to schedule the K8S instance because "
                "cell %s is not responding." % cell_uuid)
        cell_hosts = dict(cell_result)
        for h, c in cell_hosts.items():
            k8s_hosts[h] += c

    if not k8s_hosts:
        return no_ret

    all_shard_aggrs = [agg for agg in AggregateList.get_all(context)
                       if agg.name.startswith(SHARD_PREFIX)]

    return sorted(
        all_shard_aggrs,
        reverse=True,
        key=lambda aggr: sum(i for h, i in k8s_hosts.items()
                             if h in aggr.hosts))
