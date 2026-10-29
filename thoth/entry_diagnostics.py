"""Sampled entry timing only. No order decisions or extra exchange requests."""
import json
import math
import sqlite3
from pathlib import Path


def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):return None


def observe_sources(tracker,active,now,price,bar):
    saved=tracker.get('timing_sources',{}) if tracker.get('timing_bar')==bar else {}
    saved={source:saved.get(source,dict(first_seen_at=now,first_seen_price=price)) for source in active}
    tracker.update(timing_sources=saved,timing_bar=bar)
    return saved


def qualification(sources,seen,now,price,previous):
    return dict(qualified_at=now,qualified_price=price,previous_observed_at=previous,
        sources={source:seen[source] for source in sources if source in seen})


def merged_qualification(reports,now,price):
    sources={}
    previous=[]
    for report in reports:
        details=report.get('entry_diagnostics') or {}
        sources.update(details.get('sources',{}))
        stamp=number(details.get('previous_observed_at'))
        if stamp is not None:previous.append(stamp)
    return dict(qualified_at=now,qualified_price=price,sources=sources,previous_observed_at=min(previous) if previous else None)


def measure(details,timing,side,stop):
    result={};skew=False
    def difference(name,end,start):
        nonlocal skew
        end=number(end);start=number(start)
        if end is not None and start is not None:
            if end<start:skew=True
            else:result[name]=end-start
    difference('bar_close_to_qualification_seconds',details.get('qualified_at'),details.get('signal_bar_closed_at'))
    difference('observation_gap_seconds',details.get('qualified_at'),details.get('previous_observed_at'))
    seen=[number(value.get('first_seen_at')) for value in details.get('sources',{}).values()]
    seen=[value for value in seen if value is not None]
    difference('volume_wait_seconds',details.get('qualified_at'),min(seen) if seen else None)
    difference('download_seconds',details.get('sample_received_at'),details.get('sample_requested_at'))
    difference('sample_to_processing_seconds',details.get('processed_at'),details.get('sample_received_at'))
    difference('queue_seconds',details.get('checks_started_at'),details.get('queued_at'))
    difference('entry_checks_seconds',details.get('checks_completed_at'),details.get('checks_started_at'))
    for stage in ('volume_recheck','instrument_lookup','ticker','leverage'):
        difference(stage+'_seconds',details.get(stage+'_completed_at'),details.get(stage+'_started_at'))
    difference('qualified_to_submit_seconds',timing.get('submit_started_at'),details.get('qualified_at'))
    difference('submit_to_ack_seconds',timing.get('acknowledged_at'),timing.get('submit_started_at'))
    difference('submit_to_order_created_seconds',timing.get('exchange_created_at'),timing.get('submit_started_at'))
    difference('submit_to_fill_observed_seconds',timing.get('fill_observed_at'),timing.get('submit_started_at'))
    price=number(details.get('qualified_price'));fill=number(timing.get('fill_price'));qty=number(timing.get('filled_qty'))
    if price and price>0 and fill and fill>0:
        move=(fill-price)*(1 if side=='Buy' else -1)
        result['entry_move_pct']=move/price*100
        if qty and qty>0:
            result['entry_move_dollars']=move*qty
            stop=number(stop)
            if stop and stop>0:result['entry_move_stop_pct']=move*qty/stop*100
    result['clock_order_warning']=skew
    return result


def read(path,limit=50,offset=0,symbol=None):
    path=Path(path);journal=path.with_suffix('.execution.sqlite')
    if not journal.exists():return dict(items=[],total=0,limit=limit,offset=offset)
    where="json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_entry_diagnostics') IS NOT NULL"
    args=[]
    if symbol:where+=" AND json_extract(payload,'$.symbol')=?";args.append(symbol)
    with sqlite3.connect('file:'+str(journal)+'?mode=ro',uri=True) as db:
        total=db.execute('SELECT COUNT(*) FROM execution WHERE '+where,args).fetchone()[0]
        rows=db.execute('SELECT link,payload,phase,stop_dollars,instrument,result FROM execution WHERE '+where+' ORDER BY rowid DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    trades={}
    if rows:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            marks=','.join('?' for _ in rows)
            trades={link:json.loads(payload) for link,payload in db.execute('SELECT link,payload FROM trade_history WHERE link IN ('+marks+')',[row[0] for row in rows])}
    from .signal_sources import source_label
    items=[]
    for link,payload,phase,stop,instrument,result in rows:
        payload=json.loads(payload);instrument=json.loads(instrument);result=json.loads(result)
        details=instrument['thoth_entry_diagnostics'];timing=result.get('entry_timing',{})
        items.append(dict(symbol=payload['symbol'],side=payload['side'],strategy=instrument.get('thoth_strategy'),phase=phase,
            sources=instrument.get('thoth_entry_sources',[]),source_label=source_label(instrument.get('thoth_entry_sources',[])),details=details,timing=timing,
            measurements=measure(details,timing,payload['side'],stop),close_reason=trades.get(link,{}).get('reason'),realized_pnl=trades.get(link,{}).get('pnl')))
    return dict(items=items,total=total,limit=limit,offset=offset)


def record_speed(db,symbol,strategy,now,price,session,max_gap=300):
    """Use the existing observation transaction; never download more market data."""
    price=number(price)
    if price is None or price<=0:return
    row=db.execute('SELECT payload FROM price_observations WHERE symbol=? AND strategy=?',(symbol,strategy)).fetchone()
    old=json.loads(row[0]) if row else {}
    gap=now-old.get('observed_at',now)
    item=dict(symbol=symbol,strategy=strategy,observed_at=now,price=price,session=session,
        change_pct=None,speed_pct_per_minute=None,gap_seconds=None)
    if old.get('session')==session and 0<gap<=max_gap and number(old.get('price')) and old['price']>0:
        change=(price-old['price'])/old['price']*100
        item.update(change_pct=number(change),speed_pct_per_minute=number(abs(change)*60/gap),gap_seconds=gap)
    db.execute('INSERT OR REPLACE INTO price_observations VALUES(?,?,?)',(symbol,strategy,json.dumps(item,allow_nan=False)))


def fast_movers(path,now,limit=50):
    path=Path(path);history=path.with_suffix('.candles.sqlite')
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        state=json.loads(db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()[0])
    cfg=state['config'];items=[];tracked=0;stale=0
    if history.exists():
        with sqlite3.connect('file:'+str(history)+'?mode=ro',uri=True) as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='price_observations'").fetchone()
            rows=db.execute('SELECT payload FROM price_observations WHERE strategy=?',(cfg['strategy'],)).fetchall() if exists else []
        selected=set(cfg['symbols'])
        for row, in rows:
            item=json.loads(row)
            if item['symbol'] not in selected:continue
            tracked+=1;age=now-item['observed_at']
            if not 0<=age<=300:stale+=1;continue
            if number(item.get('speed_pct_per_minute')) is None:continue
            item.pop('session',None);item['age_seconds']=age;items.append(item)
    items.sort(key=lambda item:item['speed_pct_per_minute'],reverse=True)
    return dict(items=items[:limit],total_ranked=len(items),tracked=tracked,stale_excluded=stale,strategy=cfg['strategy'],server_time=now)
