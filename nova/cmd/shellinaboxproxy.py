# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import sys

import nova.conf
from nova.conf import serial_console as serial
from nova import config
from nova.console import shellinaboxproxy


CONF = nova.conf.CONF
serial.register_cli_opts(CONF)


def main():
    config.parse_args(sys.argv)

    server_address = (CONF.serial_console.shellinaboxproxy_host,
                      CONF.serial_console.shellinaboxproxy_port)

    proxy = shellinaboxproxy.ThreadingHTTPServer(
        server_address,
        shellinaboxproxy.ProxyHandler)
    proxy.service_start()
