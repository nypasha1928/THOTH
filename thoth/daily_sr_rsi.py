"""Daily S/R OR fixed-RSI signals; one durable observation for both sources."""
from copy import deepcopy
from .intrabar_entry import observe_breakout,MAX_OBSERVATION_GAP
from .timeframes import interval_seconds

STRATEGY='sr_rsi_daily'


def combine(sr,rsi):
    sides={signal for signal in (sr.get('signal'),rsi.get('signal')) if signal}
    if sr.get('conflict') or rsi.get('conflict'):sides.update(('Long','Short'))
    return {**rsi,**sr,'signal':next(iter(sides)) if len(sides)==1 else None,
        'rsi_conflict':bool(rsi.get('conflict')),'conflict':len(sides)>1,'sr_signal':sr.get('signal'),'rsi_signal':rsi.get('signal')}


def observe(tracker,rows,levels,preview,cfg,now):
    duration=interval_seconds(cfg.timeframe);start=int(now)//duration*duration
    if preview.get('time')!=start:
        raise ValueError('Daily forming observation unavailable')
    # Runner paces starts at eight/sec; allow one full watchlist sweep plus
    # loop/processing margin. A longer outage still needs a fresh baseline.
    gap=max(MAX_OBSERVATION_GAP,len(cfg.symbols)/8+20)
    state=deepcopy(tracker)
    sr=observe_breakout(state.setdefault('sr',{}),rows,levels,cfg,now,max_gap=gap)
    r=state.setdefault('rsi',{})
    rsi_ready=preview.get('rsi') is not None
    fresh=rsi_ready and r.get('ready',True) and 0<now-r.get('observed_at',0)<=gap and r.get('bar') in (start,start-duration)
    fired=set(r.get('fired',[])) if r.get('bar')==start else set()
    events=[e for e in preview['events'] if e['kind'] not in fired] if rsi_ready else []
    fired.update(e['kind'] for e in preview['events'] if rsi_ready)
    sides={e['side'] for e in events}
    rsi_conflict=preview.get('rsi_conflict',preview['conflict'])
    signal=next(iter(sides)) if fresh and len(sides)==1 and not rsi_conflict else None
    previous_observed_at=r.get('observed_at')
    r.update(bar=start,observed_at=now,fired=sorted(fired),ready=rsi_ready,
        fresh=fresh,observation_gap=now-previous_observed_at if previous_observed_at is not None else None,gap_limit=gap)
    result=combine(sr,dict(signal=signal,conflict=fresh and (len(sides)>1 or rsi_conflict)))
    new_sr=set(state['sr'].get('fired',[]))-(set(tracker.get('sr',{}).get('fired',[])) if tracker.get('sr',{}).get('bar')==start else set())
    if events or new_sr:
        result['signal_diagnostic']={
            'entry_bar':start,'rsi_events':[e['kind'] for e in events],
            'sr_levels':sorted(new_sr),'sr_signal':sr.get('signal'),'rsi_signal':signal,
            'reason':('opposing source signals' if result['conflict'] else
                      'recovery baseline; existing RSI crossing not replayed' if events and not fresh and not new_sr else
                      'fresh crossing observed'),
            'signal':result['signal'],'previous_observed_at':previous_observed_at,
            'observation_gap':r['observation_gap'],'gap_limit':gap}
    if result.get('signal_diagnostic'):
        state['last_decision']={**result['signal_diagnostic'],'observed_at':now}
    tracker.clear();tracker.update(state)
    return result
