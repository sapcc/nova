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

import subprocess

from argparse import ArgumentParser

import nova.conf
from nova.conf import serial_console as serial
from nova import config

CONF = nova.conf.CONF
serial.register_cli_opts(CONF)


def main():
    """
    """
    config.parse_args([])  # we need this to configure rpc

    parser = ArgumentParser(description=('Nova Shellinabox Console Proxy '
                                         'for Ironic Servers.'))

    parser.add_argument('proxytarget', type=str,
                        help=('Hostname or IP of the proxy target. '
                              'Without protocol.'))
    parser.add_argument('--proxyport', type=int,
                        default=443,
                        help='Port of the proxy target')
    parser.add_argument('--listenip', type=str,
                        default=CONF.serial_console.shellinaboxproxy_host,
                        help='IP of the interface to listen on.')
    parser.add_argument('--listenport', type=int,
                        default=int(CONF.serial_console.shellinaboxproxy_port),
                        help='Port to listen on.')
    cli_args = parser.parse_args()

    p = subprocess.check_output(
        "mitmproxy -R https://%s/ --port %d --bind-address %s" % (cli_args.proxytarget, cli_args.listenport, cli_args.listenip),
        shell=True)

