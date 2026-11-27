"""The generated telemetry overlay the installed root's fixture hook reads.

os/install/generated_userdata.py writes it into synthetic UBI state; the
root-init hook classifies the first player from it, and test_boot.py checks
the player's acknowledgement. Never opens or merges a user configuration.
Receipts carry only projection(): no token and no destination address.
"""
import json
import re
import uuid
from urllib.parse import urlsplit


def settings(run_id, *, endpoint=None, token=None):
    try:
        value = str(uuid.UUID(run_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('telemetry run ID must be a UUID') from error
    if value != run_id:
        raise ValueError('telemetry run ID must be canonical')
    result = dict(enabled=True, stream='automated-test', labels=['automated-test'], run_id=value)
    if endpoint is not None:
        if not isinstance(endpoint, str) or len(endpoint) > 256 or any(ord(x) < 33 or ord(x) > 126 for x in endpoint):
            raise ValueError('invalid fixture endpoint')
        try:
            url = urlsplit(endpoint)
            valid = (url.scheme in ('http', 'https') and bool(url.hostname) and url.username is None
                     and url.password is None and not url.query and not url.fragment and url.path in ('', '/'))
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('fixture endpoint must be an HTTP(S) base URL without credentials, path, query or fragment')
        result['endpoint'] = endpoint.rstrip('/')
    if token is not None:
        if not isinstance(token, str) or re.fullmatch('[0-9a-f]{64}', token) is None:
            raise ValueError('fixture token must be 64 lowercase hex characters')
        result['token'] = token
    return result


def render(run_id, *, endpoint=None, token=None):
    values = settings(run_id, endpoint=endpoint, token=token)
    return ('[telemetry]\n' + ''.join(f'{key} = {json.dumps(value, separators=(",", ":"))}\n'
                                      for key, value in values.items())).encode()


def projection(run_id, *, endpoint=None, token=None):
    """Receipt whitelist: never the token or the destination address."""
    value = settings(run_id, endpoint=endpoint, token=token)
    return {**{k: value[k] for k in ('enabled', 'stream', 'labels', 'run_id')},
            'endpoint_configured': endpoint is not None, 'token_configured': token is not None,
            'overlay_only': True, 'base_config_accessed': False, 'effective_context_verified': False}


def validate_ack(ack, nonce, process):
    """The player's telemetry context acknowledgement for request `nonce` from
    `process` (pid, start ticks)."""
    keys = {'schema', 'request_id', 'pid', 'start_ticks', 'generation', 'enabled', 'stream', 'labels', 'run_id',
            'overlay_present'}
    if not isinstance(ack, dict) or set(ack) != keys:
        raise ValueError('telemetry acknowledgement schema')
    if type(ack['schema']) is not int or ack['schema'] != 1 or ack['request_id'] != nonce:
        raise ValueError('telemetry acknowledgement request')
    if (any(type(ack[k]) is not int or ack[k] <= 0 for k in ('pid', 'start_ticks', 'generation'))
            or (ack['pid'], ack['start_ticks']) != process):
        raise ValueError('telemetry acknowledgement process')
    if type(ack['enabled']) is not bool or type(ack['overlay_present']) is not bool:
        raise ValueError('telemetry acknowledgement flags')
    if (ack['stream'] not in ('production', 'automated-test')
            or ack['labels'] != (['automated-test'] if ack['stream'] == 'automated-test' else [])):
        raise ValueError('telemetry acknowledgement classification')
    if ack['run_id'] is not None and str(uuid.UUID(ack['run_id'])) != ack['run_id']:
        raise ValueError('telemetry acknowledgement run ID')
