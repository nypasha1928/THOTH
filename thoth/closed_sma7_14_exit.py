"""SMA7/SMA30 crossover on completed 1m candles; close-only, never an entry signal."""
from decimal import Decimal,InvalidOperation
from math import isfinite
from .intrabar_exit import ExitDataError,CANDLE_PUBLICATION_GRACE


def observe(tracker,rows,side,now,entered_at,*,sampled_at=None):
    latest=int(now)//60*60-60
    if sampled_at is None or not isfinite(sampled_at) or not latest+60<=sampled_at<=now or now-sampled_at>10:
        raise ExitDataError('fresh_post_close_minute_snapshot_required')
    times=[r.get('time') for r in rows]
    if any(not isinstance(t,(int,float)) or not isfinite(t) or t%60 or t>latest+60 for t in times) or len(set(times))!=len(times):
        raise ExitDataError('invalid_or_future_minute_timestamps')
    closed=sorted((r for r in rows if r.get('time',latest+60)<=latest),key=lambda r:r['time'])
    tracker['checked_at']=now
    if not closed or closed[-1]['time']!=latest:
        if closed and closed[-1]['time']==latest-60 and now%60<=CANDLE_PUBLICATION_GRACE:
            tracker['waiting_for_candle']=latest;return None
        raise ExitDataError('latest_closed_1m_candle_unavailable')
    candles=closed[-31:]
    if len(candles)!=31 or [r['time'] for r in candles]!=list(range(latest-1800,latest+60,60)):
        raise ExitDataError('thirty_one_consecutive_closed_1m_candles_required')
    try:
        prices=[Decimal(str(r['close'])) for r in candles]
        if any(not p.is_finite() or p<=0 for p in prices):raise ValueError()
    except (InvalidOperation,KeyError,ValueError,TypeError):raise ExitDataError('invalid_closed_1m_price') from None
    if side not in ('Buy','Sell'):raise ExitDataError('invalid_position_side')
    tracker.pop('waiting_for_candle',None)
    if latest+60<=entered_at:
        tracker['waiting_for_post_entry_close']=True;return None
    tracker.pop('waiting_for_post_entry_close',None)
    if tracker.get('last_bar',-1)>=latest:return None
    previous_fast=sum(prices[-8:-1])/Decimal(7);previous_slow=sum(prices[-31:-1])/Decimal(30)
    fast=sum(prices[-7:])/Decimal(7);slow=sum(prices[-30:])/Decimal(30)
    before=previous_fast-previous_slow;after=fast-slow
    tracker.update(last_bar=latest,previous_sma7=str(previous_fast),previous_sma30=str(previous_slow),sma7=str(fast),sma30=str(slow))
    if side=='Buy' and before>=0 and after<0:return 'Closed 1m SMA 7 crossed below SMA 30 (Long exit)'
    if side=='Sell' and before<=0 and after>0:return 'Closed 1m SMA 7 crossed above SMA 30 (Short exit)'
    return None
