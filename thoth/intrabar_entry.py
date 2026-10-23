"""Sampled selected-timeframe breakouts of confirmed S/R; never replay intrabar extremes."""
import math
from .timeframes import interval_seconds

STRATEGY='sr_hourly_intrabar'
MAX_OBSERVATION_GAP=30


def observe_breakout(tracker,rows,levels,cfg,now):
    duration=interval_seconds(cfg.timeframe)
    start=int(now)//duration*duration
    if not rows or rows[-1]['time']!=start:
        raise ValueError('Selected timeframe forming candle unavailable')
    price=float(rows[-1]['close'])
    if not math.isfinite(price) or price<=0:raise ValueError('Invalid intrabar price')
    previous=tracker.get('price')
    fresh=0<now-tracker.get('observed_at',0)<=MAX_OBSERVATION_GAP
    adjacent=tracker.get('bar') in (start,start-duration)
    old_levels=tracker.get('levels',{})
    fired=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
    candidates=[]
    for key,level in levels.items():
        if key.endswith('_major') and not cfg.major:continue
        if key.endswith('_minor') and not cfg.minor:continue
        value=float(level['value'])
        old=old_levels.get(key)
        # New/changed pivot levels must first be observed, not treated as a cross.
        if previous is None or not fresh or not adjacent or old!=level or key in fired:continue
        side='Long' if 'resistance' in key and previous<=value<price else 'Short' if 'support' in key and previous>=value>price else None
        if side:candidates.append((key,side))
    closes=[float(row['close']) for row in rows]
    allowed={'Long','Short'}
    if cfg.sma_enabled:
        if len(closes)<cfg.sma_length:allowed.clear()
        else:
            average=sum(closes[-cfg.sma_length:])/cfg.sma_length
            allowed&=({'Long'} if price>average else {'Short'} if price<average else set())
    if cfg.sma_7_50_filter_enabled:
        if len(closes)<50:allowed.clear()
        else:
            difference=sum(closes[-7:])/7-sum(closes[-50:])/50
            allowed&=({'Long'} if difference>0 else {'Short'} if difference<0 else set())
    # Consume every observed crossing, including filtered/conflicting crossings.
    fired.update(key for key,_ in candidates)
    sides={side for _,side in candidates if side in allowed}
    signal=next(iter(sides)) if len(sides)==1 else None
    tracker.update(bar=start,observed_at=now,price=price,levels=levels,fired=sorted(fired))
    return dict(time=now,mark=price,signal=signal,entry_bar=start,entry_timeframe=cfg.timeframe,exit_timeframe='1')
