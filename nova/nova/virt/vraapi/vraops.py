import nova.conf
import nova.privsep.path
import vra_facada


from nova import image
from nova.compute import task_states
from nova.image import glance
from nova.virt.vmwareapi import constants
from oslo_log import log as logging

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

class VraOps(object):

    def __init__(self):
        """
        Service for all vRA operations
        """
        self.vra = vra_facada.VraFacada()
        self.vra.client.login()

    def iaas_spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info, block_device_info=None):
        """
        Spawn vRA instance
        """

        LOG.info("Spawn instance with network: {}".format(network_info))
        image_url = self.__get_image_url(context, image_meta)

        project = self.vra.project
        project_id = project.fetch(instance.project_id)

        network = self.__build_vra_network_cp(network_info)

        os_instance = self.vra.instance
        os_instance.load(instance)
        os_instance.create(project_id, image_url, network)

    def snapshot(self, context, instance, image_id, update_task_state):
        """
        Create instance snapshot
        """
        deployment = self.vra.deployment
        deployment = deployment.fetch(search_query=instance.display_name)[0]

        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)
        deployment_resource = self.vra.deployment_resource
        resource = deployment_resource.fetch(deployment['id'])[0]

        os_instance = self.vra.instance
        os_instance.snasphot(deployment['id'], resource['id'], image_id)

        update_task_state(task_state=task_states.IMAGE_UPLOADING,
                          expected_state=task_states.IMAGE_PENDING_UPLOAD)
        self.image_api = image.API()
        image_ref = self.image_api.get(context, image_id)
        if image_ref['status'] != 'active':
            image_metadata = {'disk_format': constants.DISK_FORMAT_VMDK,
                              'is_public': True,
                              'name': image_ref['name'],
                              'status': 'active',
                              'container_format': constants.CONTAINER_FORMAT_BARE,
                              'size': 0,
                              'properties': {'vmware_image_version': 1,
                                             'vmware_disktype': 'streamOptimized',
                                             'owner_id': instance.project_id}}
            self.image_api.update(context, image_id, image_metadata, data="")

    def power_on(self, instance):
        """
        Power on vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to power on instance: {}'.format(instance.display_name))

        os_instance = self.vra.instance
        os_instance.load(instance)
        os_instance.power_on()

    def power_off(self, instance):
        """
        Power off vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to power off instance: {}'.format(instance.display_name))
        os_instance = self.vra.instance
        os_instance.load(instance)
        os_instance.power_off()

    def suspend(self, instance):
        """
        Suspend vRA instance

        :param instance: Openstack instance
        :return:
        """
        os_instance = self.vra.instance
        os_instance.load(instance)
        os_instance.suspend()

    def reboot(self, instance):
        """
        Reboot vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to reboot instance: {}'.format(instance.display_name))
        os_instance = self.vra.instance
        os_instance.load(instance)
        os_instance.reboot()

    def destroy(self, instance):
        """
        Destroy vRA instance

        :param instance: Openstack instance
        :return:
        """
        os_instance = self.vra.instance
        os_instance.load(instance)
        os_instance.destroy()

    def __get_image_url(self, context, image_meta):
        """
        Fetch IMAGE direct url
        :param context: context
        :param image_meta: image meta data
        :return: image direct url
        """
        glance_client = glance.GlanceImageServiceV2()
        image_url = glance_client.show(context, image_meta.id, True, False). \
            get('direct_url', 'wwcoe / Ubuntu_18.04_x64_minimal')
        return image_url

    def __build_vra_network_resource(self, instance_template, network_info):
        """
        Build nic payload for vRA machine request
        :param instance_template: static instance dict template
        :param network_info: openstack instance network info
        """

        os_network = self.vra.network
        os_network.load(network_info)
        vra_networks = os_network.all()
        LOG.debug('vRA Networks fetched: {}'.format(vra_networks))

        network_result = []

        for index, net_info in enumerate(network_info):
            mac_addr = {"deviceIndex": index, "macAddress": net_info['address']}
            net_id = net_info['network']['id']
            network_id = None
            for vra_net in vra_networks:
                if 'tags' in vra_net:
                    for tag in vra_net['tags']:
                        if tag['key'] == "openstack_network_id" and\
                                                    tag['value'] == net_id:
                            network_id = vra_net['id']

            if not network_id:
                raise Exception('Network with id: {} was not found in vRA'.format(net_id))

            mac_cp = {
                "nicsMacAddresses": []
            }

            mac_cp['nicsMacAddresses'].append(mac_addr)

            ip_addr = net_info['network']['subnets'][0]['ips'][0]['address']
            nic_name = net_info['network']['label']
            instance_template['customProperties'] = mac_cp

            nic = {
                "addresses": [ip_addr],
                "name": "nic{}".format(index),
                "description": "Network",
                "networkId": network_id,
                "deviceIndex": index,
                "customProperties": {
                    "openstack_port_id": net_info['id']
                }
            }

            network_result.append(nic)

        return network_result

    def __build_vra_network_cp(self, network_info):
        """
        Build network custom properties payload for vRA machine request
        :param network_info: openstack instance network info
        """
        networks = []
        for index, net_info in enumerate(network_info):
            net_id = net_info['network']['id']

            #TO-DO remove hardcoded networkId - we should fetch the network from vRA
            network_details = {
                "deviceIndex": index,
                "networkId": "5388a304-9ede-4221-bb18-69345466256e",
                "openstack_network_id": net_id,
                "openstack_network_port_id": net_info['id'],
                "macAddress": net_info['address']
            }

            networks.append(network_details)

        return networks


    def attach_volume(self, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        """Attach volume storage to VM instance."""

        os_instance = self.vra.instance
        os_instance.load(instance)
        vra_vm_resource = os_instance.fetch()

        bd = self.vra.block_device
        block_device = bd.fetch(connection_info['volume_id'])
        LOG.debug("Block device found: {}".format(block_device))

        os_instance.attach_volume(block_device['id'],
                                    vra_vm_resource['id'],
                                    connection_info['volume_id'])

    def detach_volume(self, connection_info, instance, mountpoint,
                  disk_bus=None, device_type=None, encryption=None):
        """Detach volume storage from VM instance."""

        os_instance = self.vra.instance
        os_instance.load(instance)
        vra_vm_resource = os_instance.fetch()

        bd = self.vra.block_device
        block_device = bd.fetch(connection_info['volume_id'])
        LOG.debug("Block device found: {}".format(block_device))

        os_instance.detach_volume(block_device['id'],
                                  vra_vm_resource['id'])
