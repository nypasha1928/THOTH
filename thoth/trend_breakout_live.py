"""Separate closed 30m Major breakout entries aligned with completed 1h SMA200."""
from math import fsum,isfinite
from .indicator_stream import IndicatorState
from .trend_pullback import hourly,atr14
from .trend_pullback_live import MAX_DISTANCE_ATR,ENTRY_BUFFER_BPS

STRATEGY='trend_major_breakout_30m_close'


def evaluate_breakout(row,previous_close,levels):
    """One strict close cross of an already-known Major level; no touch signals."""
    if previous_close is None:return dict(signal=None,entry_sources=[],conflict=False)
    close=float(row['close']);previous=float(previous_close)
    if not all(isfinite(v) and v>0 for v in (close,previous)):raise ValueError('Invalid Major breakout candle')
    candidates=[]
    for key,level in levels.items():
        if not key.endswith('_major'):continue
        value=float(level['value'])
        if not isfinite(value) or value<=0:continue
        if 'resistance' in key and previous<=value<close:candidates.append(('sr:'+key,'Long'))
        elif 'support' in key and previous>=value>close:candidates.append(('sr:'+key,'Short'))
    sides={side for _,side in candidates};signal=next(iter(sides)) if len(sides)==1 else None
    return dict(signal=signal,entry_sources=sorted({src for src,side in candidates if side==signal}) if signal else [],conflict=len(sides)>1)


def qualify(rows,price,cfg,max_distance_atr=MAX_DISTANCE_ATR,slippage_bps=ENTRY_BUFFER_BPS):
    price=float(price)
    if not isfinite(price) or price<=0:raise ValueError('Breakout entry price must be positive and finite')
    result=dict(signal=None,reason='Insufficient closed hourly history')
    hours=hourly(rows)
    if len(hours)<200:return result
    hours=hours[-200:]
    if any(b['time']-a['time']!=3600 for a,b in zip(hours,hours[1:])):return result
    average=fsum(r['close'] for r in hours)/200;close=hours[-1]['close']
    result.update(sma200=average,hour_close=close,hour_closed_at=hours[-1]['time']+3600)
    if close==average:return {**result,'reason':'Hourly close equals SMA200'}
    direction='Long' if close>average else 'Short';sign=1 if direction=='Long' else -1
    source=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe='30',major=True,minor=False,sma_enabled=False,sma_7_50_filter_enabled=False))
    machine=IndicatorState(source);levels={}
    for row in rows[:-1]:levels=machine.step(row)['levels']
    raw=evaluate_breakout(rows[-1],rows[-2]['close'],levels);atr=atr14(rows)
    result.update(trend=direction,atr14=atr,signal_candle=rows[-1],levels_before=levels,sources=raw['entry_sources'])
    if raw['conflict'] or raw['signal']!=direction:return {**result,'reason':'No trend-aligned Major breakout'}
    if not atr or atr<=0:return {**result,'reason':'ATR unavailable'}
    entry=price*(1+sign*slippage_bps/10000)
    result.update(observed_entry_price=price,modeled_entry_price=entry,max_distance_atr=max_distance_atr,entry_buffer_bps=slippage_bps,source_distances=[])
    refs=[]
    for src in raw['entry_sources']:
        value=levels[src.split(':',1)[1]]['value'];distance=sign*(entry-value)/atr
        result['source_distances'].append(dict(source=src,boundary=value,distance_atr=distance))
        if 0<=distance<=max_distance_atr:refs.append((distance,src,value))
    if not refs:return {**result,'reason':'Entry outside allowed ATR distance'}
    distance,src,boundary=min(refs)
    return {**result,'signal':direction,'reason':'Would enter: trend + Major breakout confirmed','source':src,'boundary':boundary,'distance_atr':distance,'entry':entry}


def observe(runner,symbol,cfg,rows,now,received_at):
    from .trend_pullback_live import observe as observe_confirmed
    return observe_confirmed(runner,symbol,cfg,rows,now,received_at,breakout=True)


def recheck(side,context,now,price):
    from .trend_pullback_live import recheck as recheck_confirmed
    source=((context or {}).get('trend_breakout') or {}).get('source','')
    if not (source.startswith('sr:') and source.endswith('_major') and ('resistance' in source if side=='Long' else 'support' in source)):
        raise ValueError('Trend + Major Breakout confirmed Major breakout source unavailable')
    return recheck_confirmed(side,context,now,price,breakout=True)
