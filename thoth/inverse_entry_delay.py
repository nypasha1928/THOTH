"""Deliberate inverse-entry wait, followed by a fresh sampled breakout check."""
from copy import deepcopy


def apply_delay(tracker,observation,cfg,levels,now,*,continuous,entries_allowed,rows=None):
    delay=cfg.inverse_entry_delay_seconds
    if delay<=0:
        tracker.pop('delayed_entry',None)
        return observation
    raw=observation.get('source_signal');actual=observation.get('signal')
    sources=observation.get('entry_sources',[])
    pending=tracker.get('delayed_entry')
    observation={**observation,'signal':None}
    reason=None
    if pending:
        closes=[float(row['close']) for row in rows or []]
        filters_ok=True
        if cfg.sma_enabled:
            ready=len(closes)>=cfg.sma_length
            average=sum(closes[-cfg.sma_length:])/cfg.sma_length if ready else None
            filters_ok=ready and (observation['mark']>average if pending['raw']=='Long' else observation['mark']<average)
        if cfg.sma_7_50_filter_enabled:
            difference=sum(closes[-7:])/7-sum(closes[-50:])/50 if len(closes)>=50 else None
            filters_ok=filters_ok and difference is not None and (difference>0 if pending['raw']=='Long' else difference<0)
        if not continuous:reason='worker restarted or observation continuity lost'
        elif not entries_allowed:reason='entries paused or blocked'
        elif pending['bar']!=observation['entry_bar']:reason='entry candle changed'
        elif not filters_ok:reason='selected S/R entry filters no longer pass'
        elif actual and actual!=pending['side']:reason='opposite breakout observed'
        elif any(levels.get(key)!=level for key,level in pending['levels'].items()):reason='breakout level changed'
        elif any(not (observation['mark']>level['value'] if pending['raw']=='Long' else observation['mark']<level['value']) for level in pending['levels'].values()):reason='price returned through breakout level'
        if reason:
            tracker.pop('delayed_entry',None);pending=None
            observation['inverse_delay_event']='inverse entry wait cancelled: '+reason
        elif now>=pending['ready_at']:
            tracker.pop('delayed_entry',None)
            return {**observation,'signal':pending['side'],'source_signal':pending['raw'],
                'entry_sources':pending['sources'],'inverse_signal_detected_at':pending['started_at'],
                'inverse_delay_event':'inverse entry wait completed; breakout rechecked'}
    if actual and not pending:
        if not continuous or not entries_allowed:
            observation['inverse_delay_event']='inverse entry wait not started: entries paused, blocked or recovering'
            return observation
        captured={source.split(':',1)[1]:deepcopy(levels[source.split(':',1)[1]]) for source in sources if source.startswith('inverse_sr:') and source.split(':',1)[1] in levels}
        if not captured:return observation
        tracker['delayed_entry']=dict(side=actual,raw=raw,sources=sources,levels=captured,
            bar=observation['entry_bar'],started_at=now,ready_at=now+delay)
        observation['inverse_delay_event']='inverse entry wait started: '+actual+' after at least '+str(delay)+' seconds; breakout will be rechecked'
    return observation
