"""Direct 4h/Daily S/R entries, gated by the forming candle's volume SMA."""
import math
STRATEGY='sr_volume_intrabar'


def volume_pass(rows,period):
    if len(rows)<period:return dict(passed=False,volume=None,average=None,reason='volume MA warmup unavailable')
    values=[float(row['volume']) for row in rows[-period:]]
    if any(not math.isfinite(value) or value<0 for value in values):
        raise ValueError('Invalid candle volume')
    average=sum(values)/period
    return dict(passed=values[-1]>average,volume=values[-1],average=average,
                reason='volume passed MA' if values[-1]>average else 'volume did not exceed MA')


def observe(tracker,rows,levels,cfg,now,max_gap):
    """Qualify the current breakout only once volume passes, never queue an order early."""
    from .intrabar_entry import observe_breakout
    from .timeframes import interval_seconds
    start=int(now)//interval_seconds(cfg.timeframe)*interval_seconds(cfg.timeframe)
    report=volume_pass(rows,cfg.sr_volume_period)
    same_bar=tracker.get('bar')==start
    fresh=0<now-tracker.get('observed_at',0)<=max_gap
    pending=dict(tracker.get('volume_setups',{})) if same_bar and fresh else {}
    emitted=set(tracker.get('volume_emitted',[])) if same_bar else set()
    previous_levels=tracker.get('levels',{})
    # Observe price continuity without consuming a crossing as an entry decision.
    tracker['fired']=[]
    observation=observe_breakout(tracker,rows,levels,cfg,now,max_gap=max_gap)
    for key in tracker['fired']:
        if key not in emitted:pending[key]=levels[key]
    price=observation['mark']
    pending={key:level for key,level in pending.items()
        if key not in emitted and levels.get(key)==level and previous_levels.get(key)==level
        and (cfg.major if key.endswith('_major') else cfg.minor)
        and (price>float(level['value']) if 'resistance' in key else price<float(level['value']))}
    eligible=sorted(pending) if report['passed'] else []
    sides={'Long' if 'resistance' in key else 'Short' for key in eligible}
    signal=next(iter(sides)) if len(sides)==1 else None
    # Consume qualified decisions before dispatch, including conflicting sources.
    emitted.update(eligible)
    for key in eligible:pending.pop(key,None)
    tracker.update(volume_setups=pending,volume_emitted=sorted(emitted))
    observation.update(signal=signal,entry_sources=['sr:'+key for key in eligible] if signal else [],
        volume_check=report,volume_conflict=len(sides)>1)
    return observation
