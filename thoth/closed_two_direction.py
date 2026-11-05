"""Confirmed 30m permission and confirmed 1m OB/SR sources; no exchange writes."""
import json
from .database import connect
from .indicator_stream import IndicatorState,validate
from .order_block import OrderBlockState,select
from .sr_bounce import evaluate as bounce
from .timeframes import interval_seconds
from .verification import strategy_fingerprint

STRATEGY='ob_sr_30m_close_two_direction'


def combine_closed(ob,sr,row,previous_close,cfg):
    native=bounce(row,previous_close,sr.get('levels',{}),cfg)
    candidates=[('ob:'+item['kind'],item['side']) for item in ob['events']]
    candidates.extend((source,sr['signal']) for source in sr.get('entry_sources',[]) if sr.get('signal') and source.startswith('sr:') and source.endswith('_major'))
    candidates.extend((source,native['signal']) for source in native['entry_sources'])
    sides={side for _,side in candidates}
    side=next(iter(sides)) if len(sides)==1 and not ob['conflict'] and not sr.get('conflict') and not native['conflict'] else None
    return dict(signal=side,entry_sources=sorted({s for s,d in candidates if d==side}) if side else [])


def observe(runner,symbol,cfg,rows,now,interval):
    duration=interval_seconds(interval);current=int(now)//duration*duration;latest=current-duration
    if not rows or rows[-1]['time']!=current:raise ValueError('Fresh post-close '+interval+'m snapshot required')
    times=[row['time'] for row in rows]
    if times!=sorted(set(times)):raise ValueError('Closed source candles must be ordered and unique')
    for row in rows:
        validate(row)
        if row['time']%duration:raise ValueError('Closed source candle timestamp is off interval')
    history=runner.history(symbol,interval,rows,defer_prune=True)
    closed=[row for row in history if row['time']<=latest]
    if len(closed)<2 or closed[-1]['time']!=latest:raise ValueError('Latest and previous CLOSED '+interval+'m candles required')
    identity=strategy_fingerprint(cfg)+':closed-source:'+interval
    with connect(runner.history_path) as db:
        saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
    old=json.loads(saved[0]) if saved else {}
    known={row['time']:row for row in closed}
    valid=bool(old.get('source')==runner.source_fingerprint and old.get('ob_state') and
        all(known.get(row['time'],row)==row for row in old.get('recent',[])))
    cycle=runner.confirmation_symbol_cycles.get(symbol,0) if interval=='1' else runner.scan_cycle
    continuous=valid and old.get('session')==runner.observation_session and old.get('cycle') in (cycle-1,cycle) and 0<now-old.get('observed_at',0)<=300
    previous_bar=old.get('last_time')
    if not valid or previous_bar!=latest:
        source=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe=interval,major=True,minor=False,sma_enabled=False,sma_7_50_filter_enabled=False))
        sr=IndicatorState(source,old.get('sr_state') if valid else None)
        ob=OrderBlockState(old.get('ob_state') if valid else None,interval=interval,formation_filter=False)
        previous=old.get('last_close') if valid else None
        report=old.get('report') or dict(signal=None,entry_sources=[])
        if valid and closed[0]['time']>ob.s['last_time']+duration:raise ValueError('Closed source checkpoint gap; backfill required')
        for row in closed:
            if ob.s['last_time'] is not None and row['time']<=ob.s['last_time']:continue
            block=select(ob.step(row),cfg.order_block,formation_filter=False);levels=sr.step(row)
            if not block['ready']:block={**block,'events':[],'conflict':False}
            report=combine_closed(block,levels,row,previous,source)
            previous=row['close']
        old.update(ob_state=ob.checkpoint(),sr_state=sr.checkpoint(),last_close=previous,report=report,recent=closed[-60:])
    emitted=old['report'] if continuous and previous_bar==latest-duration else dict(signal=None,entry_sources=[])
    old.update(last_time=latest,observed_at=now,cycle=cycle,session=runner.observation_session,source=runner.source_fingerprint)
    with connect(runner.history_path) as db:
        db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(old,allow_nan=False)))
        # Both sources have advanced; retain a backfill bridge until this point.
        db.execute('DELETE FROM candles WHERE symbol=? AND interval=? AND time<?',(symbol,interval,current-duration*5000))
    return dict(time=now,mark=closed[-1]['close'],closed_bar=latest,closed_at=current,**emitted,
        source_errors={},volume_checks={},volume_delta_status='CLOSED '+interval+'m Order Block OR Major S/R breakout + bounce/rejection; no entry filters')


def arm(gate,events,now,enabled,owner):
    messages=[];latest=int(now)//1800*1800-1800
    for event in events:
        if event['id'] in gate['seen_primary']:continue
        gate['seen_primary']=(gate['seen_primary']+[event['id']])[-128:]
        bar=event.get('closed_bar')
        if (not enabled or bar!=latest or bar in gate['consumed_windows'] or not 0<=now-event['time']<=30
                or (gate.get('cooldown_until') is not None and bar+1800<=gate['cooldown_until'])):continue
        if gate['phase']=='cooldown':gate['losses']=0
        gate.update(phase='armed',armed_at=now,arm_id=gate['arm_id']+1,sources=event['sources'],primary_timeframe='30',
            directions=([dict(side='Long' if owner['side']=='Buy' else 'Short',link=owner['link'])] if owner else []),
            cooldown_until=None,permission_closed_bar=bar)
        gate['consumed_windows']=(gate['consumed_windows']+[bar])[-128:]
        messages.append('NEW CLOSED 30m permission; current position counts as direction 1' if owner else 'CLOSED 30m permission armed; waiting for first NEW CLOSED 1m direction')
    return messages


def entry_fresh(stamp,context,now):
    ended=(context or {}).get('signal_closed_at')
    return ended==int(stamp)//60*60 and 0<=now-stamp<=30
