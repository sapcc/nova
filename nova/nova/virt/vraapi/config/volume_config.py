from oslo_config import cfg
import nova.conf

vra_opts = [
    cfg.HostAddressOpt('host', default="vra-l-01a.crop.local", help="vRA client hostname or IP."),
    cfg.PortOpt('port', default=443, help="vRA client port."),
    cfg.StrOpt('domain',default="System Domain", help="vRA client domain."),
    cfg.StrOpt('username', default="configurationadmin",help="vRA client username."),
    cfg.StrOpt('password', default="VMware1!", secret=True, help="vRA client password."),
    cfg.StrOpt('organization', default="", help="vRA client organization ID."),
    cfg.IntOpt('connection_retries', default=10, help='vRA client connection retries.'),
    cfg.IntOpt('connection_retries_seconds', default=5, help='vRA client connection retry pause.'),
    cfg.IntOpt('connection_timeout_seconds', default=60, help='vRA client connection timeout.'),
    cfg.IntOpt('connection_throttling_rate', default=90, help='vRA client requests per limit.'),
    cfg.IntOpt('connection_throttling_limit_seconds', default=90, help='vRA client requests per seconds.'),
    cfg.IntOpt('connection_throttling_timeout_seconds', default=5,
               help='vRA client limit in seconds for requests per limit.'),
    cfg.IntOpt('connection_query_limit', default=2000, help="vRA client query limit."),
    cfg.BoolOpt('connection_certificate_check', default=True, help="vRA client validate certificate."),
    cfg.BoolOpt('spoof_guard', default=False, help="Enforce spoofguard."),
    cfg.ListOpt('cloud_zone', default=[], help="vRA Cloud Zone ID associated with this agent"),
]

CONF = cfg.CONF
CONF.register_opts(vra_opts, "VRA")