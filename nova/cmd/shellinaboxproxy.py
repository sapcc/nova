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

from os import path
from subprocess import check_output
from argparse import ArgumentParser

import nova.conf
from nova.console import shellinaboxproxy
from nova.conf import serial_console as serial

CONF = nova.conf.CONF
serial.register_cli_opts(CONF)


def main():
    """
    Parses cli arguments and starts mitmproxy with token validation.
    """
    parser = ArgumentParser(description=('Nova Shellinabox Console Proxy '
                                         'for Ironic Servers.'))

    parser.add_argument('proxytarget', type=str,
                        help=('Hostname or IP of the proxy target. '
                              'Without protocol.'))

    parser.add_argument('--listenip', type=str,
                        default=CONF.serial_console.shellinaboxproxy_host,
                        help='IP of the interface to listen on.')

    parser.add_argument('--listenport', type=str,
                        default=CONF.serial_console.shellinaboxproxy_port,
                        help='Port to listen on.')
    cli_args = parser.parse_args()

    # Run mitmproxy with shellinaboxproxy.py as an inline script
    check_output("mitmdump -R %s --port %s --bind-address %s --script %s" % (
                 cli_args.proxytarget,
                 cli_args.listenport,
                 cli_args.listenip,
                 path.abspath(shellinaboxproxy.__file__)), shell=True)
