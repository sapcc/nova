import constants
import json
import time
from oslo_log import log as logging

LOG = logging.getLogger(__name__)

def track_request_status(client, request_id):
    """
    Track request status

    :param request_id: vRA REST client
    :param request_id: vRA Request ID
    :return: HTTP Response content
    """
    LOG.info("Track resource status ...")
    r = client.get(
        path=constants.RESOURCE_TRACKER_API + request_id
    )
    content = json.loads(r.content)
    LOG.debug("Resource tracker info: {}".format(content))
    return content


def track_status_waiter(client, id, sleep=5.0):
    """
    Waiter mechanism for pooling vRA request status

    :param request_id: vRA REST client
    :param id: vRA Request ID
    :param sleep: Pool interval seconds
    :return: vRA current request status
    """
    status = "INPROGRESS"
    curr_status = None
    while True:
        time.sleep(sleep)
        curr_status = track_request_status(client, id)
        if curr_status['status'] != status:
            LOG.debug("Current tracker status: {}".format(curr_status))
            break

    return curr_status


#TO-DO refactor this and improve code
def track_deployment_status(client, deployment_id):
    """
    Track deployment status

    :param client: vRA REST client
    :param deployment_id: vRA Deployment ID
    :return: HTTP Response content
    """
    LOG.info("Track deployment status ...")
    r = client.get(
        path=constants.DEPLOYMENT_REQUEST_API.replace("{dep_id}", deployment_id)
    )
    content = json.loads(r.content)
    LOG.debug("Deployment status: {}".format(content['content']))
    return content['content'][0]


def track_deployment_waiter(client, id, sleep=10.0):
    """
    Waiter mechanism for pooling vRA deployment status

    :param client: vRA REST client
    :param id: vRA deployment ID
    :param sleep: Pool interval seconds
    :return: vRA current deployment status
    """
    # curr_status = None
    while True:
        time.sleep(sleep)
        curr_status = track_deployment_status(client, id)

        if curr_status['status'] == "SUCCESSFUL":
            LOG.debug("Current tracker status: {}".format(curr_status))
            break
        elif curr_status['status'] == "FAILED":
            raise Exception(curr_status['message'])
        elif curr_status['status'] == "ABORTED":
            raise Exception("Deployment aborted from vRA client")

    return curr_status
