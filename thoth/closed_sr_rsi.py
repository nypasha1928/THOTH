"""Closed 30m Major S/R OR fixed six-crossing RSI sources; no exchange writes."""
import json
from .database import connect
from .indicator_stream import IndicatorState,validate
from .rsi import RSIState
from .sr_bounce import evaluate as bounce
from .timeframes import interval_seconds
from .verification import strategy_fingerprint

STRATEGY='sr_rsi_30m_delayed'


def combine_closed(rsi,sr,row,previous_close,cfg):
    native=bounce(row,previous_close,sr.get('levels',{}),cfg)
    candidates=[('rsi:'+event['kind'],event['side']) for event in rsi['events']]
    candidates.extend((source,sr['signal']) for source in sr.get('entry_sources',[]) if sr.get('signal') and source.startswith('sr:') and source.endswith('_major'))
    candidates.extend((source,native['signal']) for source in native['entry_sources'])
    sides={side for _,side in candidates}
    conflict=len(sides)>1 or rsi.get('conflict') or sr.get('conflict') or native.get('conflict')
    side=next(iter(sides)) if len(sides)==1 and not conflict else None
    return dict(signal=side,entry_sources=sorted({s for s,d in candidates if d==side}) if side else [],
        signal_diagnostic=dict(entry_bar=row['time'],candle=dict(row),rsi=rsi.get('rsi'),
            thresholds=rsi.get('thresholds',{}),previous_rsi=rsi.get('previous_rsi'),previous_thresholds=rsi.get('previous_thresholds',{}),rsi_events=[e['kind'] for e in rsi['events']],
            sr_levels=sorted({source.split(':',1)[1] for source in sr.get('entry_sources',[])+native['entry_sources']}),sr_level_values=sr.get('levels',{}),sr_sources=sr.get('entry_sources',[]),bounce_sources=native['entry_sources'],
            conflict=bool(conflict),reason='opposing source signals' if conflict else 'closed 30m source',signal=side))


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
    valid=bool(old.get('source')==runner.source_fingerprint and old.get('rsi_state') and
        all(known.get(row['time'],row)==row for row in old.get('recent',[])))
    cycle=runner.confirmation_symbol_cycles.get(symbol,0) if interval=='1' else runner.scan_cycle
    continuous=valid and old.get('session')==runner.observation_session and old.get('cycle') in (cycle-1,cycle) and 0<now-old.get('observed_at',0)<=300
    previous_bar=old.get('last_time')
    if not valid or previous_bar!=latest:
        source=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe=interval,major=True,minor=False,sma_enabled=False,sma_7_50_filter_enabled=False))
        sr=IndicatorState(source,old.get('sr_state') if valid else None)
        rsi=RSIState(cfg.rsi,old.get('rsi_state') if valid else None)
        previous=old.get('last_close') if valid else None
        report=old.get('report') or dict(signal=None,entry_sources=[])
        if valid and closed[0]['time']>rsi.s['last_time']+duration:raise ValueError('Closed source checkpoint gap; backfill required')
        for row in closed:
            if rsi.s['last_time'] is not None and row['time']<=rsi.s['last_time']:continue
            if rsi.s['last_time'] is not None and row['time']!=rsi.s['last_time']+duration:raise ValueError('Closed RSI source gap; backfill required')
            previous_rsi=rsi.s['rsi'];previous_thresholds=dict(rsi.s['thresholds'])
            result={**rsi.step(row,duration),'previous_rsi':previous_rsi,'previous_thresholds':previous_thresholds};levels=sr.step(row)
            report=combine_closed(result,levels,row,previous,source)
            previous=row['close']
        old.update(rsi_state=rsi.checkpoint(),sr_state=sr.checkpoint(),last_close=previous,report=report,recent=closed[-60:])
    emitted=old['report'] if continuous and previous_bar==latest-duration else dict(signal=None,entry_sources=[])
    old.update(last_time=latest,observed_at=now,cycle=cycle,session=runner.observation_session,source=runner.source_fingerprint)
    with connect(runner.history_path) as db:
        db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(old,allow_nan=False)))
        # Both sources have advanced; retain a backfill bridge until this point.
        db.execute('DELETE FROM candles WHERE symbol=? AND interval=? AND time<?',(symbol,interval,current-duration*5000))
    return dict(time=now,mark=closed[-1]['close'],closed_bar=latest,closed_at=current,**emitted,
        source_errors={},volume_checks={},volume_delta_status='CLOSED '+interval+'m Major S/R breakout + bounce/rejection OR six fixed RSI signals; no entry filters')
