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


class NovaShellInABoxProxy(proxy.ReverseProxyResource, object):

    def proxyClientFactoryClass(self, *args, **kwargs):
        """
        Make connections over HTTPS.
        """
        return TLSMemoryBIOFactory(
            ssl.optionsForClientTLS(self.host.decode("ascii")), True,
            super(NovaShellInABoxProxy, self)
            .proxyClientFactoryClass(*args, **kwargs))

    def getChild(self, path, request):
        """
        Ensure that implementation of C{proxyClientFactoryClass} is honored
        down the resource chain.
        """
        child = super(NovaShellInABoxProxy, self).getChild(path, request)
        return NovaShellInABoxProxy(child.host, child.port, child.path,
                                    child.reactor)

    def prepare403Resp(self, message):
        """
        Returns the 403 page with given message.
        """
        return """
<html>
<head><title>403 Forbidden</title></head>
<body bgcolor="white">
<center><h1>403 Forbidden</h1></center>
<hr><center>%s</center>
</body>
</html>
""" % message

    def render(self, request):
        """
        Process the requests with Nova consoleauth token validation.
        """
        print "Received request with arguments: %s" % request.args
        token = request.args.pop('token', '')

        if not token:
            # no token = no console
            request.setResponseCode(403, message="Forbidden")
            return self.prepare403Resp('Token is missing')

        ctxt = context.get_admin_context()
        rpcapi = consoleauth_rpcapi.ConsoleAuthAPI()

        if not rpcapi.check_token(ctxt, token=token):
            # no valid token = no console
            request.setResponseCode(403, message="Forbidden")
            return self.prepare403Resp('Token has expired or invalid')
        else:
            # all good, do proxy
            return proxy.ReverseProxyResource.render(self, request)


@task.react
def main(reactor):
    """
    Entry Point. Parse given arguments and start serving.
    """
    parser = ArgumentParser(description=('Nova Shellinabox Console Proxy '
                                         'for Ironic Servers.'))

    parser.add_argument('--listenip', type=str,
                        default='0.0.0.0',
                        help=('IP of the interface to listen on. '
                              'Default: 0.0.0.0'))
    parser.add_argument('--listenport', type=int,
                        default=80, help=('Port to listen on.'
                                          'Default: 80'))
    parser.add_argument('proxytarget', type=str,
                        help=('Hostname or IP of the proxy target. '
                              'Without protocol.'))
    parser.add_argument('proxyport', type=int,
                        help='Port of the proxy target')
    cli_args = parser.parse_args()

    # start logging to stdout from this point on
    globalLogBeginner.beginLoggingTo([textFileLogObserver(stdout)])

    serve_forever = defer.Deferred()
    shellinabox = NovaShellInABoxProxy(cli_args.proxytarget,
                                       cli_args.proxyport, '')
    shellinabox.putChild('', shellinabox)
    site = server.Site(shellinabox)

    endpoint = endpoints.serverFromString(reactor,
                                          "tcp:%d:interface=%s" %
                                          (cli_args.listenport,
                                           cli_args.listenip))
    endpoint.listen(site)

    return serve_forever
