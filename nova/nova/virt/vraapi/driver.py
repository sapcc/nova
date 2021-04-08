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
A connection to the VMware vRA platform.
"""
from oslo_log import log as logging
import nova.conf
from nova.i18n import _
import nova.privsep.path
from nova.virt import hardware
from nova.virt.vraapi import machine
from nova.virt.vraapi import vraops
#from config import volume_config

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

class VMwareVRADriver(machine.Machine):
    """The VRA host connection object."""

    def __init__(self, virtapi, scheme="https"):
        super(VMwareVRADriver, self).__init__(virtapi)

        if (CONF.vmware.host_ip is None or
                CONF.vmware.host_username is None or
                CONF.vmware.host_password is None):
            raise Exception(_("Must specify host_ip, host_username and "
                              "host_password to use vraapi.VMwareVRADriver"))

    def init_host(self, host):
        self.vraops = vraops.VraOps()

    def get_info(self, instance):
        return hardware.InstanceInfo(
            state=instance.power_state)

    def get_available_nodes(self, refresh=False):
        """No nodes are returned in case of vRA driver"""
        return []

    def get_available_resource(self, nodename):
        pass

    def get_info(self, instance):
        return hardware.InstanceInfo(
            state=instance.power_state)

    def get_host_ip_addr(self):
        raise NotImplementedError()

    def get_num_instances(self):
        return []

    def list_instances(self):
        return []

    def get_host_uptime(self):
        raise NotImplementedError()

    def host_maintenance_mode(self, host, mode):
        raise NotImplementedError()

    def host_power_action(self, action):
        raise NotImplementedError()

    def set_host_enabled(self, enabled):
        raise NotImplementedError()

    def update_provider_tree(self, provider_tree, nodename):
        raise NotImplementedError()

    def manage_image_cache(self, context, all_instances):
        pass

    def poll_rebooting_instances(self, timeout, instances):
        raise NotImplementedError()

    @property
    def need_legacy_block_device_info(self):
        return True

    def cleanup_host(self, host):
        pass

    def get_volume_connector(self, instance):
        """Return volume connector information."""
        connector = {'host': CONF.vmware.host_ip}
        connector['instance'] = instance.uuid
        connector['connection_capabilities'] = ['vmware_service_instance_uuid:%s' %
                                                instance.uuid]
        return connector

    def attach_volume(self, context, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        """Attach volume storage to VM instance."""
        self.vraops.attach_volume(connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None)

    def detach_volume(self, context, connection_info, instance, mountpoint,
                      encryption=None):
        """Detach volume storage to VM instance."""
        # NOTE(claudiub): if context parameter is to be used in the future,
        # the _detach_instance_volumes method will have to be updated as well.
        self.vraops.detach_volume(connection_info, instance, mountpoint,
                                  disk_bus=None, device_type=None, encryption=None)