# Copyright 2018 SAP SE
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import sys

import oslo_messaging as messaging

import nova.conf
from nova import config
from nova import context
from nova.virt.vmwareapi import driver

CONF = nova.conf.CONF

class TestClient(object):

    def __init__(self):
        config.parse_args(sys.argv, configure_db=False)

        transport = messaging.get_rpc_transport(CONF)

        target = messaging.Target(topic=driver.RPC_TOPIC, version='1.0')
        self._client = messaging.RPCClient(transport, target)

    def main(self):
        class Instance(object):
            def uuid(self):
                return "2aec314a-624e-43c3-9a3c-69597366f880"

        self.get_console(context.get_context(), Instance())

    def get_console(self, ctxt, instance):
        print(self._client.call(ctxt, 'vspc_get_console_output', instance_uuid=instance.uuid))

if __name__ == "__main__":
    client = TestClient()
    client.main()