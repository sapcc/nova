
from nova import context
from nova.consoleauth import rpcapi as consoleauth_rpcapi


def request(flow):
    token = flow.request.query["token"]

    if token:
        ctxt = context.get_admin_context()
        rpcapi = consoleauth_rpcapi.ConsoleAuthAPI()

        if not rpcapi.check_token(ctxt, token=token):
            # token not valid
            flow.response.status_code = 403
            flow.response.content = b"The token has expired or invalid."

