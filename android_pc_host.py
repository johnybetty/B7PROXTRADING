"""Android-compatible lifecycle host for unchanged PC strategies.

SIGNAL-ONLY ENGINE HARNESS: this module intentionally has no broker order transport.
Do not wire it to REAL/DEMO START until a reconciled broker executor exists.
"""
from collections import deque
from queue import Queue
from threading import Event, Lock, Thread
import time
from pc_strategy_mixin import PCStrategyMixin
from host_contract import assert_host_ready


class TickCache:

    def __init__(self):
        self._asset_tick_lock = Lock()
        self._asset_tick_history = {
            'Z-CRY/IDX': deque(maxlen=5000)
        }

    def push(self, pair, price, recv_ms):
        if pair != 'Z-CRY/IDX':
            raise ValueError('PC source strategy supports Z-CRY/IDX only')
        if not (isinstance(recv_ms, (int, float)) and isinstance(price, (int, float)) and price > 0):
            raise ValueError('invalid tick')
        with self._asset_tick_lock:
            rows = self._asset_tick_history[pair]
            if rows and recv_ms <= rows[-1]['recv_ms']:
                return False
            rows.append({'recv_ms': recv_ms, 'price': price})
            return True


class SignalOnlyExecutor:
    """No real order transport. A PC strategy can only log DISABLED."""

    def __init__(self, cache, emit=None):
        self._emit = emit or (lambda event: None)
        self.broker = cache
        self.on_update = lambda key, fields: None
        self.sent_minutes = set()
        self.pending_uuid = None
        self.fo_pending = False
        self.unconfirmed = False
        self.attempts = []
        self.attempt_count = 0

    def _record_intent(self, transport, item):
        payload = dict(item)
        self.attempt_count += 1
        self.attempts.append({
            'transport': transport,
            'payload': payload,
            'result': 'DISABLED'
        })
        if len(self.attempts) > 100:
            del self.attempts[:-100]
        self._emit(('order_intent', {
            'transport': transport,
            'payload': payload,
            'result': 'DISABLED',
            'sequence': self.attempt_count
        }))
        return 'DISABLED'

    def submit_m1_quick(self, item):
        return self._record_intent('submit_m1_quick', item)

    def submit(self, item):
        return self._record_intent('submit', item)


class AndroidPCHost(PCStrategyMixin):
    """Run original PC method bodies on a worker, not the Android UI thread."""

    def __init__(self, executor=None):
        self.stop = Event()
        self.risk_halted = Event()
        self.q = Queue()
        self.order_patterns = {}
        self.executor = executor if executor is not None else SignalOnlyExecutor(TickCache(), emit=self.q.put)
        self._thread = None
        self._mode = None
        assert_host_ready(self)

    def start_signal_only(self, mode):
        if mode not in ('OPEN_PRICE_PATTERN', 'RANGE_REACTION'):
            raise ValueError('unsupported PC strategy mode')
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError('engine already running')
        self.stop.clear()
        self.risk_halted.clear()
        self._mode = mode
        target = self._receive_open_price_pattern if mode == 'OPEN_PRICE_PATTERN' else self._receive_range_reaction
        self._thread = Thread(target=self._run_guarded, args=(target,), daemon=True)
        self._thread.start()

    def _run_guarded(self, target):
        try:
            try:
                target()
            except Exception as exc:
                self.risk_halted.set()
                self.q.put(('error', f"{type(exc).__name__}: {exc}"))
        finally:
            self.q.put(('status', 'STOPPED'))

    def push_tick(self, pair, price, recv_ms):
        broker = self.executor.broker
        if hasattr(broker, 'push'):
            return broker.push(pair, price, recv_ms)
        if pair != 'Z-CRY/IDX' or price <= 0 or recv_ms <= 0:
            return False
        broker._store_asset_tick(pair, float(price), int(recv_ms))
        return True

    def stop_engine(self, timeout=3):
        self.stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError('engine worker did not stop')
        return True

    def drain_events(self, limit=100):
        events = []
        for _ in range(limit):
            if self.q.empty():
                break
            events.append(self.q.get_nowait())
        return events

    def status(self):
        return {
            'mode': self._mode,
            'running': bool(self._thread and self._thread.is_alive()),
            'orders_enabled': bool(getattr(self.executor, 'enabled', False)),
            'risk_halted': self.risk_halted.is_set(),
            'order_intent_count': getattr(self.executor, 'attempt_count', 0),
            'recent_order_intents': getattr(self.executor, 'attempts', [])[-5:],
            'pending_uuid': getattr(self.executor, 'pending_uuid', None),
            'unconfirmed': bool(getattr(self.executor, 'unconfirmed', False))
        }
