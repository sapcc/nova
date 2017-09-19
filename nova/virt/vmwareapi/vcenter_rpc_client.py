from pprint import pprint
from oslo_config import cfg
import oslo_messaging as messaging
import nova.conf

CONF = nova.conf.CONF

class NovaVcenterConfigClient(object):

    def __init__(self, instance):

        self.transport = messaging.get_transport(cfg.CONF)
        self.instance_values = dict()
        self.instance_values['memory_mb'] =  instance.memory_mb
        self.instance_values['instance_vcpus'] = instance.instance.vcpus

        ##Set Configurations required to Create Messaging Transport
        cfg.CONF.set_override('rabbit_host', CONF.oslo_messaging_rabbit.rabbit_hosts)
        #cfg.CONF.set_override('rabbit_port', 5672)
        cfg.CONF.set_override('rabbit_userid', CONF.oslo_messaging_rabbit.rabbit_userid)
        cfg.CONF.set_override('rabbit_password', CONF.oslo_messaging_rabbit.rabbit_password)
        #cfg.CONF.set_override('rabbit_login_method', 'AMQPLAIN')
        #cfg.CONF.set_override('rabbit_virtual_host', '/')
        cfg.CONF.set_override('rpc_backend', 'rabbit')

        self.transport = messaging.get_transport(cfg.CONF)
        self.target = messaging.Target(topic='testme')
        self.client = messaging.RPCClient(self.transport, self.target)


    def call_nova_host(self):
        ctxt = {}
        return self.client.call(ctxt, 'retrieve_vcenter_config_values', self.instance_values)