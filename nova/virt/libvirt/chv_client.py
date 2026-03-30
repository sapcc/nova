# Copyright 2025 SAP SE
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
import urllib.parse

import nova.conf
import nova.privsep.libvirt

CONF = nova.conf.CONF


class ChvClientError(Exception):
    pass


class ChvClient:

    def __init__(self, instance):
        if not (path_template := CONF.workarounds.chv_socket_path_template):
            raise ChvClientError(
                '[workarounds]/chv_socket_path_template is not set.')

        try:
            socket_path = path_template % (instance.name, )
        except Exception as e:
            raise ChvClientError("Could not template [workarounds]/"
                f"chv_socket_path_template ({path_template}) with "
                f"{instance.name}: {e}")

        quoted_socket_path = urllib.parse.quote_plus(socket_path)
        self._base_url = f"http+unix://{quoted_socket_path}/api/v1"

    def post_migration_announce(self):
        """Calls the VM's vm.post-migration-announce endpoint

        Raises ChvClientError if the response is not an HTTP 204
        """
        url = f"{self._base_url}/vm.post-migration-announce"
        try:
            status_code, resp_text = \
                nova.privsep.libvirt.chv_socket_request('put', url)
        except Exception as e:
            raise ChvClientError(e) from e

        if status_code != 204:
            raise ChvClientError("vm.post-migration-announce returned "
                f"{status_code} instead of 204: {resp_text}")
