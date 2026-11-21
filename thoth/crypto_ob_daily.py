"""Closed-hour strength OB entries confirmed by the last completed Daily SMA7/50.

Daily data is entry-only: hourly opposite formations remain sufficient for exits.
The daily boundary is the exchange's UTC midnight, not New York midnight.
"""
from math import fsum, isfinite
from .crypto_ob_hours import (
    StrengthState as HourState, _validate, THRESHOLDS, ENTRY_THRESHOLDS,
    EXIT_THRESHOLDS, ZERO_STRENGTH_SYMBOLS, LIST, BLOCKED_HOURS, TIMEZONE,
    entry_block_reason, POLICY as HOURS_POLICY,
)

STRATEGY = 'crypto_ob_strength_daily_sma750_hours'
NAME = 'Crypto OB · 1h Close · Daily SMA7/50 · 6-Hour Entry Block'
POLICY = {k:v for k,v in HOURS_POLICY.items() if k!='sma_period'}
POLICY.update(strategy=STRATEGY, confirmation_timeframe='D', fast_period=7,
              slow_period=50, confirmation_basis='last 50 consecutive completed UTC Daily closes',
              crossover_required=False, equality='block', insufficient_data='block',
              sma200=False)

class StrengthState(HourState):
    policy = STRATEGY

    def step(self, row):
        result = super().step(row)
        details = result['crypto_ob_strength']
        details.pop('sma200', None)
        details['basis'] = 'raw quote-price width; CLOSED 1h OB; separate completed Daily SMA7/50 entry permission'
        # No hourly SMA is an entry permission in this profile.
        result['sma'] = None
        if result['signal_diagnostic'] is not None:
            result['signal_diagnostic'] = {**details, 'reason':'CLOSED 1h OB; Daily permission required for entry only'}
        return result

def daily_confirmation(rows, now):
    """Reject stale, future, nonfinite or gapped Daily evidence; exclude forming D."""
    boundary = int(now)//86400*86400
    closed = sorted((r for r in rows if r['time']+86400<=now), key=lambda r:r['time'])[-50:]
    if len(closed)!=50 or [r['time'] for r in closed]!=list(range(boundary-50*86400,boundary,86400)):
        raise ValueError('Daily SMA7/50: need 50 consecutive completed Daily candles through latest UTC close')
    values = [float(r['close']) for r in closed]
    if not all(isfinite(v) and v>0 for v in values):
        raise ValueError('Daily SMA7/50: invalid completed close')
    fast, slow = fsum(values[-7:])/7, fsum(values)/50
    return dict(timeframe='D', signal_bar=boundary-86400, closed_at=boundary,
                fast_period=7, slow_period=50, closes=values, sma7=fast, sma50=slow,
                direction='Long' if fast>slow else 'Short' if fast<slow else None)

def validate_exit(symbol, side, context, now):
    return _validate(symbol, side, context, now, 'exit', policy=STRATEGY)

def validate_entry(symbol, side, context, now):
    if symbol in ZERO_STRENGTH_SYMBOLS:
        raise ValueError('Zero OB minimum strength: '+symbol+' new entries disabled; existing exits remain active')
    details = _validate(symbol, side, context, now, 'entry', policy=STRATEGY)
    reason = entry_block_reason(now)
    if reason: raise ValueError(reason)
    confirmation = details.get('daily_confirmation') or {}
    try:
        boundary = int(now)//86400*86400
        if (confirmation.get('timeframe')!='D' or confirmation.get('signal_bar')!=boundary-86400
                or confirmation.get('closed_at')!=boundary or confirmation.get('fast_period')!=7
                or confirmation.get('slow_period')!=50 or len(confirmation['closes'])!=50):
            raise ValueError('stale/missing Daily evidence')
        # Recompute the recorded means; never trust an unverified direction label.
        rows=[dict(time=boundary-(50-i)*86400,close=v) for i,v in enumerate(confirmation['closes'])]
        checked=daily_confirmation(rows,now)
        valid=(checked['direction']==side and confirmation.get('sma7')==checked['sma7']
               and confirmation.get('sma50')==checked['sma50'] and confirmation.get('direction')==side)
    except (KeyError,TypeError,ValueError,OverflowError): valid=False
    if not valid:
        raise ValueError('Daily SMA7/50 entry permission missing, stale, equal or opposite')
    return details
