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

"""VIF drivers for VMware."""

from oslo_log import log as logging
from oslo_utils import versionutils
from oslo_vmware import vim_util

import nova.conf
from nova import exception
from nova.i18n import _
from nova.network import model
from nova.virt.vmwareapi import constants
from nova.virt.vmwareapi import network_util
from nova.virt.vmwareapi import vm_util


LOG = logging.getLogger(__name__)


CONF = nova.conf.CONF

# VMware VIF models in priority order (most preferred first)
SUPPORTED_VIF_MODELS = (
    model.VIF_MODEL_VMXNET3,
    model.VIF_MODEL_VMXNET,
    model.VIF_MODEL_E1000E,
    model.VIF_MODEL_E1000,
    model.VIF_MODEL_PCNET,
    model.VIF_MODEL_SRIOV,
)


def _get_supported_vif_model(image_meta):
    """Get the best supported VIF model from image metadata.

    :param image_meta: ImageMeta object containing image properties
    :returns: The first matching model from SUPPORTED_VIF_MODELS, or None
    """
    if not image_meta:
        return None

    supported_models = image_meta.properties.get('hw_supported_vif_models')
    if not supported_models:
        return None

    for candidate_model in SUPPORTED_VIF_MODELS:
        if candidate_model in supported_models:
            return candidate_model

    return None


def get_vif_model(image_meta):
    """Get the VIF model from image metadata.

    This function tries to determine the VIF model based on the image
    metadata, if provided.

    It checks hw_supported_vif_models first (selecting the first supported
    model from the priority list), then falls back to hw_vif_model.

    If either no image_meta has been provided, or the metadata
    has not been set, it falls back to the default model.
    :param image_meta: ImageMeta object containing image properties
    :returns: The VIF model to use (string)
    """
    if not image_meta:
        return constants.DEFAULT_VIF_MODEL

    # First, check if hw_supported_vif_models is specified
    if vif_model := _get_supported_vif_model(image_meta):
        return vif_model

    # Fall back to hw_vif_model if specified
    if vif_model := image_meta.properties.get('hw_vif_model'):
        return vif_model

    # Return default model
    return constants.DEFAULT_VIF_MODEL


def _check_ovs_supported_version(session):
    # The port type 'ovs' is only support by the VC version 5.5 onwards
    min_version = versionutils.convert_version_to_int(
        constants.MIN_VC_OVS_VERSION)
    vc_version = versionutils.convert_version_to_int(
        vim_util.get_vc_version(session))
    if vc_version < min_version:
        LOG.warning('VMware vCenter version less than %(version)s '
                    'does not support the \'ovs\' port type.',
                    {'version': constants.MIN_VC_OVS_VERSION})


def get_network_ref(session, cluster, vif):
    if vif['type'] == model.VIF_TYPE_OVS:
        _check_ovs_supported_version(session)
        # Check if this is the NSX-MH plugin is used
        if CONF.vmware.integration_bridge:
            net_id = CONF.vmware.integration_bridge
            use_external_id = False
            network_type = 'opaque'
        else:
            # The NSX|V3 plugin will pass the nsx-logical-switch-id as part
            # of the port details. This will enable the VC to connect to
            # that specific opaque network
            net_id = (vif.get('details') and
                      vif['details'].get('nsx-logical-switch-id'))
            if not net_id:
                # Make use of the original one, in the event that the
                # plugin does not pass the aforementioned id
                LOG.info('NSX Logical switch ID is not present. '
                         'Using network ID to attach to the '
                         'opaque network.')
                net_id = vif['network']['id']
            use_external_id = True
            network_type = 'nsx.LogicalSwitch'
        network_ref = {'type': 'OpaqueNetwork',
                       'network-id': net_id,
                       'network-type': network_type,
                       'use-external-id': use_external_id}
    elif vif['type'] == model.VIF_TYPE_DVS:
        # Port binding for DVS VIF types may pass the name
        # of the port group, so use it if present
        vif_details = vif.get('details', {})

        dvs_uuid = vif_details.get('dvs_id')
        dvs_port_group_key = vif_details.get('pg_id')
        if dvs_uuid and dvs_port_group_key:
            network_ref = {
                'type': 'DistributedVirtualPortgroup',
                'dvpg': dvs_port_group_key,
                'dvsw': dvs_uuid
            }
        else:
            network_id = vif_details.get('dvs_port_group_name')
            if network_id is None:
                # Make use of the original one, in the event that the
                # port binding does not provide this key in VIF details
                network_id = vif['network']['bridge']
            network_ref = network_util.get_network_with_the_name(
                    session, network_id, cluster)
        if not network_ref:
            raise exception.NetworkNotFoundForBridge(bridge=network_id)
        if vif_details.get('dvs_port_key'):
            network_ref['dvs_port_key'] = vif_details['dvs_port_key']
    else:
        reason = _('vif type %s not supported') % vif['type']
        raise exception.InvalidInput(reason=reason)
    return network_ref


def get_vif_dict(session, cluster, vif_model, vif):
    mac = vif['address']
    name = vif['network']['bridge'] or CONF.vmware.integration_bridge
    ref = get_network_ref(session, cluster, vif)
    return {'network_name': name,
            'mac_address': mac,
            'network_ref': ref,
            'iface_id': vif['id'],
            'vif_model': vif_model}


def get_vif_info(session, cluster, vif_model, network_info):
    vif_infos = []
    if network_info is None:
        return vif_infos
    for vif in network_info:
        vif_infos.append(get_vif_dict(session, cluster, vif_model, vif))
    return vif_infos


def get_network_device(hardware_devices, mac_address):
    """Return the network device with MAC 'mac_address'."""
    for device in hardware_devices:
        if device.__class__.__name__ in vm_util.ALL_SUPPORTED_NETWORK_DEVICES:
            if hasattr(device, 'macAddress'):
                if device.macAddress == mac_address:
                    return device
