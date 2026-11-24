"""Open-relative M1 tick patterns. Thresholds are measured in observed price steps."""
from dataclasses import dataclass
from statistics import median

@dataclass
class PatternConfig:
    min_ticks: int = 12
    min_crossings: int = 2
    pullback_steps: float = 1.0
    sharp_steps: float = 3.0
    sharp_window_seconds: float = 4.0
    min_step: float = 1e-07

def inspect(ticks, second, cfg=None):
    if not cfg:
        cfg = PatternConfig()
    
    samples = sorted(((t, p) for t, p in ticks if t <= second))
    info = {
        'pattern': None,
        'reason': 'WAIT',
        'crossings': 0,
        'side': 'NEUTRAL',
        'one_way': False,
        'step': cfg.min_step,
        'pullback': 0.0
    }
    
    if not samples:
        info['reason'] = 'NO_TICKS'
        return info
    
    if samples[0][0] > 2:
        info['reason'] = 'OPEN_MISSING'
        return info
    
    if len(samples) < cfg.min_ticks:
        info['reason'] = 'LOW_TICKS'
        return info
    
    op = samples[0][1]
    moves = [abs(b[1] - a[1]) for a, b in zip(samples, samples[1:]) if abs(b[1] - a[1]) > 0]
    if moves:
        step = median(sorted(moves)[:max(1, len(moves) // 2)])
    else:
        step = cfg.min_step
    step = max(step, 1e-12)
    info['step'] = step
    
    def sign(p):
        diff = p - op
        if abs(diff) < step * 0.5:
            return 0
        return 1 if diff > 0 else -1
    
    nz = [sign(p) for t, p in samples if sign(p) != 0]
    info['crossings'] = sum(1 for a, b in zip(nz, nz[1:]) if a != b)
    
    last = sign(samples[-1][1])
    if last > 0:
        info['side'] = 'ABOVE'
    elif last < 0:
        info['side'] = 'BELOW'
    else:
        info['side'] = 'NEUTRAL'
    
    early = [sign(p) for t, p in samples if t <= 20 and sign(p) != 0]
    one_way = len(early) >= 5 and len(set(early)) == 1
    info['one_way'] = one_way
    
    if second < 20:
        info['reason'] = 'OBSERVING'
        return info
    
    # P1 Pattern: One-Way trend with pullback
    if one_way and 20 <= second <= 27.8:
        direction = early[-1]
        if last == direction:
            window_prices = [p for t, p in samples if t >= 20]
            if not window_prices:
                info['reason'] = 'P1_WAIT_WINDOW_TICKS'
                return info
            extreme = max(window_prices) if direction == 1 else min(window_prices)
            pullback = (extreme - samples[-1][1]) if direction == 1 else (samples[-1][1] - extreme)
            info['pullback'] = pullback / step
            if pullback / step >= cfg.pullback_steps or second >= 27:
                dir_label = "BUY" if direction == 1 else "SELL"
                reason_label = f"one-way pullback_steps={pullback / step:.2f}" if pullback / step >= cfg.pullback_steps else "one-way fallback 27s"
                info['pattern'] = ('P1', dir_label, reason_label)
                info['reason'] = 'MATCH'
                return info
            else:
                info['reason'] = 'P1_WAIT_PULLBACK'
                return info
    
    # P3 Pattern: Sharp Reversal
    if 23 <= second <= 27.8 and last != 0:
        prior = [(t, p) for t, p in samples if t <= second - cfg.sharp_window_seconds]
        recent = [(t, p) for t, p in samples if t >= second - cfg.sharp_window_seconds]
        if prior and recent:
            previous_side = sign(prior[-1][1])
            displacement = abs(samples[-1][1] - prior[-1][1]) / step
            if previous_side == -last and displacement >= cfg.sharp_steps:
                dir_label = "BUY" if last == 1 else "SELL"
                info['pattern'] = ('P3', dir_label, f"sharp reversal steps={displacement:.2f}")
                info['reason'] = 'MATCH'
                return info
    
    # P2 Pattern: Mean Reversion / Crossings
    if 25 <= second <= 27.8 and info['crossings'] >= cfg.min_crossings and last != 0:
        dir_label = "SELL" if last == 1 else "BUY"
        info['pattern'] = ('P2', dir_label, f"crossings={info['crossings']}")
        info['reason'] = 'MATCH'
        return info
    
    info['reason'] = 'NO_MATCH'
    return info

def evaluate(ticks, second, cfg=None):
    return inspect(ticks, second, cfg).get('pattern')
