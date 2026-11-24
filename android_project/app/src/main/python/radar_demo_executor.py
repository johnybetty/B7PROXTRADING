# Source Generated with Decompyle++
# File: radar_demo_executor.pyc (Python 3.8)

'''Account-scoped executor with explicit REAL opt-in. Defaults disabled.'''
import asyncio
import threading
import time
from broker_api import BrokerAPI

class StrictExpiryBroker(BrokerAPI):
    
    def __init__(self = None, config = None, logger = None):
        super().__init__(config, logger)
        self.target_expiry = 0
        self.entry_mode = 'MASTER'

    
    def _expire_at(self, duration_seconds):
        target = int(self.target_expiry)
        now = time.time()
        max_remaining = 62 if self.entry_mode in ('NEXT', 'M1_QUICK') else 36
        if target:
            if not target - now < target - now or target - now <= max_remaining:
                pass
            else:
                5
        raise RuntimeError('EXPIRY_GUARD: target candle expired or outside valid window')
        return target

    
    async def _send(self = None, topic = None, event = None, payload = None, join_ref = None):
        if topic == 'bo' and event == 'create' and payload.get('option_type') != 'blitz':
            target = int(self.target_expiry)
            if payload.get('deal_type') != self.cfg.get('account_type', 'demo'):
                raise RuntimeError('ACCOUNT_MISMATCH')
            if target:
                if not payload.get('expire_at'):
                    pass
                if int(0) != target:
                    raise RuntimeError('EXPIRY_MISMATCH')
                now = time.time()
                sec = now % 60
                if self.entry_mode == 'NEXT':
                    if sec <= sec:
                        pass
                    
                elif self.entry_mode == 'M1_QUICK':
                    if sec <= sec:
                        pass
                    
                elif self.entry_mode == 'CURRENT':
                    if sec <= sec:
                        pass
                    
                elif sec <= sec:
                    pass
                
            allowed = (sec <= sec) < 30
            if not now >= target - 5 or allowed:
                raise RuntimeError(f'''LATE_ORDER_SKIP mode={self.entry_mode} sec={sec:.3f}''')
        await super()._send(topic, event, payload, join_ref)
        return <NODE:28>

    __classcell__ = None


class DemoExecutor:
    
    def __init__(self, token, device_id, asset, amount, enabled, on_update, ladder, compensation_enabled, account_type, real_confirmed, broker_name, fo_enabled, fo_trigger = (14000, False, None, None, False, 'demo', False, 'binomo', False, 1)):
        if account_type not in ('demo', 'real'):
            raise ValueError('Invalid account')
        if not account_type == 'real' and enabled and real_confirmed:
            raise RuntimeError('REAL_REQUIRES_EXPLICIT_CONFIRMATION')
        if broker_name not in ('binomo', 'stockity'):
            raise ValueError('Invalid broker')
        self.fo_enabled = bool(fo_enabled)
        self.fo_trigger = int(fo_trigger)
        self.fo_phase = 'FO' if self.fo_enabled else 'REAL'
        self.fo_losses = 0
        self.fo_pending = False
        self.fo_real_has_won = False
        self.broker_name = broker_name
        self.account_type = account_type
        self.enabled = bool(enabled)
        self.asset = asset
        self.amount = amount
        self.on_update = on_update
        self.lock = threading.RLock()
        self.sent_minutes = set()
        self.broker = None
        if not ladder:
            pass
        self.ladder = (lambda .0: [ float(x) for x in .0 ])([
            amount])
        self.stage = 0
        self.compensation_enabled = bool(compensation_enabled)
        self.pending_uuid = None
        self.unconfirmed = False
        self.unknown_order = None
        if self.enabled:
            cfg = {
                'broker': broker_name,
                'domains': {
                    'binomo': 'binomo5.com',
                    'stockity': 'stockity.com' },
                'authtoken': token,
                'device_id': device_id,
                'asset_ric': asset,
                'account_type': account_type,
                'amount_multiplier': 100,
                'price_ws_url': 'wss://as.stockity.com/' if broker_name == 'stockity' else 'wss://as.binomo5.com/',
                'timeout_seconds': 10,
                'debug_ws': False }
            self.broker = StrictExpiryBroker(cfg)
            self.broker.start_ws_background(asset)

    
    def _update(self, minute, **fields):
        pass
    # WARNING: Decompyle incomplete

    
    def reset_compensation(self):
        pass
    # WARNING: Decompyle incomplete

    
    def _settle_compensation(self, oid, result):
        pass
    # WARNING: Decompyle incomplete

    
    def submit_m1_quick(self, signal):
        '''CURRENT M1 expiry, dynamic 00-29 entry; one attempt per candle.'''
        if not self.enabled:
            return 'DISABLED'
        direction = None.get('direction')
        minute = int(signal['minute'])
        expiry = int(signal['expiry_ts'])
        now = time.time()
        if direction not in ('BUY', 'SELL'):
            return 'INVALID'
        if now <= now or now < minute * 60 + 29.5:
            pass
        else:
            None * 60
        if not expiry == minute * 60 + 60:
            return 'EXPIRED'
    # WARNING: Decompyle incomplete

    
    def _m1_quick_fo_worker(self, key, direction, expiry):
        pass
    # WARNING: Decompyle incomplete

    
    def submit_cluster_5s(self, signal):
        '''Independent 5-second Blitz path; retain existing FO/compensation/result state.'''
        if not self.enabled:
            return 'DISABLED'
        if None.get('direction') not in ('BUY', 'SELL'):
            return 'INVALID'
        if not signal.get('signal_id'):
            pass
        sid = None('')
        if not sid:
            return 'INVALID'
        created_mono = None.get('created_mono')
        if created_mono is not None and time.monotonic() - float(created_mono) > 0.5:
            return 'SKIP_STALE'
        if None is None and time.time() - float(signal.get('received_at', 0)) > 1.5:
            return 'STALE'
    # WARNING: Decompyle incomplete

    
    def _virtual_5s_worker(self, key, direction):
        pass
    # WARNING: Decompyle incomplete

    
    def submit(self, signal):
        if not self.enabled:
            return 'DISABLED'
        if None['direction'] not in ('BUY', 'SELL'):
            return 'INVALID'
        minute = None['minute']
        expiry = int(signal['expiry_ts'])
        entry_mode = signal.get('entry_mode', 'MASTER')
        if entry_mode in ('NEXT', 'CURRENT'):
            if not time.time() <= time.time() or time.time() < expiry - 5:
                pass
            else:
                minute * 60
            return 'EXPIRED'
    # WARNING: Decompyle incomplete

    
    def _virtual_worker(self, minute, expiry, entry_mode, direction):
        pass
    # WARNING: Decompyle incomplete

    
    def _result_worker(self, minute, oid, expiry):
        time.sleep(max(0, (expiry - time.time()) + 2))
    # WARNING: Decompyle incomplete


