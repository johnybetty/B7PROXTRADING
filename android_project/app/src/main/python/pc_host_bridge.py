"""Android-facing JSON API for original PC strategy host (signal-only).
No order API is exposed. Never persist credentials here.
"""
import json
import threading
from android_pc_host import AndroidPCHost
from broker_session_gate import GATE

_lock = threading.RLock()
_host = None

def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)

def _get():
    global _host
    if _host is None:
        _host = AndroidPCHost()
    return _host

def status():
    with _lock:
        return _json(_get().status())

def start(mode):
    with _lock:
        host = _get()
        host.start_signal_only(mode)
        return _json(host.status())

def tick(pair, price, recv_ms):
    with _lock:
        host = _get()
        accepted = host.push_tick(pair, float(price), int(recv_ms))
        return _json({'accepted': accepted, 'status': host.status()})

def events(limit=50):
    with _lock:
        limit = max(1, min(100, int(limit)))
        host = _get()
        return _json({'events': host.drain_events(limit), 'status': host.status()})

def stop():
    with _lock:
        host = _get()
        host.stop_engine()
        return _json(host.status())

def arm(wallet, amount, confirmed_real=False, compensation_enabled=False, ladder_json='[]'):
    with _lock:
        if wallet not in ('DEMO', 'REAL'):
            raise ValueError('WALLET_INVALID')
        if wallet == 'REAL' and not confirmed_real:
            raise ValueError('REAL_CONFIRMATION_REQUIRED')
        amount = float(amount)
        if amount < 1 or amount > 100000000:
            raise ValueError('AMOUNT_INVALID')
        ladder = json.loads(ladder_json) if isinstance(ladder_json, str) else ladder_json
        if not isinstance(ladder, list) or len(ladder) > 12:
            raise ValueError('INVALID_COMPENSATION_LADDER')
        if any(not isinstance(v, (int, float)) or v < 1 or v > 100000000 for v in ladder):
            raise ValueError('INVALID_COMPENSATION_LOTS')
        sess = GATE._session
        if not sess or not GATE._vip_verified:
            raise RuntimeError('BROKER_SESSION_AND_VIP_REQUIRED')
        
        host = _get()
        old = getattr(host, 'executor', None)
        if getattr(old, 'pending_uuid', None) or getattr(old, 'unconfirmed', False):
            raise RuntimeError('PENDING_ORDER_RECONCILIATION_REQUIRED')
        mode = host._mode
        if mode not in ('OPEN_PRICE_PATTERN', 'RANGE_REACTION'):
            raise RuntimeError('START_PC_HOST_FIRST')
        host.stop_engine()
        
        def callback(key, fields):
            allowed = ('broker_status', 'broker_direction', 'broker_amount', 'broker_expiry',
                       'broker_profit', 'compensation_level', 'broker_uuid', 'broker_message')
            data = {'minute': key, **{k: (str(v) if k == 'broker_message' else v) for k, v in fields.items() if k in allowed}}
            host.q.put(('broker_update', data))
        
        from radar_demo_executor import DemoExecutor
        executor = DemoExecutor({
            'amount': amount,
            'enabled': True,
            'account_type': wallet.lower(),
            'real_confirmed': bool(confirmed_real),
            'broker_name': sess['broker'],
            'on_update': callback,
            'ladder': ladder,
            'compensation_enabled': bool(compensation_enabled),
            'token': sess['token'],
            'device_id': sess['device_id'],
            'pair': 'Z-CRY/IDX',
        })
        host.executor = executor
        host.start_signal_only(mode)
        return _json(host.status())

def disarm():
    with _lock:
        host = _get()
        host.stop_engine()
        if hasattr(host.executor, 'enabled'):
            host.executor.enabled = False
        return _json(host.status())
