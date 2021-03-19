import os

import eventlet
if not os.environ.get('DISABLE_EVENTLET_PATCHING'):
    eventlet.monkey_patch()

import logging
import requests
import json
from requests.exceptions import HTTPError
from requests.exceptions import ConnectionError
from requests.exceptions import ConnectTimeout
import synchronization as sync

LOG = logging.getLogger(__name__)


class VraUnauthorized(Exception):
    pass

# Decorator
def connection_retry_policy(func):
    def decorator(self, *args, **kwargs):
        method = "{}.{}".format(self.__class__.__name__, func.__name__)
        max_login_attempts = 3
        until = 0
        pause = 60

        pattern = "Retrying connection ({}/{}) with timeout {}s for {}"

        login_attempts = 0
        now = 1
        while True:
            resp = 0
            try:
                resp = func(self, *args, **kwargs)

                LOG.debug(
                    "HTTP Response URL={} Method={} Code={} Reason={}"
                    .format(resp.url, resp.request.method,
                            resp.status_code, resp.reason))

                if resp.status_code == 404:
                    return resp

                if resp.status_code >= 400 and resp.status_code < 500:
                    raise VraUnauthorized(resp.content)

                if resp.status_code >= 300:
                    msg = "HTTP Response URL={} Code={} Reason={} \
                        Content={}".format(resp.url, resp.status_code,
                                           resp.reason, resp.content)
                    raise HttpUnsuccessfulException(msg)
                return resp
            except (HTTPError, ConnectionError, ConnectTimeout) as err:
                LOG.error("Unable to connect. Error: {}".format(err))
            except VraUnauthorized as err:
                if resp.status_code == 401:
                    LOG.error("Unauthorized: {}".format(err))
                    if login_attempts < max_login_attempts:
                        login_attempts += 1
                        self.login()
                        continue
                    else:
                        raise err
                else:
                    LOG.error("Error: {}".format(err))
                    raise err

            now += 1
            if now > until:
                raise Exception("Failed. {}".format(
                    pattern.format(now, until, pause, method)))
            LOG.debug(msg)
            eventlet.sleep(pause)
        return None

    return decorator


class VraRestClient(object):
    """
    This class is reponsible to orchestrate the Openstack to vRA communication using vRA REST API
    """
    LOGIN_API = "/csp/gateway/am/api/login"
    BLUEPRINT_REQUESTS_API = "/blueprint/api/blueprint-requests"
    BLUEPRINTS_API = "/blueprint/api/blueprints"

    REQUEST_SSL_VERIFY = False
    REQUEST_TIMEOUT = 60

    def __init__(self, api_scheduler, base_url, username, password, domain):
        """Constructor"""
        logging.basicConfig(level=logging.INFO)
        self.api_scheduler = api_scheduler
        self.base_url = base_url
        self.username = username
        self.password = password
        self.domain = domain

        requests.packages.urllib3.disable_warnings()
        self.session = requests.session()

    # START #### A set of methods encapsulating the vRA API queue

    def _get_url(self, path):
        return "{}{}".format(self.base_url, path)

    @connection_retry_policy
    def _post(self, path, json):
        with self.api_scheduler:
            return self.session.post(
                url=self._get_url(path),
                json=json,
                verify=self.REQUEST_SSL_VERIFY,
                timeout=self.REQUEST_TIMEOUT)

    @connection_retry_policy
    def _get(self, path):
        with self.api_scheduler:
            return self.session.get(
                url=self._get_url(path),
                verify=self.REQUEST_SSL_VERIFY,
                timeout=self.REQUEST_TIMEOUT)

    @connection_retry_policy
    def _put(self, path, json):
        with self.api_scheduler:
            return self.session.put(
                url=self._get_url(path),
                json=json,
                verify=self.REQUEST_SSL_VERIFY,
                timeout=self.REQUEST_TIMEOUT)

    @connection_retry_policy
    def _delete(self, path):
        with self.api_scheduler:
            return self.session.delete(
                url=self._get_url(path),
                verify=self.REQUEST_SSL_VERIFY,
                timeout=self.REQUEST_TIMEOUT)

    # END   #### A set of methods encapsulating the vRA API queue
    def login(self):
        """
            Login to vRA
        """
        LOG.info("About to login to {} ...".format(self.base_url))
        r = self._post(
            path=self.LOGIN_API,
            json={"username": self.username,
                  "password": self.password, "domain": self.domain},
        )
        content = json.loads(r.content)
        self.loginToken = content.get("cspAuthToken")
        self.session.headers.update(
            {'Authorization': 'Bearer {}'.format(self.loginToken)})

        LOG.info("Obtained vRA token")

    def blueprintRequest(self, blueprintId, blueprintVersion, deploymentName, inputs):
        """
            Request blueprint from vRA
        """
        LOG.info("About to request blueprint {} ...".format(blueprintId))

        jsonPayload = {
            "blueprintId": blueprintId,
            "blueprintVersion": blueprintVersion,
            "deploymentName": deploymentName,
            "inputs": inputs,
            "plan": False,
            "projectId": "e3a81308-d6cc-4125-ae3e-fdaa80f12e95",
            "reason": "Create server",
            "simulate": False
        }
        LOG.info(json.dumps(jsonPayload))

        r = self._post(
            path=self.BLUEPRINT_REQUESTS_API,
            json=jsonPayload
        )
        content = json.loads(r.content)

        LOG.info(r)
        LOG.info(content)

    def getBlueprints(self):
        """
            Get all blueprints
        """
        LOG.info("Get Blueprints ...")
        r = self._get(
            path=self.BLUEPRINTS_API
        )
        content = json.loads(r.content)
        return content["content"]

    def getBlueprint(self, name):
        """
            Get blueprint by name
        """
        blueprints = self.getBlueprints()
        blueprint = filter(lambda x: x["name"] == name, blueprints)
        if len(blueprint) == 1:
            return blueprint[0]
        elif len(blueprint) > 1:
            LOG.error("Multiple blueprints available with name {}".format(name))
            return
        elif len(blueprint) == 0:
            LOG.error("Blueprint with name {} not found!".format(name))
            return
