# Source Generated with Decompyle++
# File: pc_session_bridge.pyc (Python 3.8)

'''Native-facing bridge; responses NEVER contain token/device_id.'''
import json
from broker_session_gate import GATE

def _j(x):
    return json.dumps(x, False, **('ensure_ascii',))


def status():
    return _j(GATE.status())


def set_session(broker, user_id, token, device_id):
    return _j(GATE.set_session(broker, user_id, token, device_id))


def vip_start_verified(user_id):
    return _j(GATE.vip_start_verified(user_id))


def preflight(wallet = ('DEMO',)):
    return _j(GATE.preflight(wallet))


def clear():
    return _j(GATE.clear())


def reset_vip():
    return _j(GATE.reset_vip())


def balance(wallet = ('DEMO',)):
    '''Read wallet balance from authenticated broker REST endpoint, not local PNL.'''
    if wallet not in ('DEMO', 'REAL'):
        return _j({
            'ok': False,
            'error': 'INVALID_WALLET' })
# WARNING: Decompyle incomplete

