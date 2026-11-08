"""Native Major breakout plus bounce/rejection; no indicator entry filters."""
from .intrabar_entry import observe_breakout
from .sr_bounce import observe as observe_bounce
from .timeframes import interval_seconds

STRATEGY='sr_4h_daily_unfiltered'


def observe(tracker,rows,levels,cfg,now,max_gap):
    start=int(now)//interval_seconds(cfg.timeframe)*interval_seconds(cfg.timeframe)
    breakout=tracker.setdefault('breakout',{});native=tracker.setdefault('native',{})
    before=set(breakout.get('fired',[])) if breakout.get('bar')==start else set()
    previous_price=breakout.get('price');previous_observed_at=breakout.get('observed_at')
    crossed=observe_breakout(breakout,rows,levels,cfg,now,max_gap=max_gap)
    keys=set(breakout['fired'])-before
    candidates=[('sr:'+key,'Long' if 'resistance' in key else 'Short') for key in sorted(keys)]
    touched=observe_bounce(native,rows,levels,cfg,now,max_gap=max_gap)
    if touched['signal']:candidates.extend((source,touched['signal']) for source in touched['entry_sources'])
    sides={side for _,side in candidates}
    conflict=len(sides)>1 or touched['volume_conflict']
    side=next(iter(sides)) if len(sides)==1 and not conflict else None
    return dict(time=now,mark=crossed['mark'],signal=side,conflict=bool(conflict),
        entry_sources=sorted({src for src,direction in candidates if direction==side}) if side else [],
        raw_sources=[dict(source=src,side=direction) for src,direction in candidates],
        evidence=dict(timeframe=cfg.timeframe,observed_at=now,candle=dict(rows[-1]),previous_close=rows[-2]['close'],
            previous_observed_price=previous_price,previous_observed_at=previous_observed_at,levels=levels))
