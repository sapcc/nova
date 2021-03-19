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
        LOG.debug(50 * "=", "vRA Driver Initialized", 50 * "=")
        self.vraops = vraops.VraOps()

    def get_available_nodes(self, refresh=False):
        """No nodes are returned in case of vRA driver"""
        return []

    def get_available_resource(self, nodename):
        pass

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None):
        LOG.debug('Spawning instance from vRA driver')

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
