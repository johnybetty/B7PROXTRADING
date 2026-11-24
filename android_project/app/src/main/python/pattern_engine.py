# uncompyle6 version 3.9.3
# Python bytecode version base 3.8.0 (3413)
# Decompiled from: Python 3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2025, 16:15:46) [MSC v.1943 64 bit (AMD64)]
# Embedded file name: pattern_engine.py
# Compiled at: 2026-11-12 20:24:15
# Size of source mod 2**32: 2133 bytes
"""BOT X V0.11: isolated Python signal-only engine for integration tests.
No network, no broker orders, no VIP access. Never use as trading advice.
"""
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Tick:
    candle_id: int
    second: float
    price: float


@dataclass(frozen=True)
class Signal:
    candle_id: int
    pattern: str
    direction: str
    reason: str


class SignalEngine:
    __doc__ = "Minimal P2 crossing detector, not the production PC strategy."

    def __init__(self):
        self.candle_id = None
        self.open_price = None
        self.last_side = 0
        self.crossings = 0
        self.emitted = False

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        if not 0 <= tick.second < 60:
            raise ValueError("second must be in [0,60)")
        elif tick.price <= 0:
            raise ValueError("price must be positive")
        elif self.candle_id != tick.candle_id:
            self.candle_id = tick.candle_id
            self.open_price = tick.price
            self.last_side = 0
            self.crossings = 0
            self.emitted = False
        else:
            side = (tick.price > self.open_price) - (tick.price < self.open_price)
            if side:
                if self.last_side:
                    if side != self.last_side:
                        if tick.second <= 25:
                            self.crossings += 1
            if side:
                self.last_side = side
            if not self.emitted:
                if 25 <= tick.second <= 27:
                    if self.crossings >= 2:
                        if side:
                            self.emitted = True
                            return Signal(tick.candle_id, "P2 PROTOTYPE", "SELL" if side > 0 else "BUY", f"crossings={self.crossings}")
        return


if __name__ == "__main__":
    import json, sys
    engine = SignalEngine()
    for line in sys.stdin:
        try:
            raw = json.loads(line)
            signal = engine.on_tick(Tick(int(raw["candle_id"]), float(raw["second"]), float(raw["price"])))
            print((json.dumps({'type':"SIGNAL",  'signal':signal.__dict__} if signal else {"type": "WAIT"})), flush=True)
        except Exception as exc:
            try:
                print((json.dumps({'type':"ERROR",  'message':str(exc)})), flush=True)
            finally:
                exc = None
                del exc
