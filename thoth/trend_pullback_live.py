"""Closed 1h trend and closed 30m Major pullback entries; account-sized orders."""
import json,time
from math import isfinite
from .database import connect
from .indicator_stream import validate
from .trend_pullback import qualify
from .verification import strategy_fingerprint

STRATEGY='trend_major_pullback_30m_close'
MAX_DISTANCE_ATR=.25
ENTRY_BUFFER_BPS=2
POST_CLOSE_SECONDS=120


def fresh(evidence,now):
    ended=evidence.get('closed_at')
    return ended==int(now)//1800*1800 and 0<=now-ended<=POST_CLOSE_SECONDS


def recheck(side,context,now,price):
    """Use the actual submission quote against the frozen confirmed setup."""
    evidence=(context or {}).get('trend_pullback') or {}
    if not fresh(evidence,now) or evidence.get('signal')!=side:
        raise ValueError('Trend + Major Pullback confirmed setup expired or unavailable')
    price=float(price);atr=float(evidence.get('atr14',0));boundary=float(evidence.get('boundary',0))
    average=float(evidence.get('sma200',0));close=float(evidence.get('hour_close',0))
    if not all(isfinite(v) and v>0 for v in (price,atr,boundary,average,close)):
        raise ValueError('Trend + Major Pullback price/reference unavailable')
    if not (close>average if side=='Long' else close<average):
        raise ValueError('Trend + Major Pullback hourly direction unavailable')
    sign=1 if side=='Long' else -1;buffered=price*(1+sign*ENTRY_BUFFER_BPS/10000)
    distance=sign*(buffered-boundary)/atr
    if not 0<=distance<=MAX_DISTANCE_ATR:
        raise ValueError('Trend + Major Pullback submission outside 0.25 ATR distance')
    return dict(checked_at=now,price=price,buffered_price=buffered,distance_atr=distance)


def observe(runner,symbol,cfg,rows,now,received_at):
    current=int(now)//1800*1800;latest=current-1800
    if not rows or rows[-1]['time']!=current:raise ValueError('Fresh forming 30m snapshot required')
    stamps=[r['time'] for r in rows]
    if stamps!=sorted(set(stamps)) or any(t%1800 for t in stamps):raise ValueError('30m source timestamps must be ordered and aligned')
    for row in rows:validate(row)
    history=runner.history(symbol,'30',rows)
    closed=[r for r in history if r['time']<=latest][-1200:]
    if not closed or closed[-1]['time']!=latest:raise ValueError('Latest CLOSED 30m candle unavailable')
    identity=strategy_fingerprint(cfg)+':trend-major-pullback-live'
    with connect(runner.history_path) as db:
        saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
    old=json.loads(saved[0]) if saved else {}
    continuous=(old.get('session')==runner.observation_session and old.get('source')==runner.source_fingerprint
        and old.get('cycle') in (runner.scan_cycle-1,runner.scan_cycle) and 0<now-old.get('observed_at',0)<=POST_CLOSE_SECONDS)
    # Persist consumption before dispatch; restart, recovery and late checks do not replay.
    tracker=dict(last_bar=latest,session=runner.observation_session,source=runner.source_fingerprint,cycle=runner.scan_cycle,observed_at=now)
    with connect(runner.history_path) as db:
        db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker)))
    runner.observe_trend_pullback(symbol,cfg,rows,received_at)
    reason='Waiting for next CLOSED 30m Major pullback; 1h SMA200 direction';decision=None
    if not continuous:reason='Baseline/recovery: old setup not replayed'
    elif old.get('last_bar')==latest:pass
    elif old.get('last_bar')!=latest-1800:reason='Baseline/recovery: missed closed candle'
    elif not 0<=now-received_at<=30:reason='Stale 30m price snapshot'
    elif now-current>POST_CLOSE_SECONDS:reason='Post-close entry window expired'
    else:
        decision=qualify(closed,rows[-1]['close'],cfg,MAX_DISTANCE_ATR,ENTRY_BUFFER_BPS)
        decision={**decision,'signal_bar':latest,'closed_at':current,'timeframe':'30','entry_risk_mode':'saved_account_settings'}
        reason=decision['reason']
        if not fresh(decision,time.time()) or not 0<=time.time()-received_at<=30:
            decision={**decision,'signal':None,'reason':'Post-close window or price snapshot expired during calculation'};reason=decision['reason']
    side=decision.get('signal') if decision else None
    sources=['htf:30:'+decision['source']] if side else ['htf:30:'+s for s in (decision or {}).get('sources',[])]
    diagnostic={**decision,'raw_sources':decision.get('sources',[])} if decision else None
    context=dict(primary_timeframe='30',trend_pullback=decision,entry_diagnostics=dict(qualified_at=now,qualified_price=rows[-1]['close'],signal_bar_closed_at=current,previous_observed_at=old.get('observed_at'))) if side else None
    if side:
        from .signal_stretch import snapshot
        from .signal_sources import source_label
        metrics=snapshot(closed,side,[],qualified_at=now,atr14_override=decision['atr14'])
        if metrics:
            boundary=decision['boundary'];sign=1 if side=='Long' else -1
            metrics.update(timeframe='30',closed_at=current,qualification_after_close_seconds=now-current,
                references=[dict(source=sources[0],label=source_label(sources),kind='bounced_support' if side=='Long' else 'rejected_resistance',
                    price=boundary,close_distance_pct=sign*(closed[-1]['close']-boundary)/boundary*100)])
            context['signal_stretch']=metrics
    return dict(time=now,mark=rows[-1]['close'],signal=side,entry_sources=sources,signal_diagnostic=diagnostic,
        entry_context=context,source_errors={},volume_checks={},
        volume_delta_status=reason,source_status='no_qualifying_signal' if decision and not decision.get('sources') else None)
