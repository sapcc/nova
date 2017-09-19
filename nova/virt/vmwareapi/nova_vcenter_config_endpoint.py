from oslo_config import cfg
import oslo_messaging as messaging
import nova.conf
from nova.virt.vmwareapi import vm_util
from oslo_log import log as logging
from nova.virt.vmwareapi import driver
from oslo_vmware import vim_util as vutil
from nova import exception
from nova.virt.vmwareapi import vim_util
import json

CONF = nova.conf.CONF

transport = messaging.get_transport(cfg.CONF)
target = messaging.Target(topic='testme', server='192.168.56.102')

LOG = logging.getLogger(__name__)

class NovaVcenterConfigEndpoint(object):
    def retrieve_vcenter_config_values(self, session, instance_values):

        LOG.debug("INSTANCE_VALUES: %s", instance_values)

        vcenter_mapper = dict()
        vcenter_mapper['host_password'] = CONF.vmware.host_password
        vcenter_mapper['host_username'] = CONF.vmware.host_username
        vcenter_mapper['host_ip'] = CONF.vmware.host_ip
        vcenter_mapper['instance_uuid'] = session.vim.service_content.about.instanceUuid

        self._cluster_name = CONF.vmware.cluster_name
        self._cluster_ref = vm_util.get_cluster_ref_by_name(session, self._cluster_name)
        if self._cluster_ref is None:
            raise exception.NotFound(_("The specified cluster '%s' was not "
                                       "found in vCenter")
                                     % self._cluster_name)


        resource_pool = vim_util.get_object_zproperties(self._session.vim, None, self._cluster_ref, self._cluster_ref._type, "resourcePool")
        datastore = vim_util.get_object_properties(self._session.vim, None, self._cluster_ref,
                                                       self._cluster_ref._type, "datastore")

        vcenter_mapper['cluster'] = json.dumps(self._cluster_ref)
        vcenter_mapper['res_pool'] = json.dumps(resource_pool)
        vcenter_mapper['datastore'] = json.dumps(datastore)

        return vcenter_mapper

    def check_availability(self, instance_values):
        pass

endpoints = [NovaVcenterConfigEndpoint()]
server = messaging.get_rpc_server(transport, target, endpoints, executor='blocking')
server.start()