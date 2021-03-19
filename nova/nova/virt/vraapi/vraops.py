from oslo_log import log as logging
import nova.conf
from nova.virt import hardware
import nova.privsep.path
import requests
import synchronization as sync
from VraRestClient import VraRestClient

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

class VraOps(object):

    def __init__(self):
        self.vra_host = CONF.vmware.host_ip
        self.vra_username = CONF.vmware.host_username
        self.vra_password = CONF.vmware.host_password

        self.api_scheduler = sync.Scheduler(rate=2,
                                       limit=1)
        self.vraClient = VraRestClient(self.api_scheduler, "https://" + self.vra_host,
                                  self.vra_username, self.vra_password, "System Domain")

    def spawn(self, instance, image_meta, injected_files,
              admin_password, network_info, block_device_info=None):

        LOG.debug('Spawn instance')
