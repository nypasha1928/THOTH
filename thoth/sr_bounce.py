# Derived from reference/support_resistance.pine (MPL-2.0, © TFlab).
"""Exact native Bounce/Rejection predicates; no breakout or extra entry filter."""
from math import isfinite


def evaluate(row,previous_close,levels,cfg):
    """Pine lines 629–637 and 656–664, with close[1] from the previous BAR."""
    close=float(row['close']);low=float(row['low']);high=float(row['high'])
    if previous_close is None:return dict(signal=None,entry_sources=[],volume_conflict=False,conflict=False)
    previous_close=float(previous_close)
    if not all(isfinite(value) for value in (close,low,high,previous_close)):
        raise ValueError('Invalid S/R Bounce/Rejection candle')
    sources=[];sides=set()
    for key,level in levels.items():
        if key.endswith('_major') and not cfg.major:continue
        if key.endswith('_minor'):continue
        value=float(level['value'])
        if not isfinite(value):continue
        if 'support' in key and low<=value and close>value and close>previous_close:
            sources.append('sr_bounce:'+key);sides.add('Long')
        if 'resistance' in key and high>=value and close<value and close<previous_close:
            sources.append('sr_rejection:'+key);sides.add('Short')
    side=next(iter(sides)) if len(sides)==1 else None
    return dict(signal=side,entry_sources=sorted(sources) if side else [],volume_conflict=len(sides)>1,conflict=len(sides)>1)


def observe(tracker,rows,levels,cfg,now,max_gap):
    """Existing causal live protections: baseline, fresh samples and once per level/bar."""
    start=int(now)//60*60
    if len(rows)<2 or rows[-1]['time']!=start or rows[-2]['time']!=start-60:
        raise ValueError('Current and previous minute candles required for Bounce/Rejection')
    raw=evaluate(rows[-1],rows[-2]['close'],levels,cfg)
    same=tracker.get('bar')==start
    fired=set(tracker.get('fired',[])) if same else set()
    continuous=(0<now-tracker.get('observed_at',0)<=max_gap and tracker.get('bar') in (start,start-60))
    candidates=[]
    for source in raw['entry_sources']:
        key=source.split(':',1)[1]
        # A just-discovered/changed pivot cannot manufacture a live touch signal.
        known=tracker.get('levels',{}).get(key)==levels[key]
        if continuous and known and source not in fired:candidates.append(source)
        else:fired.add(source)
    fired.update(candidates)
    emitted=raw['signal'] if candidates else None
    previous=tracker.get('observed_at')
    from .entry_diagnostics import qualification
    details=qualification(candidates,{s:dict(first_seen_at=now,first_seen_price=rows[-1]['close']) for s in candidates},
        now,rows[-1]['close'],previous) if emitted else None
    tracker.update(bar=start,observed_at=now,levels=levels,fired=sorted(fired))
    return dict(time=now,mark=rows[-1]['close'],signal=emitted,entry_sources=candidates if emitted else [],
        entry_diagnostics=details,volume_conflict=raw['volume_conflict'])
