import constants
import json
import nova.conf
import vra_utils
from oslo_config import cfg
from oslo_log import log as logging
from vra_lib import client as vra_client

LOG = logging.getLogger(__name__)

RESOURCE_TRACKER_SLEEP = 5.0

CONF = nova.conf.CONF

class ResourceNotImplemented(Exception):
    pass


class Resource(object):

    def __init__(self, client):
        self.client = client
        self.openstack_id = None
        self.openstack_payload = None
        self.project = None
        self.id = None
        self.revision = None

    def load(self, payload):
        """
        Load Resource from OpenStack openstack_payload
        """
        raise ResourceNotImplemented()

    def fetch(self):
        """
        Load Resource from vRA
        """
        raise ResourceNotImplemented()

    def all(self):
        """
        Fetch a list of all resources
        """
        raise ResourceNotImplemented()

    def all_revisions(self):
        """
        Fetch a list of all resources revisions
        """
        raise ResourceNotImplemented()

    def save(self):
        """
        Create or update resource in vRA
        """
        raise ResourceNotImplemented()

    def delete(self):
        """
        Delete a resource from vRA
        """
        raise ResourceNotImplemented()

    def track(self, resource_track_id):
        tracker = vra_utils.track_status_waiter(self.client, resource_track_id,
                                                RESOURCE_TRACKER_SLEEP)
        if tracker['status'] == 'FAILED':
            LOG.error(tracker['message'])
            raise Exception(tracker['message'])

    def track_deployment(self, deployment_id):
        tracker = vra_utils.track_deployment_waiter(self.client, deployment_id,
                                                RESOURCE_TRACKER_SLEEP)
        if tracker['status'] == 'FAILED':
            LOG.error(tracker['message'])
            raise Exception(tracker['message'])

    def save_and_track(self, path, payload):
        response = self.client.post(
            path=path,
            json=payload
        )

        content = json.loads(response.content)
        self.track(content['id'])

    def delete_and_track(self, path):
        response = self.client.delete(
            path=path
        )

        content = json.loads(response.content)
        self.track(content['id'])

    def get_request_handler(self, path):
        r = self.client.get(
            path=path
        )
        content = json.loads(r.content)
        return content["content"]


class Project(Resource):
    """
    vRA Project class
    """
    def __init__(self, client):
        super(Project, self).__init__(client)


    def fetch(self, project_id):
        """
        Get project
        """
        vra_projects = self.all()
        projId = None
        for proj in vra_projects:
            if 'openstackProjId' in proj['customProperties']:
                if proj['customProperties']['openstackProjId'] == project_id:
                    projId = proj['id']

        if not projId:
            raise ValueError('Project id not found in vRA for id: {}'.format(
                project_id))

        return projId

    def all(self):
        """
        Fetch all available vRA projects

        :return: HTTP Response content
        """
        LOG.info("Fetching vRA Projects...")
        return self.get_request_handler(constants.PROJECTS_GET_API)


class Deployment(Resource):
    """
    vRA Deployment class
    """
    def __init__(self, client):
        super(Deployment, self).__init__(client)


    def fetch(self, search_query=None):
        """
        Get deployment
        """
        LOG.info("Fetching vRA Deployments...")

        path = constants.DEPLOYMENTS_GET_API
        if search_query:
            path = constants.DEPLOYMENTS_GET_API + "?search=" + search_query

        return self.get_request_handler(path)

    def all(self):
        """
        Fetch all vRA deployments

        :return: HTTP Response content
        """
        """
        Get deployments
        """
        LOG.info("Fetching vRA Deployments...")

        path = constants.DEPLOYMENTS_GET_API
        return self.get_request_handler(path)


class DeploymentResource(Resource):
    """
    vRA DeploymentResource class
    """
    def __init__(self, client):
        super(DeploymentResource, self).__init__(client)


    def fetch(self, deployment_id):
        """
        Get deployment
        """
        path = constants.DEPLOYMENT_RESOURCES_API.replace("{deployment_id}",
                                                          deployment_id)
        return self.get_request_handler(path)


class BlockDevice(Resource):
    """
    vRA BlockDevice class
    """
    def __init__(self, client):
        super(BlockDevice, self).__init__(client)


    def fetch(self, device_id):
        """
        Get deployment
        """
        path = constants.BLOCK_DEVICE_API + "?$filter=tags.item.key eq openstack_volume_id" + \
               " and tags.item.value eq {}".format(device_id)
        return self.get_request_handler(path)[0]


class Network(Resource):
    """
    vRA Network class
    """
    def __init__(self, client):
        super(Network, self).__init__(client)


    def fetch(self, device_id):
        """
        Get network
        """
        pass

    def load(self, network_info):
        self.network_info = network_info

    def all(self):
        path = constants.NETWORKS_API
        return self.get_request_handler(path)


class Instance(Resource):
    """
       vRA Instance class
       """
    def __init__(self, client):
        super(Instance, self).__init__(client)

    def fetch(self):
        """
        Get instance
        """
        vra_machine_content = None
        path = constants.MACHINES_API + "?$filter=tags.item.key eq openstack_instance_id" + \
               " and tags.item.value eq {}".format(self.instance.uuid)
        content = self.get_request_handler(path)
        LOG.debug('vRA Machine content: {}'.format(content))
        if len(content) == 0:
            #Let's try to refetch instance by custom property, if tag is missing
            vra_machine_content = self.get_instance_by_cp()
        else:
            vra_machine_content = content[0]
        return vra_machine_content

    def get_instance_by_cp(self):
        path = constants.MACHINES_API + "?$filter=customProperties.openstack_instance_id eq  {}"\
            .format(self.instance.uuid)
        return self.get_request_handler(path)[0]

    def load(self, instance_payload):
        self.instance = instance_payload

    def save(self):
        pass

    def create(self, project_id, image_url, networks):
        """
        Create instance in vRA

        :param project_id: vRA Facade instance
        :param image_url: vRA Facade instance
        :param networks: vRA Facade instance
        :return:
        """

        #TO-DO we need to check if we have multiple tags how we can pass them
        #here and process dynamically
        storage_tag = {
            'key': "Storage",
            'value': project_id
        }

        instance_tag = {
            'key': "openstack_instance_id",
            'value': self.instance.uuid
        }

        instance_payload = {
            "description": self.instance.display_description,
            "tags": [],
            "flavor": self.instance.flavor.name,
            "disks": [],
            "customProperties": {},
            "bootConfig": {
                "content": "_"
            },
            "name": self.instance.display_name,
            "imageRef": image_url,
            "projectId": project_id,
            "storage": {
                "constraints": {
                    "tag": []
                }
            },
            "nics": []
        }

        instance_payload['storage']['constraints']['tag'].append(storage_tag)
        instance_payload['tags'].append(instance_tag)
        instance_payload['customProperties']['openstack_instance_id'] = self.instance.uuid
        instance_payload['customProperties']['awaitIp'] = False #TO-DO remove - for now use for tests
        instance_payload['customProperties']['networkDetails'] = json.dumps(networks)

        self.save_and_track(constants.MACHINES_API, instance_payload)
        LOG.info('vRA Create instance initialized')

    def destroy(self):
        """
        Destroy vRA instance
        :return:
        """
        vra_instance = self.fetch()
        if not vra_instance:
            raise Exception('Instance could not be located in vRA')
        url = '{}{}'.format(constants.MACHINES_API, vra_instance['id'])
        self.delete_and_track(url)

    def snasphot(self, deployment_id, resource_id, image_id):
        path = constants.DEPLOYMENT_RESOURCE_REQUESTS_API.replace("{deployment_id}",
                                                                  deployment_id)
        resource_path = path.replace("{resource_id}", resource_id)
        json_payload = {
            "actionId": "Cloud.vSphere.Machine.Snapshot.Create",
            "inputs": {
                "name": image_id
            }
        }

        r = self.client.post(
            path=resource_path,
            json=json_payload
        )
        content = json.loads(r.content)
        LOG.debug('vRA Snapshot create initialized: {}'.format(content))
        return content

    def power_on(self):
        """
        Power on vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to power on instance: {}'.format(self.instance.display_name))
        vra_instance = self.fetch()
        url = constants.POWER_ON_API.replace("{id}", vra_instance['id'])
        self.save_and_track(url, "")

    def power_off(self):
        """
        Power off vRA instance

        :param instance: Openstack instance
        :return:
        """
        LOG.debug('Attempting to power off instance: {}'.format(self.instance.display_name))
        vra_instance = self.fetch()
        url = constants.POWER_OFF_API.replace("{id}", vra_instance['id'])
        self.save_and_track(url, "")

    def suspend(self):
        """
        Suspend vRA instance
        """
        LOG.debug('Attempting to suspend instance: {}'.format(self.instance.display_name))
        vra_instance = self.fetch()
        url = constants.SUSPEND_API.replace("{id}", vra_instance['id'])
        self.save_and_track(url, "")

    def reboot(self):
        """
        Reboot vRA instance
        """
        LOG.debug('Attempting to reboot instance: {}'.format(self.instance.display_name))
        vra_instance = self.fetch()
        url = constants.REBOOT_API.replace("{id}", vra_instance['id'])
        self.save_and_track(url, "")

    def attach_volume(self, block_device_id, vra_instance_id, volume_id):
        """
        Attach volume to instance

        :param block_device_id: vRA volume id
        :param vra_instance_id: vRA instance id
        :param volume_id: Openstack volume id
        :return:
        """
        path = constants.ATTACH_VOLUME_API.replace("{id}", vra_instance_id)
        json_payload = {
            "blockDeviceId": block_device_id,
            "name": volume_id,
            "description": volume_id
        }
        r = self.client.post(
            path=path,
            json=json_payload
        )
        content = json.loads(r.content)
        LOG.debug('vRA Attach volume initialized: {}'.format(content))

    def detach_volume(self, block_device_id, vra_instance_id):
        """
        Detach volume from instance

        :param block_device_id: vRA volume id
        :param vra_instance_id: vRA instance id
        :return:
        """
        path = constants.DETACH_VOLUME_API.replace("{id}", vra_instance_id)
        disk_path = path.replace("{disk_id}", block_device_id)

        r = self.client.delete(
            path=disk_path
        )
        content = json.loads(r.content)
        LOG.debug('vRA Detach volume initialized: {}'.format(content))
        return content

    def attach_interface(self, project_id, catalog, resource, vif):
        catalog_item = catalog.fetch(constants.CATALOG_ATTACH_INTERFACE)[0]
        inputs = {
            "machineId": resource['customProperties']['instanceUUID'],
            "name": vif['network']['label'],
            "macAddress": vif['address'],
            "openStackSegmentPortId": vif['id']
        }

        catalog.call_catalog_item(project_id, catalog_item['id'],
                                  "Attach {} network".format(vif['id']),
                                  inputs, track=False)

    def detach_interface(self, project_id, catalog, resource, vif):
        catalog_item = catalog.fetch(constants.CATALOG_DETACH_INTERFACE)[0]

        inputs = {
          "machineId": resource['customProperties']['instanceUUID'],
          "macAddress": vif['address']
        }

        catalog.call_catalog_item(project_id, catalog_item['id'],
                                  "Detach {} network".format(vif['id']),
                                  inputs, track=True)


class CatalogItem(Resource):
    """
    vRA CatalogItem class
    """

    def __init__(self, client):
        super(CatalogItem, self).__init__(client)

    def fetch(self, catalog_item_name):
        """
        Get catalog item by name
        """
        path = constants.CATALOG_ITEM_API + "?search=" + catalog_item_name
        return self.get_request_handler(path)

    def all(self):
        """
        Fetch all available vRA catalog items

        :return: HTTP Response content
        """
        path = constants.CATALOG_ITEM_API
        return self.get_request_handler(path)

    def call_catalog_item(self, project_id, catalog_item_id, deployment_name,
                                                    inputs, track=True):
        path = constants.CATALOG_ITEM_REQUEST.replace("{catalog_item_id}",
                                                      catalog_item_id)

        interface_payload = {
            "bulkRequestCount": 1,
            "deploymentName": deployment_name,
            "inputs": inputs,
            "projectId": project_id
        }

        response = self.client.post(
            path=path,
            json=interface_payload
        )

        deployment = json.loads(response.content)
        deployment_id = deployment[0]['deploymentId']
        if track:
            self.track_deployment(deployment_id)


class VraFacada(object):

    def __init__(self):
        vra_config = vra_client.VraClientConfig()
        c = cfg.CONF.VRA

        # TO-DO Maybe we can move this config init outside
        vra_config.host = c.host
        vra_config.port = c.port
        vra_config.username = c.username
        vra_config.password = c.password
        vra_config.organization = c.organization
        vra_config.connection_retries = c.connection_retries
        vra_config.connection_retries_seconds = c.connection_retries_seconds
        vra_config.connection_timeout_seconds = c.connection_timeout_seconds
        vra_config.connection_throttling_rate = c.connection_throttling_rate
        vra_config.connection_throttling_limit_seconds = c.connection_throttling_limit_seconds
        vra_config.connection_throttling_timeout_seconds = c.connection_throttling_timeout_seconds
        vra_config.connection_query_limit = c.connection_query_limit
        vra_config.connection_certificate_check = c.connection_certificate_check
        vra_config.cloud_zone = c.cloud_zone
        vra_config.logger = LOG

        self.client = vra_client.VraClient(vra_config)

    @property
    def instance(self):
        return Instance(self.client)

    @property
    def project(self):
        return Project(self.client)

    @property
    def deployment(self):
        return Deployment(self.client)

    @property
    def deployment_resource(self):
        return DeploymentResource(self.client)

    @property
    def block_device(self):
        return BlockDevice(self.client)

    @property
    def network(self):
        return Network(self.client)

    @property
    def catalog_item(self):
        return CatalogItem(self.client)
