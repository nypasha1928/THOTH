"""Two entry directions per hourly permission; exchange-independent state rules."""
from .volume_delta_windows import window_start

STRATEGY='ob_sr_windows_two_direction'


def new_gate():
    return dict(phase='waiting',armed_at=None,arm_id=0,sources=[],primary_timeframe=None,
        directions=[],seen_primary=[],consumed_windows=[],losses=0,cooldown_until=None)


def arm(gate,events,now,enabled,owner):
    messages=[]
    for event in events:
        identity=event['id']
        if identity in gate['seen_primary']:continue
        gate['seen_primary']=(gate['seen_primary']+[identity])[-128:]
        start=window_start(now)
        if (not enabled or start is None
                or event.get('window_start')!=start or not 0<=now-event['time']<=30
                or start in gate['consumed_windows'] or (gate.get('cooldown_until') is not None and start<gate['cooldown_until'])):continue
        if gate['phase']=='cooldown':gate['losses']=0
        gate.update(phase='armed',armed_at=now,arm_id=gate['arm_id']+1,sources=event['sources'],
            primary_timeframe='60',directions=([dict(side='Long' if owner['side']=='Buy' else 'Short',link=owner['link'])] if owner else []),cooldown_until=None)
        gate['consumed_windows']=(gate['consumed_windows']+[start])[-128:]
        messages.append('NEW 1h window permission; current position counts as direction 1' if owner else '1h window permission armed; waiting for first fresh 1m direction')
    return messages


def decision(gate,side,stamp,now):
    if gate['phase']!='armed' or side not in ('Long','Short') or not gate['armed_at']<stamp<=now or now-stamp>30:return None
    directions=gate['directions']
    if directions and side==directions[-1]['side']:return None
    if len(directions)>=2:return 'exit'
    return 'entry' if not directions else 'reverse'


def reserve(gate,side,link,arm_id):
    if gate['phase']!='armed' or gate['arm_id']!=arm_id or len(gate['directions'])>=2:
        raise ValueError('Two-direction permission expired or already consumed')
    if gate['directions'] and side==gate['directions'][-1]['side']:
        raise ValueError('Only the opposite second direction is permitted')
    gate['directions'].append(dict(side=side,link=link))


def finish(gate):
    gate.update(phase='waiting',armed_at=None,sources=[],primary_timeframe=None)


def hourly_signal(state,rows,cfg,now):
    """Unfiltered forming-hour OB plus Major S/R breakout and native bounce/rejection."""
    from .order_block import OrderBlockState,select
    from .sr_bounce import evaluate as bounce
    start=int(now)//3600*3600
    if len(rows)<2 or rows[-1]['time']!=start or rows[-2]['time']!=start-3600:
        raise ValueError('Current and previous hourly candles required')
    state.advance(rows,now)
    if state.ob.s['last_time']!=start-3600 or state.sr_report is None:
        raise ValueError('Hourly Order Block / S/R history unavailable')
    ob=select(OrderBlockState(state.ob.checkpoint(),interval='60',formation_filter=False).step(rows[-1]),cfg.order_block,formation_filter=False)
    if not ob['ready']:raise ValueError('Hourly Order Block history warming up')
    levels=state.sr_report['levels'];previous=rows[-2]['close'];price=rows[-1]['close']
    candidates=[('ob:'+event['kind'],event['side']) for event in ob['events']]
    for key,level in levels.items():
        if not key.endswith('_major'):continue
        value=level['value']
        if 'resistance' in key and previous<=value<price:candidates.append(('sr:'+key,'Long'))
        if 'support' in key and previous>=value>price:candidates.append(('sr:'+key,'Short'))
    native=bounce(rows[-1],previous,levels,cfg)
    candidates.extend((source,native['signal']) for source in native['entry_sources'])
    sides={side for _,side in candidates}
    signal=next(iter(sides)) if len(sides)==1 and not ob['conflict'] and not native['conflict'] else None
    return dict(signal=signal,entry_sources=sorted({source for source,side in candidates if side==signal}) if signal else [])


def entry_fresh(stamp,context,now):
    """A queued direction-2 reversal may cross a minute while close settles; TTL stays 30s."""
    queued_second=(context or {}).get('queued_reversal') is True and (context or {}).get('direction_index')==1
    return 0<=now-stamp<=30 and (int(stamp)//60==int(now)//60 or queued_second)
