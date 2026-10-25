"""Per-coin higher-timeframe arming; no exchange operations."""
STRATEGY='sr_rsi_volume_gate'


def new_gate():
    return dict(phase='waiting',direction=None,reference_direction=None,armed_at=None,primary_timeframe=None,
        sources=[],losses=0,cooldown_until=None,arm_id=0,seen_primary=[])


def apply(gate,primary,volume,now,*,enabled,position_open):
    events=[]
    if gate['phase']=='cooldown' and now>=gate['cooldown_until']:
        gate['phase']='wait_fresh'
        events.append('cooldown finished; waiting for a fresh 4h/Daily S/R or RSI signal')
    fresh=[]
    for event in sorted(primary,key=lambda item:(item['time'],item['timeframe'])):
        if event['id'] in gate['seen_primary']:continue
        gate['seen_primary']=(gate['seen_primary']+[event['id']])[-128:]
        if event['signal'] not in ('Long','Short'):continue
        if not enabled:events.append('higher-timeframe signal ignored while entries are paused or blocked');continue
        if gate['phase']=='armed':
            events.append('higher-timeframe signal ignored; Volume Delta gate remains armed and loss count unchanged');continue
        if gate['phase']=='cooldown':continue
        if gate.get('cooldown_until') is not None and event.get('window_start',0)<gate['cooldown_until']:
            events.append('higher-timeframe signal skipped: observation window began before cooldown ended');continue
        fresh.append(event)
    if fresh:
        earliest=min(event['time'] for event in fresh)
        candidates=[event for event in fresh if event['time']==earliest]
        event=candidates[0]
        was_locked=gate['phase']=='wait_fresh'
        frames=sorted({item['timeframe'] for item in candidates})
        sources=sorted({source for item in candidates for source in item['sources']})
        directions={item['signal'] for item in candidates}
        gate.update(phase='armed',direction='Any',reference_direction=event['signal'] if len(directions)==1 else 'Both',armed_at=now,
            primary_timeframe='+'.join(frames),sources=sources,arm_id=gate['arm_id']+1)
        if was_locked:gate['losses']=0
        events.append(('unlocked and ' if was_locked else '')+'armed by '+gate['primary_timeframe']+' S/R or RSI; waiting for a NEW bullish OR bearish 1m Volume Delta signal')
    confirmation=None
    if (enabled or position_open) and gate['phase']=='armed' and volume.get('signal') in ('Long','Short') and volume['time']>gate['armed_at']:
        confirmation=dict(signal=volume['signal'],entry_sources=gate['sources']+volume.get('entry_sources',[]),
            entry_context=dict(arm_id=gate['arm_id'],primary_timeframe=gate['primary_timeframe'],armed_at=gate['armed_at'],confirmation_timeframe='1'))
    return confirmation,events


def result(gate,pnl,now,loss_limit,cooldown_minutes):
    if pnl>0:gate['losses']=0
    elif pnl<0:gate['losses']+=1
    if gate['losses']>=loss_limit:
        gate.update(phase='cooldown',direction=None,armed_at=None,sources=[],primary_timeframe=None,
            cooldown_until=now+cooldown_minutes*60)
        return True
    return False
