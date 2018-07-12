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

from sys import stdout
from argparse import ArgumentParser

from nova import context
from nova.consoleauth import rpcapi as consoleauth_rpcapi

from twisted.web import proxy, server
from twisted.protocols.tls import TLSMemoryBIOFactory
from twisted.internet import ssl, defer, task, endpoints
from twisted.logger import globalLogBeginner, textFileLogObserver

from twisted.internet import reactor


globalLogBeginner.beginLoggingTo([textFileLogObserver(stdout)])


def main():
    site = server.Site(proxy.ReverseProxyResource('ironic-conductor-ipmi-console', 80, ''))
    reactor.listenTCP(6084, site)
    reactor.run()

if __name__ == '__main__':
    main()

