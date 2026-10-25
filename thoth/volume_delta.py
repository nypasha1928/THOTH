"""Uploaded Pine's default 1-minute LTF candle-volume proxy (not trade delta).

No exchange writes. Parent timeframe and lower-timeframe candles are supplied by
callers. This module implements only Volume Delta entries and SMA alignment.
"""
from math import isfinite
from .timeframes import interval_seconds


def signal(rows,minute_rows,interval,now,*,sma_filter=True,bull_type='strong',bear_type='strong',bullish_enabled=True,bearish_enabled=True):
    duration=interval_seconds(interval);start=int(now)//duration*duration
    if not rows or rows[-1]['time']!=start:raise ValueError('Current Volume Delta candle unavailable')
    current=rows[-1]
    for row in rows:
        for key in ('open','close','volume'):
            value=float(row[key])
            if not isfinite(value) or value<0 or key!='volume' and value==0:
                raise ValueError('Invalid Volume Delta candle')
    volume=float(current['volume'])
    result=dict(time=now,bar=start,signal=None,entry_sources=[],bull_volume=None,bear_volume=None,
        delta=None,normalized_delta=None,sma7=None,sma50=None,ready=False,status='warming up')
    if volume==0:return {**result,'status':'Zero-volume candle; no delta signal'}
    if interval=='1':lower=[current]
    else:
        lower=sorted([row for row in minute_rows if start<=row['time']<=int(now)//60*60],key=lambda row:row['time'])
        expected=set(range(start,int(now)//60*60,60))
        if not lower or not expected.issubset({row['time'] for row in lower}):
            raise ValueError('Incomplete lower-timeframe Volume Delta history')
        if len({row['time'] for row in lower})!=len(lower) or any(row['time']%60 for row in lower):
            raise ValueError('Invalid lower-timeframe candle timestamps')
    bull=bear=0.
    for row in lower:
        op,close,vol=(float(row[key]) for key in ('open','close','volume'))
        if not all(isfinite(value) for value in (op,close,vol)) or op<=0 or close<=0 or vol<0:
            raise ValueError('Invalid lower-timeframe Volume Delta data')
        if close>op:bull+=vol
        elif close<op:bear+=vol
    delta=bull-bear;norm=delta/volume
    closes=[float(row['close']) for row in rows]
    fast=sum(closes[-7:])/7 if len(closes)>=7 else None
    slow=sum(closes[-50:])/50 if len(closes)>=50 else None
    ready=not sma_filter or fast is not None and slow is not None
    if sma_filter and (not ready or any(b['time']-a['time']!=duration for a,b in zip(rows[-50:-1],rows[-49:]))):
        return {**result,'bull_volume':bull,'bear_volume':bear,'delta':delta,'normalized_delta':norm,'status':'SMA 7/50 history unavailable'}
    if bull_type not in ('strong','weak','all') or bear_type not in ('strong','weak','all'):
        raise ValueError('Unsupported Volume Delta candle type')
    up=current['close']>current['open'];down=current['close']<current['open']
    bullish=bullish_enabled and up and (norm>0 if bull_type=='strong' else norm<0 if bull_type=='weak' else True)
    bearish=bearish_enabled and down and (norm<0 if bear_type=='strong' else norm>0 if bear_type=='weak' else True)
    bullish=bullish and (not sma_filter or fast>slow)
    bearish=bearish and (not sma_filter or fast<slow)
    direction='Long' if bullish else 'Short' if bearish else None
    return {**result,'signal':direction,'entry_sources':['volume_delta:'+('bullish_'+bull_type if direction=='Long' else 'bearish_'+bear_type)] if direction else [],
        'bull_volume':bull,'bear_volume':bear,'delta':delta,'normalized_delta':norm,'sma7':fast,'sma50':slow,
        'ready':True,'status':'1-minute candle-volume proxy; SMA 7/50 '+('enabled' if sma_filter else 'off')}


def lower_inputs(fetch,symbol,interval,rows,now,known=()):
    """At most two 1m pages cover the current supported entry candle."""
    if interval=='1':return []
    start=rows[-1]['time']
    page=fetch(symbol,'1',limit=1000)
    merged={row['time']:row for row in known if row['time']>=start}
    merged.update({row['time']:row for row in page if row['time']>=start})
    required=set(range(start,int(now)//60*60,60))
    if page and page[0]['time']>start and not required.issubset(merged):
        older=fetch(symbol,'1',limit=1000,end=page[0]['time']*1000-1)
        merged.update({row['time']:row for row in older if start<=row['time']<page[0]['time']})
    return sorted(merged.values(),key=lambda row:row['time'])


def reference_history(rows,minute_rows,interval,now,**settings):
    """Closed-candle references only where every actual 1m candle is present."""
    duration=interval_seconds(interval)
    groups={}
    for row in minute_rows:groups.setdefault(int(row['time'])//duration*duration,[]).append(row)
    output=[]
    for i,row in enumerate(rows):
        if row['time']+duration>now:continue
        lower=groups.get(row['time'],[])
        if not lower:continue
        expected=set(range(row['time'],row['time']+duration,60))
        if not expected.issubset({bar['time'] for bar in lower}):continue
        try:
            result=signal(rows[max(0,i-49):i+1],lower,interval,row['time']+duration-1,**settings)
        except ValueError:continue
        output.append(dict(time=row['time'],levels={},sma=None,signal=result['signal'],conflict=False,entry_sources=result['entry_sources']))
    return output
