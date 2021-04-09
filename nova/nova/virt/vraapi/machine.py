from oslo_log import log as logging

import nova.conf
from nova.virt import driver
from nova.virt.vraapi import vraops

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF

class Machine(driver.ComputeDriver):

    def __init__(self, virtapi):
        super(Machine, self).__init__(virtapi)
        self.vraops = vraops.VraOps()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info,
              block_device_info=None):
        LOG.debug("instance: {}, image_meta: {}, injected_files: {}, admin_password: {},"
                  " allocations: {}, network_info: {}, block_device_info: {}".
                  format(instance, image_meta, injected_files,
                  admin_password, allocations, network_info,
                  block_device_info))
        self.vraops.iaas_spawn(context, instance, image_meta, injected_files,
                          admin_password, network_info, block_device_info)


    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True):
        LOG.debug("instance: {}".format(instance))
        self.vraops.destroy(instance)

    def cleanup(self):
        raise NotImplementedError()

    def inject_network_info(self, instance, nw_info):
        LOG.debug("instance: {}, nw_info: {}".format(instance, nw_info))

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        LOG.debug("instance: {}, network_info: {}, reboot_type: {}, block_device_info: {}"
                  "bad_volumes_callback".format(instance, network_info, reboot_type,
               block_device_info, bad_volumes_callback))

    def snapshot(self, context, instance, image_id, update_task_state):
        LOG.debug("instance: {}, image_id: {}, update_task_state: {}".
                  format(instance, image_id, update_task_state))
        self.vraops.snapshot(context, instance, image_id, update_task_state)

    def power_off(self, instance, timeout=0, retry_interval=0):
        """Power off the specified instance."""
        LOG.debug("instance: {}".format(instance))
        self.vraops.power_off(instance)

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        """Power on the specified instance."""
        LOG.debug("instance: {}, network_info: {}, block_device_info:{}".
                  format(instance, network_info, block_device_info))
        self.vraops.power_on(instance)

    def resume(self, context, instance, network_info, block_device_info=None):
        LOG.debug("instance: {}, network_info: {}, block_device_info: {}".format(
            instance, network_info, block_device_info))

    def suspend(self, context, instance):
        LOG.debug("instance: {}".format(instance))

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        LOG.debug("instance: {}, network_info: {}, image_meta: {}, rescue_password: {}".
                                                  format(instance,
                                                  network_info,
                                                  image_meta,
                                                  rescue_password))

    def unrescue(self, instance, network_info):
        LOG.debug("instance: {}, network_info: {}".format(instance, network_info))

    def pause(self, instance):
        LOG.debug("instance: {}".format(instance))

    def unpause(self, instance):
        LOG.debug("instance: {}".format(instance))

    def attach_interface(self, context, instance, image_meta, vif):
        LOG.debug("instance: {}, image_meta: {}, vif: {}".format(instance,
                                                                image_meta,
                                                                vif))

    def detach_interface(self, context, instance, vif):
        LOG.debug("instance: {}, vif: {}".format(instance, vif))

    def get_mks_console(self, context, instance):
        LOG.debug("instance: {}".format(instance))

    def get_vnc_console(self, context, instance):
        LOG.debug("instance: {}".format(instance))

    def get_console_output(self, context, instance):
        LOG.debug("instance: {}".format(instance))
