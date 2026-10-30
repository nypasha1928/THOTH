"""Read-only closed-hour candle/entry measurements; never a trading filter."""
from .entry_diagnostics import number


def _snapshot(rows,side,sources,sr=None,ob=None,qualified_at=None):
    if not rows or side not in ('Long','Short'):return None
    candle=rows[-1]
    values={key:number(candle.get(key)) for key in ('open','high','low','close')}
    if any(value is None or value<=0 for value in values.values()):return None
    op,hi,lo,close=(values[key] for key in ('open','high','low','close'))
    if not lo<=min(op,close)<=max(op,close)<=hi:return None
    # Wilder ATR14 includes the signal candle, with the first high-low as TR.
    atr=None;seed=[];previous=None;last_time=None
    for row in rows:
        high=number(row.get('high'));low=number(row.get('low'));price=number(row.get('close'))
        if high is None or low is None or price is None or low<=0 or high<low:return None
        if last_time is not None and row['time']!=last_time+3600:
            atr=None;seed=[];previous=None
        tr=high-low if previous is None else max(high-low,abs(high-previous),abs(low-previous))
        if atr is None:
            seed.append(tr)
            if len(seed)==14:atr=sum(seed)/14
        else:atr=(atr*13+tr)/14
        previous=price;last_time=row['time']
    span=hi-lo;direction=1 if side=='Long' else -1
    references=[]
    from .signal_sources import source_label
    def add(source,price,kind,stamp=None):
        price=number(price)
        if price is None or price<=0:return
        references.append(dict(source=source,label=source_label([source]),kind=kind,price=price,pivot_time=stamp,
            close_distance_pct=direction*(close-price)/price*100))
    for source in sources:
        if source.startswith('sr:'):
            level=(sr or {}).get('levels',{}).get(source[3:])
            if level:add(source,level['value'],'broken_sr_level',level.get('pivot_time'))
        elif source=='ob:supply_breakout':
            for zone in (ob or {}).get('broken_supply',[]):add(source,zone.get('high'),'broken_supply_high',zone.get('time'))
        elif source=='ob:demand_breakdown':
            for zone in (ob or {}).get('broken_demand',[]):add(source,zone.get('low'),'broken_demand_low',zone.get('time'))
        elif source in ('ob:bullish_formed','ob:bearish_formed'):
            zones=(ob or {}).get('zones',{}).get('demand' if source.endswith('bullish_formed') else 'supply',[])
            if zones:
                zone=zones[0]
                add(source,zone.get('high' if source.endswith('bullish_formed') else 'low'),'formed_block_edge',zone.get('time'))
    closed_at=candle['time']+3600;qualified=number(qualified_at)
    return dict(version=1,timeframe='60',bar=candle['time'],closed_at=closed_at,side=side,ohlc=values,
        atr14=atr,atr_basis='Wilder ATR14 including signal candle',range=span,range_atr14=span/atr if atr and atr>0 else None,
        opposing_wick_pct=(hi-max(op,close) if side=='Long' else min(op,close)-lo)/span*100 if span>0 else None,
        qualification_after_close_seconds=qualified-closed_at if qualified is not None and qualified>=closed_at else None,
        references=references)


def _with_fill(snapshot,timing):
    if not snapshot:return None
    result={**snapshot,'references':[dict(item) for item in snapshot.get('references',[])]}
    fill=number(timing.get('fill_price'));close=number(snapshot.get('ohlc',{}).get('close'))
    direction=1 if snapshot.get('side')=='Long' else -1
    if fill is not None and fill>0 and close is not None and close>0:
        result.update(fill_price=fill,close_to_fill_pct=direction*(fill-close)/close*100)
        for item in result['references']:
            item['entry_distance_pct']=direction*(fill-item['price'])/item['price']*100
    observed=number(timing.get('fill_observed_at'))
    if observed is None:observed=number(timing.get('fill_confirmed_at'))
    closed=number(snapshot.get('closed_at'))
    if observed is not None and closed is not None and observed>=closed:
        result['close_to_fill_observed_seconds']=observed-closed
    return result


def snapshot(rows,side,sources,sr=None,ob=None,qualified_at=None):
    # Optional diagnostics must not suppress a valid signal or account management.
    try:return _snapshot(rows,side,sources,sr,ob,qualified_at)
    except (TypeError,ValueError,KeyError,AttributeError,ArithmeticError):return None


def with_fill(snapshot,timing):
    try:return _with_fill(snapshot,timing)
    except (TypeError,ValueError,KeyError,AttributeError,ArithmeticError):return None
