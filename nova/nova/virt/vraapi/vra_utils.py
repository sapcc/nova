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
