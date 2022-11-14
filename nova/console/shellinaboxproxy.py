# Copyright (c) 2018 OpenStack Foundation
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

import hashlib

import configparser
from mitmproxy.models import http
from mitmproxy.script import concurrent
from netlib import http as netlib_http
from netlib import version
from sqlalchemy import create_engine
from sqlalchemy.sql import text

STATIC_FILES_EXT = (".js", ".css", ".html", ".ico", ".png", ".gif")


def _load_dbs():
    config = configparser.ConfigParser()
    config.read("/etc/nova/nova.conf")
    api_db_url = config["api_database"]["connection"]
    api_db_engine = create_engine(api_db_url)
    with api_db_engine.connect() as api_db:
        for (db_url,) in api_db.execute(
            text("SELECT database_connection FROM cell_mappings WHERE id > 1")
        ):
            yield create_engine(db_url)


_DBS = list(_load_dbs())


@concurrent
def request(flow):
    if flow.error or not flow.live:
        return
    request = flow.request

    # We only have to validate the "initial" GET
    # on the console path, as that one returns a session-id
    # which is validated by the backend

    if request.method == "POST":  # POST gets validated by the backend
        return

    path = request.path
    if path.endswith(STATIC_FILES_EXT):  # Not secret
        return

    if path in [None, "", "/"]:  # The liveness probe
        _root_response(flow)
        return

    token = flow.request.query.pop("token", None)

    if not token:
        _access_denied(flow, b"No token provided.")
        return

    token_hash = _get_sha256_str(token)

    for db in _DBS:
        if _validate_token(db, token_hash):
            return

    _access_denied(flow, b"The token has expired or is invalid.")


def _root_response(flow):
    http_version = flow.request.data.http_version
    headers = netlib_http.Headers(
        Server=version.MITMPROXY,
        Connection="close",
        Content_Length="0",
    )

    flow.response = http.HTTPResponse(
        http_version, 204, b"No content", headers, b""
    )


def _validate_token(db_engine, token_hash):
    with db_engine.connect() as con:
        for (exists,) in con.execute(
            text(
                "SELECT EXISTS("
                "  SELECT 1 FROM console_auth_tokens "
                "    WHERE token_hash=:token_hash"
                "    AND expires > UNIX_TIMESTAMP()"
                ")"
            ),
            token_hash=token_hash,
        ):
            if exists > 0:
                return True
    return False


def _get_sha256_str(base_str):
    return hashlib.sha256(base_str.encode("utf-8")).hexdigest()


def _access_denied(flow, reason):
    flow.response = http.make_error_response(403, reason)
