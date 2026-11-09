"""30m Major pullback setup + fresh CLOSED 15m native confirmation; no SMA."""
import json,time
from math import isfinite
from .database import connect
from .indicator_stream import validate
from .sr_bounce import evaluate as native
from .trend_pullback import atr14
from .verification import strategy_fingerprint

STRATEGY='major_pullback_30m_intrabar'
LABEL='Major Pullback · 30m Intrabar + 15m Close · No SMA'
MAX_DISTANCE_ATR=.25
ENTRY_BUFFER_BPS=2


def confirmation(rows,now):
    current=int(now)//900*900
    if len(rows)<3 or rows[-1]['time']!=current or rows[-2]['time']!=current-900 or rows[-3]['time']!=current-1800:
        raise ValueError('Fresh forming and two previous 15m candles required')
    stamps=[r['time'] for r in rows]
    if stamps!=sorted(set(stamps)) or any(t%900 for t in stamps):raise ValueError('15m confirmation timestamps must be ordered and aligned')
    for row in rows:validate(row)
    last,previous=rows[-2],rows[-3];source=int(last['time'])//1800*1800
    setup=dict(last,time=source)
    if last['time']!=source:
        if previous['time']!=source:raise ValueError('Actual 15m pair required for 30m setup')
        setup.update(open=previous['open'],high=max(previous['high'],last['high']),low=min(previous['low'],last['low']),volume=previous['volume']+last['volume'])
    return last,previous,setup


def qualify(closed15,previous15,setup30,previous30,levels,current_levels,cfg,price,atr,observed_at,fired):
    raw30=native(setup30,previous30['close'],levels,cfg)
    raw15=native(closed15,previous15['close'],levels,cfg)
    common=sorted(set(raw30['entry_sources']) & set(raw15['entry_sources']))
    side=raw30['signal'] if raw30['signal']==raw15['signal'] and not raw30['conflict'] and not raw15['conflict'] else None
    sources=[]
    for source in common if side else []:
        key=source.split(':',1)[1];level=levels[key]
        token=json.dumps([source,level['value'],level.get('pivot_time')],separators=(',',':'))
        if token in fired:continue
        # Consume an event even if its ATR distance fails; do not chase it later.
        fired.append(token)
        if current_levels.get(key)==level:sources.append(source)
    result=dict(signal=None,reason='No matching 30m Major pullback and CLOSED 15m bounce/rejection',sources=sources,
        source_bar=setup30['time'],confirmation_bar=closed15['time'],confirmation_closed_at=closed15['time']+900,
        observed_at=observed_at,price=float(price),signal_candle=setup30,confirmation_candle=closed15,
        previous_30m_close=previous30['close'],previous_15m_close=previous15['close'],atr14=atr,
        reference_timeframe='30',confirmation_timeframe='15',sma_filter=False,
        max_distance_atr=MAX_DISTANCE_ATR,entry_buffer_bps=ENTRY_BUFFER_BPS,source_distances=[])
    if not side or not sources:return result
    if atr is None or not isfinite(atr) or atr<=0:return {**result,'reason':'ATR14 unavailable'}
    sign=1 if side=='Long' else -1;buffered=float(price)*(1+sign*ENTRY_BUFFER_BPS/10000);refs=[]
    for src in sources:
        level=levels[src.split(':',1)[1]];boundary=float(level['value']);distance=sign*(buffered-boundary)/atr
        live_valid=float(price)>boundary and float(price)>previous30['close'] if side=='Long' else float(price)<boundary and float(price)<previous30['close']
        result['source_distances'].append(dict(source=src,boundary=boundary,distance_atr=distance,setup_still_valid=live_valid))
        if live_valid and 0<=distance<=MAX_DISTANCE_ATR:refs.append((distance,src,boundary))
    if not refs:return {**result,'reason':'Confirmed pullback no longer valid or entry outside 0.25 ATR distance'}
    distance,source,boundary=min(refs)
    return {**result,'signal':side,'reason':'Would enter: 30m Major pullback + CLOSED 15m bounce/rejection',
        'source':source,'boundary':boundary,'distance_atr':distance,'buffered_price':buffered,'entry_risk_mode':'saved_account_settings'}


def recheck(side,context,now,price):
    evidence=(context or {}).get('major_pullback_intrabar') or {};source=evidence.get('source','')
    ended=evidence.get('confirmation_closed_at');observed=evidence.get('observed_at')
    if (evidence.get('signal')!=side or ended!=int(now)//900*900 or observed is None or not 0<=now-observed<=30):
        raise ValueError('Major Pullback 15m confirmation expired or unavailable')
    expected='sr_bounce:' if side=='Long' else 'sr_rejection:'
    if not source.startswith(expected) or not source.endswith('_major'):raise ValueError('Major pullback source unavailable')
    price=float(price);atr=float(evidence.get('atr14',0));boundary=float(evidence.get('boundary',0));previous=float(evidence.get('previous_30m_close',0))
    if not all(isfinite(v) and v>0 for v in (price,atr,boundary,previous)):raise ValueError('Major pullback price/reference unavailable')
    valid=price>boundary and price>previous if side=='Long' else price<boundary and price<previous
    sign=1 if side=='Long' else -1;buffered=price*(1+sign*ENTRY_BUFFER_BPS/10000);distance=sign*(buffered-boundary)/atr
    if not valid or not 0<=distance<=MAX_DISTANCE_ATR:
        raise ValueError('Major pullback submission no longer valid or outside 0.25 ATR distance')
    return dict(checked_at=now,price=price,buffered_price=buffered,distance_atr=distance,sma_filter=False)


def observe(runner,symbol,cfg,packet,now):
    rows=packet['primary']['30'];lower=packet['confirmation_15']
    if not rows or rows[-1]['time']!=int(now)//1800*1800:raise ValueError('Fresh forming 30m source required')
    stamps=[r['time'] for r in rows]
    if stamps!=sorted(set(stamps)) or any(t%1800 for t in stamps):raise ValueError('30m setup timestamps must be ordered and aligned')
    for row in rows:validate(row)
    last,previous,confirmed_setup=confirmation(lower,now)
    received=packet.get('sample_received_at',now)
    history=runner.history(symbol,'30',rows)
    results,count=runner.analysis(symbol,cfg,history,now)
    if count<max(14,2*cfg.pivot_long+1,2*cfg.pivot_short+1):raise ValueError('Major S/R and ATR history warming up')
    current=int(now)//1800*1800
    if not results or results[-1]['time']!=current-1800:raise ValueError('Latest confirmed 30m Major levels unavailable')
    identity=strategy_fingerprint(cfg)+':closed-15m-confirmation'
    with connect(runner.history_path) as db:
        saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
    old=json.loads(saved[0]) if saved else {}
    budget=min(300.,max(30.,len(cfg.symbols)/8+20,runner.previous_scan_seconds*1.5+runner.interval+10))
    continuous=(old.get('session')==runner.observation_session and old.get('source')==runner.source_fingerprint and
        old.get('cycle') in (runner.scan_cycle-1,runner.scan_cycle) and 0<now-old.get('observed_at',0)<=budget)
    fresh_confirmation=old.get('confirmation_bar')!=last['time']
    baseline=not continuous or (fresh_confirmation and old.get('confirmation_bar')!=last['time']-900)
    blocked=last['time'] if baseline else old.get('blocked_confirmation_bar')
    if not 0<=now-received<=30:blocked=last['time']
    fired={key:value for key,value in old.get('fired',{}).items() if int(key)>=current-1800}
    decision=None;reason='Waiting for a fresh CLOSED 15m confirmation; NO SMA or fixed entry windows'
    if blocked==last['time']:reason='Baseline/recovery: old or stale 15m confirmation not replayed'
    else:
        prior=next((b for b in reversed(results) if b['time']==confirmed_setup['time']-1800),None)
        before=next((r for r in reversed(history) if r['time']==current-1800),None)
        if not prior or not before:raise ValueError('30m levels before 15m confirmation or previous 30m candle unavailable')
        # Both responses contain actual data in the current 30m candle. Refresh
        # its last price/extremes with the newer forming 15m snapshot.
        setup={**rows[-1],'close':lower[-1]['close'],'high':max(rows[-1]['high'],lower[-1]['high']),'low':min(rows[-1]['low'],lower[-1]['low'])}
        closed=[r for r in history if r['time']<current][-1200:]
        measured_atr=atr14(closed+[setup])
        decision=qualify(last,previous,setup,before,prior['levels'],results[-1]['levels'],cfg,lower[-1]['close'],measured_atr,now,fired.setdefault(str(current),[]))
        reason=decision['reason']
        if not 0<=time.time()-received<=30 or int(time.time())//900*900!=last['time']+900:
            decision={**decision,'signal':None,'reason':'15m confirmation snapshot expired during calculation'};reason=decision['reason'];blocked=last['time']
    tracker=dict(session=runner.observation_session,source=runner.source_fingerprint,cycle=runner.scan_cycle,observed_at=now,confirmation_bar=last['time'],blocked_confirmation_bar=blocked,fired=fired)
    with connect(runner.history_path) as db:
        db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
    side=(decision or {}).get('signal');sources=['htf:30:'+decision['source']] if side else ['htf:30:'+s for s in (decision or {}).get('sources',[])]
    context=dict(primary_timeframe='30',major_pullback_intrabar=decision,
        entry_diagnostics=dict(qualified_at=now,qualified_price=lower[-1]['close'],signal_bar_closed_at=last['time']+900,previous_observed_at=old.get('observed_at'))) if side else None
    if side:
        from .signal_stretch import snapshot
        from .signal_sources import source_label
        metrics=snapshot([setup],side,[],qualified_at=now,atr14_override=decision['atr14'])
        if metrics:
            sign=1 if side=='Long' else -1;boundary=decision['boundary'];closed_at=setup['time']+1800
            metrics.update(timeframe='30',closed_at=closed_at,provisional=last['time']+900<closed_at,observed_at=now,
                qualification_after_close_seconds=now-closed_at if now>=closed_at else None,
                references=[dict(source=sources[0],label=source_label(sources),kind='bounced_support' if side=='Long' else 'rejected_resistance',price=boundary,close_distance_pct=sign*(setup['close']-boundary)/boundary*100)])
            context['signal_stretch']=metrics
    return dict(time=now,mark=lower[-1]['close'],signal=side,entry_sources=sources,entry_context=context,
        signal_diagnostic={**decision,'raw_sources':decision.get('sources',[])} if decision and (fresh_confirmation or decision.get('sources')) else None,
        source_status='no_qualifying_signal' if decision and not decision.get('sources') else None,
        source_errors={},volume_checks={},volume_delta_status=reason)


def chart_references(rows15,levels30,cfg,now):
    """15m candle references against the strategy's prior-known 30m Major levels."""
    by_time={row['time']:row for row in rows15};states={row['time']:row for row in levels30};out=[]
    for row in rows15:
        source=int(row['time'])//1800*1800
        levels={key:value for key,value in states.get(source-1800,{}).get('levels',{}).items() if key.endswith('_major')}
        raw=native(row,by_time.get(row['time']-900,{}).get('close'),levels,cfg)
        out.append(dict(time=row['time'],close=row['close'],levels=levels,sma=None,**raw,provisional=row['time']+900>now))
    return out
