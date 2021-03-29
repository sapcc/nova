import json
import nova.conf
import requests
import nova.privsep.path
import synchronization as sync

from nova import image
from nova.compute import task_states
from nova.image import glance
from nova.virt import hardware
from nova.virt.vmwareapi import constants
from oslo_log import log as logging
from VraRestClient import VraRestClient
from vra_templates.instance import InstanceTemplate

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

class VraOps(object):

    def __init__(self):
        """
        Service for all vRA operations
        """
        self.vra_host = CONF.vmware.host_ip
        self.vra_username = CONF.vmware.host_username
        self.vra_password = CONF.vmware.host_password

        self.api_scheduler = sync.Scheduler(rate=2,
                                       limit=1)
        self.vraClient = VraRestClient(self.api_scheduler, "https://" + self.vra_host,
                                  self.vra_username, self.vra_password, "System Domain")

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info, block_device_info=None):

        image_url = self.__get_image_url(context, image_meta)
        topology = hardware.get_best_cpu_topology(instance.flavor, image_meta,
                                                  allow_threads=False)


        project_id = self.__get_project_id(instance)
        LOG.info("Found vRA project id: {}".format(project_id))
        bp_name = "Standalone Server Mmidolesov"
        blueprint = self.vraClient.getBlueprint(bp_name)

        if not blueprint:
            raise ValueError("Blueprint id not found for name {}".format(bp_name))

        inputs = {
                    'name': instance.display_name,
                    'cpuCount': instance.vcpus,
                    'coreCount': topology.cores,
                    'memory': instance.memory_mb,
                    'imageRef': 'wwcoe / Ubuntu_18.04_x64_minimal', #mock image for now - later use image_url
                    'bootDiskCapacityInGB': instance.root_gb,
                    "cloudConfig": "| ssh_pwauth: yes"
                }

        self.vraClient.blueprintRequest(blueprint["id"],
                                   "1",
                                   instance.display_name,
                                   inputs, project_id)


    def iaas_spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info, block_device_info=None):
        """
        Spawn vRA instance
        """

        LOG.info("Spawn instance with network: {}".format(network_info))
        image_url = self.__get_image_url(context, image_meta)
        project_id = self.__get_project_id(instance)

        instance_template = InstanceTemplate.instance_template()
        instance_template['description'] = instance.display_description
        instance_template['flavor'] = instance.flavor.name
        instance_template['name'] = instance.display_name
        instance_template['imageRef'] = "wwcoe / smallVM" #Mock for now - use image_url
        instance_template['projectId'] = project_id
        instance_template['bootConfig']['content'] = "#cloud-config\nrepo_update: true\nrepo_upgrade: all\n\npackages:\n - mysql-server\n\nruncmd:\n - sed -e '/bind-address/ s/^#*/#/' -i /etc/mysql/mysql.conf.d/mysqld.cnf\n - service mysql restart\n - mysql -e \"GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' IDENTIFIED BY 'mysqlpassword';\"\n - mysql -e \"FLUSH PRIVILEGES;\"\n"

        storage_tag = {
            'key': "Storage",
            'value': project_id
        }

        instance_tag = {
            'key': "openstack_instance_id",
            'value': instance.uuid
        }

        self.__build_vra_network_resource(instance_template, network_info)

        instance_template['storage']['constraints']['tag'].append(storage_tag)
        instance_template['tags'].append(instance_tag)
        instance_template['customProperties']['nicsMacAddresses'] = \
            json.dumps(instance_template['customProperties']['nicsMacAddresses'])
        instance_template['customProperties']['openstack_instance_id'] = instance.uuid
        self.vraClient.iaasMachineRequest(instance_template)

    def snapshot(self, context, instance, image_id, update_task_state):
        """
        Create instance snapshot
        """

        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)
        deployment = self.vraClient.getVraDeployments(search_query=instance.display_name)[0]
        resource = self.vraClient.getVraResourceByDeploymentId(deployment['id'])[0]
        self.vraClient.snapshotRequest(deployment['id'], resource['id'], image_id)
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
        vra_instance = self.vraClient.get_vra_machine(instance)
        if not vra_instance:
            raise Exception('Instance could not be located in vRA')
        self.vraClient.power_on_instance(vra_instance['id'])

    def power_off(self, instance):
        """
        Power off vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to power off instance: {}'.format(instance.display_name))
        vra_instance = self.vraClient.get_vra_machine(instance)
        if not vra_instance:
            raise Exception('Instance could not be located in vRA')
        self.vraClient.power_off_instance(vra_instance['id'])

    def destroy(self, instance):
        """
        Destroy vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to destroy instance: {}'.format(instance.display_name))
        vra_instance = self.vraClient.get_vra_machine(instance)
        if not vra_instance:
            raise Exception('Instance could not be located in vRA')
        self.vraClient.destroy(vra_instance['id'])

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

    def __get_project_id(self, instance):
        """
        Get vRA project id by openstack instance project id
        :param instance: openstack instance
        :return: vRA project id
        """
        vra_project = self.vraClient.fetchVraProjects()
        projId = None
        for proj in vra_project:
            if proj['customProperties']['openstackProjId'] == instance.project_id:
                projId = proj['id']

        if not projId:
            raise ValueError('Project id not found in vRA for id: {}'.format(
                instance.project_id))

        return projId

    def __build_vra_network_resource(self, instance_template, network_info):
        """
        Build nic payload for vRA machine request
        :param instance_template: static instance dict template
        :param network_info: openstack instance network info
        """
        for index, net_info in enumerate(network_info):
            mac_addr = {"deviceIndex": index, "macAddress": net_info['address']}
            net_id = net_info['network']['id']
            vra_networks = self.vraClient.getVraNetworks()
            LOG.debug('vRA Networks fetched: {}'.format(vra_networks))
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

            instance_template['nics'].append(nic)
