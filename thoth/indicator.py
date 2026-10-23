# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. https://mozilla.org/MPL/2.0/
# Derived from the TFlab Pine indicator in reference/support_resistance.pine.
"""Causal replay of confirmed pivots and source major/minor promotion logic.

Experimental port: TradingView parity requires external reference exports.
No future bars are used at the historical pivot's chart timestamp.
"""
from .timeframes import interval_seconds
from math import nan, isfinite
from time import time

from .indicator_core import ZigZag


TYPES = {'MHL': 'support_major', 'MLL': 'support_major',
         'MHH': 'resistance_major', 'MLH': 'resistance_major',
         'mHL': 'support_minor', 'mLL': 'support_minor',
         'mHH': 'resistance_minor', 'mLH': 'resistance_minor'}


def extract_levels(points):
    found = {}
    for kind, value, index, stamp in reversed(points):
        key = TYPES.get(kind)
        if key and key not in found:
            found[key] = {'value': value, 'pivot_time': stamp}
    return found


def confirmed_pivot(rows, i, period, field, high):
    """Rightmost equal extreme wins: ties on the right delay confirmation."""
    if i < 2 * period:
        return nan, None
    center = i - period
    value = rows[center][field]
    left = [r[field] for r in rows[center-period:center]]
    right = [r[field] for r in rows[center+1:i+1]]
    valid = (all(value >= v for v in left) and all(value > v for v in right)) if high else (
        all(value <= v for v in left) and all(value < v for v in right))
    return (value, (value, center, rows[center]['time'])) if valid else (nan, None)


def evaluate(rows, cfg, now=None):
    now = time() if now is None else now
    duration = interval_seconds(cfg.timeframe)
    rows = sorted(rows, key=lambda r:r['time'])
    if len({r['time'] for r in rows}) != len(rows):
        raise ValueError('Duplicate candle timestamps')
    for r in rows:
        if any(not isfinite(r[k]) or r[k] <= 0 for k in ('open','high','low','close')):
            raise ValueError('Invalid candle price')
        if not (r['low'] <= min(r['open'],r['close']) <= max(r['open'],r['close']) <= r['high']):
            raise ValueError('Invalid candle range')
    if cfg.strategy=='rsi_hourly_intrabar':
        from .rsi import RSIState
        state=RSIState(cfg.rsi)
        return [state.step(row,duration) for row in rows if row['time']+duration<=now]
    if cfg.strategy=='sr_rsi_daily':
        from .rsi import RSIState
        from .daily_sr_rsi import combine
        sr=evaluate(rows,cfg.model_copy(update={'strategy':'sr_breakout'}),now)
        rsi=RSIState(cfg.rsi)
        return [combine(bar,rsi.step(row,duration)) for bar,row in zip(sr,rows)]
    if cfg.strategy=='sr_sma_7_50':
        from .sma_cross import SMACross,combine_sr_sma
        sr=evaluate(rows,cfg.model_copy(update={'strategy':'sr_breakout'}),now)
        cross=SMACross(slow_period=50)
        return [combine_sr_sma(a,cross.step(row,duration)) for a,row in zip(sr,rows)]
    if cfg.strategy in ('sma_cross','sma_7_50'):
        from .sma_cross import SMACross
        cross=SMACross(slow_period=50 if cfg.strategy=='sma_7_50' else 200)
        return [cross.step(row,duration) for row in rows if row['time']+duration<=now]
    machines = [ZigZag(), ZigZag()]
    high_info = [(nan, nan, nan), (nan, nan, nan)]
    low_info = [(nan, nan, nan), (nan, nan, nan)]
    previous = {}
    output = []
    total = 0.0
    for i, row in enumerate(rows):
        if row['time'] + duration > now:
            break  # Forming candles never modify indicator state.
        total += row['close']
        if i >= cfg.sma_length:
            total -= rows[i-cfg.sma_length]['close']
        average = total/cfg.sma_length if i >= cfg.sma_length-1 else None
        levels = {}
        for j, period in enumerate((cfg.pivot_long, cfg.pivot_short)):
            hi, info = confirmed_pivot(rows, i, period, 'high', True)
            if info: high_info[j] = info
            lo, info = confirmed_pivot(rows, i, period, 'low', False)
            if info: low_info[j] = info
            points = machines[j].step(hi, lo, high_info[j], low_info[j], row['close'])
            for key, value in extract_levels(points).items():
                levels[('long_' if j == 0 else 'short_')+key] = value
        bullish = bearish = False
        if i and rows[i-1]['time']+duration == row['time']:
            for key, level in levels.items():
                if key.endswith('_major') and not cfg.major: continue
                if key.endswith('_minor') and not cfg.minor: continue
                old = previous.get(key)
                if old is None: continue
                if 'resistance' in key and rows[i-1]['close'] <= old['value'] and row['close'] > level['value']:
                    bullish = True
                if 'support' in key and rows[i-1]['close'] >= old['value'] and row['close'] < level['value']:
                    bearish = True
        if cfg.sma_enabled:
            bullish = bullish and average is not None and row['close'] > average
            bearish = bearish and average is not None and row['close'] < average
        if cfg.sma_7_50_filter_enabled:
            window=rows[max(0,i-49):i+1]
            ready=len(window)==50 and all(b['time']-a['time']==duration for a,b in zip(window,window[1:]))
            fast=sum(r['close'] for r in window[-7:])/7 if ready else None
            slow=sum(r['close'] for r in window)/50 if ready else None
            bullish=bullish and ready and fast>slow
            bearish=bearish and ready and fast<slow
        # Conflicting signals are visible but do not silently choose a direction.
        signal = 'Long' if bullish and not bearish else 'Short' if bearish and not bullish else None
        if cfg.strategy=='sr_hourly_intrabar':signal=None  # OHLC replay cannot reconstruct sampled live crossings.
        output.append(dict(time=row['time'], levels=levels, sma=average, signal=signal,
                           conflict=bullish and bearish, close=row['close']))
        previous = levels
    return output
