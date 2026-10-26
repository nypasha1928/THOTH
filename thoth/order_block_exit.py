"""Unfiltered Order Block formation exits; breaks never close positions."""
from .order_block import OrderBlockState


def observe(tracker,rows,side,now,*,session,source,max_gap=30,opened_ms=None):
    duration=14400;start=int(now)//duration*duration
    if side not in ('Buy','Sell'):raise ValueError('Invalid Order Block exit side')
    if rows and rows[-1]['time']==start-duration and 0<=now-start<=10:
        tracker.setdefault('ready',False)
        tracker['waiting_for_candle']=start
        return None  # Bybit can briefly publish only the just-closed candle.
    if not rows or rows[-1]['time']!=start:raise ValueError('Current 4h Order Block exit candle unavailable')
    tracker.pop('waiting_for_candle',None)
    valid_source=tracker.get('source')==source
    checkpoint=tracker.get('closed_state') if valid_source else None
    known={row['time']:row for row in rows}
    revised=checkpoint and any(row['time'] in known and known[row['time']]!=row for row in tracker.get('recent',[]))
    if revised:checkpoint=None
    state=OrderBlockState(checkpoint,interval='240',formation_filter=False)
    for row in rows:
        if row['time']+duration>now:break
        if state.s['last_time'] is not None and row['time']<=state.s['last_time']:continue
        state.step(row)
    if state.s['last_time']!=start-duration:raise ValueError('Contiguous closed 4h Order Block exit history unavailable')
    preview=OrderBlockState(state.checkpoint(),interval='240',formation_filter=False).step(rows[-1])
    events=set(preview['events'])&{'bullish_formed','bearish_formed'}
    fresh=(valid_source and not revised and tracker.get('session')==session and tracker.get('ready') and preview['ready']
        and 0<now-tracker.get('observed_at',0)<=max_gap and tracker.get('bar') in (start,start-duration))
    fired=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
    new=events-fired;fired.update(events)
    target='bearish_formed' if side=='Buy' else 'bullish_formed'
    # A live formation whose candle began after entry is provably post-entry,
    # even on the first check after recovery. Never replay a pre-entry box.
    post_entry=opened_ms is not None and start*1000>opened_ms and preview['ready']
    reason=('Bearish Order Block formed (exit Long)' if side=='Buy' else 'Bullish Order Block formed (exit Short)') if (fresh or post_entry) and target in new else None
    tracker.update(source=source,session=session,bar=start,observed_at=now,ready=preview['ready'],fired=sorted(fired),
        closed_state=state.checkpoint(),recent=[row for row in rows if row['time']<start][-60:])
    return reason
