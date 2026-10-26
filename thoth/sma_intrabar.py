"""Sampled 4-hour SMA 7/50 crossings, with no candle-close wait or replay."""
import math
STRATEGY='sma_7_50_intrabar'


def observe(tracker,rows,now,max_gap=30):
    start=int(now)//14400*14400
    if len(rows)<50 or rows[-1]['time']!=start:raise ValueError('Current 4h SMA candle/history unavailable')
    window=rows[-50:]
    if any(b['time']-a['time']!=14400 for a,b in zip(window,window[1:])):raise ValueError('4h SMA history contains a gap')
    closes=[float(row['close']) for row in window]
    if any(not math.isfinite(value) or value<=0 for value in closes):raise ValueError('Invalid SMA price')
    fast=sum(closes[-7:])/7;slow=sum(closes)/50;difference=fast-slow
    fresh=0<now-tracker.get('observed_at',0)<=max_gap and tracker.get('bar') in (start,start-14400)
    previous=tracker.get('difference');signal=None
    if fresh and previous is not None:
        if previous<=0<difference:signal='Long'
        elif previous>=0>difference:signal='Short'
    tracker.update(bar=start,observed_at=now,difference=difference)
    return dict(time=now,mark=closes[-1],signal=signal,sma7=fast,sma50=slow,
        entry_sources=['sma:7_50'] if signal else [],entry_timeframe='240',exit_timeframe='none')
