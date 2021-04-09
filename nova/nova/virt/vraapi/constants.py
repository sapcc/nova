"""
vRA REST URL mappings
"""

#Blueprint
LOGIN_API = "/csp/gateway/am/api/login"
BLUEPRINT_REQUESTS_API = "/blueprint/api/blueprint-requests"
BLUEPRINTS_API = "/blueprint/api/blueprints"

#Deployment
DEPLOYMENTS_GET_API = "/deployment/api/deployments/"
DEPLOYMENT_RESOURCES_API = "/deployment/api/deployments/{deployment_id}/resources/"
DEPLOYMENT_RESOURCE_REQUESTS_API = "/deployment/api/deployments/{deployment_id}/resources/{resource_id}/requests/"


#IAAS
MACHINES_API = "/iaas/api/machines/"
PROJECTS_GET_API = "/iaas/api/projects"
NETWORKS_API = "/iaas/api/networks"
RESOURCE_TRACKER_API = "/iaas/api/request-tracker/"
POWER_ON_API = "/iaas/api/machines/{id}/operations/power-on"
POWER_OFF_API = "/iaas/api/machines/{id}/operations/power-off"
ATTACH_VOLUME_API = "/iaas/api/machines/{id}/disks"
DETACH_VOLUME_API = "/iaas/api/machines/{id}/disks/{disk_id}"
BLOCK_DEVICE_API = "/iaas/api/block-devices/"