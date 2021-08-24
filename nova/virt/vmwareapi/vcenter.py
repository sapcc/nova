# Copyright (c) 2021 SAP SE
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
import requests


class VCenterRESTHelper(object):
    def __init__(self, host, user, password, verify_ssl=False):
        self.api = None
        self.host = host
        self.user = user
        self.password = password
        self.verify_ssl = verify_ssl

        self._URL = 'https://{}/rest{{}}'.format(self.host)

        self.login()

    def login(self):
        s = requests.Session()
        if not self.verify_ssl:
            s.verify = False

        r = s.post(self._URL.format('/com/vmware/cis/session'),
                   auth=(self.user, self.password))
        if r.status_code != 200:
            raise RuntimeError('{}: {}'.format(r, r.content))

        self.api = s

    def disconnect(self):
        if self.api:
            self.api.delete(self._URL.format('/com/vmware/cis/session'))

    def raw_request(self, method, url, unpack=True, **kwargs):
        if not url.startswith('/'):
            url = '/' + url

        r = getattr(self.api, method)(self._URL.format(url), **kwargs)
        if r.status_code != 200:
            raise RuntimeError('{}: {}'.format(r, r.content))

        if r.content:
            data = r.json()
            if unpack and 'value' in data:
                return data['value']
            return data

    def get(self, url, **kwargs):
        return self.raw_request('get', url, **kwargs)

    def post(self, url, **kwargs):
        return self.raw_request('post', url, **kwargs)

    def list_libraries(self):
        data = self.get('/com/vmware/content/subscribed-library')
        return data

    def list_items(self, lib_id):
        data = self.get('/com/vmware/content/library/item',
                        params={'library_id': lib_id})
        return data

    def list_item_storage(self, item_id):
        data = self.get('com/vmware/content/library/item/storage',
                        params={'library_item_id': item_id})
        return data

    def subscribed_item_action(self, item_id, action, data=None):
        url = ('/com/vmware/content/library/subscribed-item/id:{}'
               .format(item_id))
        data = self.post(url=url, params={'~action': action}, json=data)
        return data

    def sync_subscribed_item(self, item_id, force_sync_content=False):
        return self.subscribed_item_action(item_id, 'sync', {
            'force_sync_content': force_sync_content
        })

    def list_item_files(self, item_id):
        data = self.get('com/vmware/content/library/item/file',
                        params={'library_item_id': item_id})
        return data

    def find_item(self, find):
        return self.post('/com/vmware/content/library/item',
                         params={'~action': 'find'}, json={'spec': find})
