"""BOT X signal-only tick bridge; broker orders deliberately disabled."""
import json
from pattern_engine import SignalEngine, Tick

_engines = {}
_last = {}

def reset():
    _engines.clear()
    _last.clear()
    return json.dumps({
        'status': 'READY',
        'mode': 'SIGNAL_ONLY',
        'orders_enabled': False
    })

def tick(candle_id, second, price):
    return tick_pair('Z-CRY/IDX', candle_id, second, price)

def tick_pair(pair, candle_id, second, price):
    try:
        pair = str(pair).strip()
        if not pair or len(pair) > 64:
            raise ValueError('invalid pair')
        candle_id = int(candle_id)
        second = float(second)
        price = float(price)
        if not (0 <= second < 60) or not (0 < price < float('inf')):
            raise ValueError('invalid tick values')
        
        previous = _last.get(pair)
        if previous is not None:
            prev_candle, prev_sec = previous
            if (candle_id < prev_candle) or (candle_id == prev_candle and second <= prev_sec):
                return json.dumps({
                    'status': 'WAIT',
                    'pair': pair,
                    'reason': 'STALE_TICK',
                    'orders_enabled': False
                })
        
        engine = _engines.setdefault(pair, SignalEngine())
        result = engine.on_tick(Tick(candle_id, second, price))
        _last[pair] = (candle_id, second)
        
        if result:
            return json.dumps({
                'status': 'SIGNAL',
                'pair': pair,
                'signal': result.__dict__,
                'orders_enabled': False
            })
        else:
            return json.dumps({
                'status': 'WAIT',
                'pair': pair,
                'orders_enabled': False
            })
    except Exception as exc:
        return json.dumps({
            'status': 'ERROR',
            'message': str(exc),
            'orders_enabled': False
        })

from open_price_pattern import inspect, PatternConfig
from collections import deque

_pc_ticks = {}
_pc_last = {}
_pc_emitted = set()

def reset_pc():
    _pc_ticks.clear()
    _pc_last.clear()
    _pc_emitted.clear()
    return json.dumps({
        'status': 'READY',
        'mode': 'PC_PATTERN_DIAGNOSTIC',
        'orders_enabled': False
    })

def pc_tick(pair, candle_id, second, price):
    try:
        pair = str(pair).strip()
        candle_id = int(candle_id)
        second = float(second)
        price = float(price)
        if not pair or len(pair) > 64 or not (0 <= second < 60) or not (0 < price < float('inf')):
            raise ValueError('Invalid tick')
        
        previous = _pc_last.get(pair)
        if previous is not None:
            if (candle_id < previous[0]) or (candle_id == previous[0] and second <= previous[1]):
                return json.dumps({
                    'status': 'WAIT',
                    'reason': 'STALE_TICK',
                    'pair': pair,
                    'orders_enabled': False
                })
            if previous[0] != candle_id:
                _pc_ticks.pop(pair, None)
        
        ticks = _pc_ticks.setdefault(pair, deque(maxlen=10000))
        ticks.append((second, price))
        _pc_last[pair] = (candle_id, second)
        
        info = inspect(ticks, second, PatternConfig())
        result = {
            'status': 'WAIT',
            'pair': pair,
            'candle_id': candle_id,
            'second': second,
            'reason': info['reason'],
            'crossings': info['crossings'],
            'sample_count': len(ticks),
            'first_second': round(ticks[0][0], 2),
            'last_second': round(second, 2),
            'side': info['side'],
            'one_way': info['one_way'],
            'orders_enabled': False
        }
        
        if info['pattern'] and (pair, candle_id) not in _pc_emitted:
            pattern, direction, reason = info['pattern']
            _pc_emitted.add((pair, candle_id))
            result.update(
                status='SIGNAL',
                signal={
                    'candle_id': candle_id,
                    'pattern': pattern,
                    'direction': direction,
                    'reason': reason
                }
            )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({
            'status': 'ERROR',
            'message': str(exc),
            'orders_enabled': False
        })
