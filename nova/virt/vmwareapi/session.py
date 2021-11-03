# Copyright (c) 2013 Hewlett-Packard Development Company, L.P.
# Copyright (c) 2012 VMware, Inc.
# Copyright (c) 2011 Citrix Systems, Inc.
# Copyright 2011 OpenStack Foundation
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

import abc
import six
import sys

from oslo_vmware import api
from oslo_vmware import exceptions as vexc
from oslo_vmware import vim
from oslo_vmware.vim_util import get_moref_value

import nova.conf
CONF = nova.conf.CONF


@six.add_metaclass(abc.ABCMeta)
class StableMoRefProxy(object):
    """Abstract Basis class which acts as a proxy
    for Managed-Object-References (MoRef).
    Those references are usually "stable", meaning
    they don't change over the life-time of the object.

    But usually doesn't mean always. In that case, we
    need to fetch the reference again via some search method,
    which uses a guaranteed stable identifier (names, uuids, ...)
    """
    def __init__(self, ref):
        self.moref = ref

    @abc.abstractmethod
    def fetch_moref(self):
        """Updates the moref field or raises
        same exception the initial search would have
        """

    def __getattr__(self, name):
        return getattr(self.moref, name)


class VMwareAPISession(api.VMwareAPISession):
    """Sets up a session with the VC/ESX host and handles all
    the calls made to the host.
    """
    def __init__(self, host_ip=CONF.vmware.host_ip,
                 host_port=CONF.vmware.host_port,
                 username=CONF.vmware.host_username,
                 password=CONF.vmware.host_password,
                 retry_count=CONF.vmware.api_retry_count,
                 scheme="https",
                 cacert=CONF.vmware.ca_file,
                 insecure=CONF.vmware.insecure,
                 pool_size=CONF.vmware.connection_pool_size):
        super(VMwareAPISession, self).__init__(
                host=host_ip,
                port=host_port,
                server_username=username,
                server_password=password,
                api_retry_count=retry_count,
                task_poll_interval=CONF.vmware.task_poll_interval,
                scheme=scheme,
                create_session=True,
                cacert=cacert,
                insecure=insecure,
                pool_size=pool_size)

    @staticmethod
    def _is_vim_object(module):
        """Check if the module is a VIM Object instance."""
        return isinstance(module, vim.Vim)

    def call_method(self, module, method, *args, **kwargs):
        """Calls a method within the module specified with
        args provided.
        """
        try:
            if not self._is_vim_object(module):
                return self.invoke_api(module, method, self.vim,
                                       *args, **kwargs)

            return self.invoke_api(module, method, *args, **kwargs)
        except vexc.ManagedObjectNotFoundException as monfe:
            obj = monfe.details.get("obj")
            any_change = False
            for arg in args:
                if (isinstance(arg, StableMoRefProxy)
                        and obj == get_moref_value(arg.moref)):
                    arg.fetch_moref()
                    any_change = True

            if not any_change:
                six.reraise(*sys.exc_info())

            return self.call_method(module, method, *args, **kwargs)
