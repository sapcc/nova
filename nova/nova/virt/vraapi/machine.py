from oslo_log import log as logging

import nova.conf
from nova.virt import driver

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF

class Machine(driver.ComputeDriver):

    def __init__(self, virtapi):
        super(Machine, self).__init__(virtapi)

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None):
        pass

    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True):
        pass

    def cleanup(self):
        raise NotImplementedError()

    def inject_network_info(self, instance, nw_info):
        pass

    def power_off(self, instance, timeout=0, retry_interval=0):
        pass

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        pass

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        pass

    def snapshot(self, context, instance, image_id, update_task_state):
        pass

    def resume(self, context, instance, network_info, block_device_info=None):
        pass

    def suspend(self, context, instance):
        pass

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        pass

    def unrescue(self, instance, network_info):
        pass

    def pause(self, instance):
        pass

    def unpause(self, instance):
        pass

    def attach_interface(self, context, instance, image_meta, vif):
        pass

    def detach_interface(self, context, instance, vif):
        pass

    def get_mks_console(self, context, instance):
        pass

    def get_vnc_console(self, context, instance):
        pass

    def get_console_output(self, context, instance):
        pass




