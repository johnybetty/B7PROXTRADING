# Source Generated with Decompyle++
# File: pc_strategy_mixin.pyc (Python 3.8)

import time
import json
import traceback
import threading
from threading import Event, Lock
from collections import deque

class PCStrategyMixin:
    
    def _receive_open_price_pattern(self):
        '''M1 open-relative tick patterns; one CURRENT and optional NEXT per cycle.'''
        evaluate = evaluate
        inspect = inspect
        PatternConfig = PatternConfig
        import open_price_pattern
        Lock = Lock
        import threading
        broker = self.executor.broker
        cfg = PatternConfig()
        result_lock = Lock()
        outcomes = { }
        settled_events = []
        order_kinds = { }
        locked = set()
        cooldown_end = None
        seen_results = set()
        skipped = set()
        original = self.executor.on_update
        
        def capture(key = None, fields = None):
            original(key, fields)
        # WARNING: Decompyle incomplete

        self.executor.on_update = capture
        active = None
        current_key = None
        next_plan = None
        next_done = False
        submitted_current = set()
        analysis_logged = set()
        self.q.put(('log', 'OPEN PRICE PATTERN | LOSE GUARD ON | P1/P2/P3 separate locks | all lose => 10 candles SIGNAL ONLY'))
    # WARNING: Decompyle incomplete

    
    def _receive_range_reaction(self):
        '''One order per M1; only confirmed broker results determine the next side.'''
        median = median
        import statistics
        deque = deque
        import collections
        broker = self.executor.broker
        direction = 'BUY'
        previous_key = None
        waiting_color = False
        next_entry_mode = 'START BUY'
        loss_samples = deque(20, **('maxlen',))
        entry_prices = { }
        pending_outcomes = { }
        self.q.put(('log', 'RANGE REACTION | START BUY | entry 00-15s after broker result | DEMO first'))
        original_callback = self.executor.on_update
        
        def capture(key = None, fields = None):
            original_callback(key, fields)
            status = fields.get('broker_status')
            if status in ('WIN', 'LOSE', 'DRAW'):
                pending_outcomes[key] = status

        self.executor.on_update = capture
    # WARNING: Decompyle incomplete

    
    def _receive_pattern(self, mode):
        '''Retain CURRENT/NEXT signals while previous known order settles.

        Unknown WS order state MUST NOT be auto-released: broker might have
        accepted it even without a UUID. Reconcile manually first.
        '''
        seen = set()
        queued = None
    # WARNING: Decompyle incomplete


