# Copyright 2016 OpenStack Foundation
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

from oslo_config import cfg


flavor_opts = [
    cfg.StrOpt(
        "default_flavor",
        default="m1.small",
        deprecated_for_removal=True,
        deprecated_since="14.0.0",
        deprecated_reason="The EC2 API is deprecated.",
        help="""
Default flavor to use for the EC2 API only.
The Nova API does not support a default flavor.
"""),
    cfg.StrOpt(
        "flavorid_alias_prefix",
        default="x_deprecated_",
        help="""
To enable gradual deprecation of old flavor names, the new flavors can specifiy
an extra_spec key 'catalog:alias', which adds the flavor to the flavor listing
a second time, only with a different flavorid, and the flavor name replaced by
the value of 'catalog:alias'.

The flavorid is changed by prepending this config value to the actual flavorid.
When the flavor with this flavorid is inspected or used to deploy a server, the
actual aliased flavor will be shown/used respectively.

The 'x_' prefix in the default sorts aliased flavors towards the end of the
flavor list (when sorting by flavorid, which is the API default). This
decreases visibility for aliased flavors.
""")
]


def register_opts(conf):
    conf.register_opts(flavor_opts)


def list_opts():
    return {"DEFAULT": flavor_opts}
