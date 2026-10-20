"""Causal SMA 7/50 exits using the current (unfinished) exchange candle."""
import math


def observe_exit(tracker,rows,timeframe,side,now):
    duration=60 if timeframe=='1' else 86400
    start=int(now)//duration*duration
    rows=sorted(rows,key=lambda r:r['time'])
    if len(rows)<51 or rows[-1]['time']!=start:
        raise ValueError('Fresh forming candle and 50 prior candles required for SMA exits')
    rows=rows[-51:]
    if any(b['time']-a['time']!=duration for a,b in zip(rows,rows[1:])):
        raise ValueError('Candle gap in SMA exit observations')
    values=[float(r['close']) for r in rows]
    if any(not math.isfinite(v) or v<=0 for v in values):raise ValueError('Invalid SMA exit price')
    previous=sum(values[-8:-1])/7-sum(values[:-1])/50
    current=sum(values[-7:])/7-sum(values[-50:])/50
    if tracker.get('timeframe')==timeframe and tracker.get('time')==start:
        previous=tracker['difference']
    tracker.update(time=start,timeframe=timeframe,difference=current,checked_at=now)
    if side=='Buy' and previous>=0 and current<0:return 'SMA 7 crossed below SMA 50 (intrabar exit)'
    if side=='Sell' and previous<=0 and current>0:return 'SMA 7 crossed above SMA 50 (intrabar exit)'
    return None
