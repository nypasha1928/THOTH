"""Independent 1h/4h OB or S/R entries; matching signals share one order."""
STRATEGY='order_block_sr_intrabar'


def combine(order_block,sr,now,mark,errors=None,timeframe='240'):
    from .timeframes import interval_seconds
    duration=interval_seconds(timeframe);label='1h' if timeframe=='60' else '4h'
    reports=[value for value in (order_block,sr) if value]
    sides={value['signal'] for value in reports if value.get('signal') in ('Long','Short')}
    conflict=(len(sides)>1 or bool(order_block.get('order_block_conflict')) or bool(sr.get('volume_conflict')))
    volume=order_block.get('volume_checks',{}).get(timeframe) or sr.get('volume_check') or {}
    signal=next(iter(sides)) if len(sides)==1 and not conflict and volume.get('passed') is True else None
    sources=list(dict.fromkeys(source for value in reports if signal and value.get('signal')==signal for source in value.get('entry_sources',[])))
    result=dict(time=now,mark=mark,signal=signal,entry_sources=sources,source_errors=errors or {},
        volume_checks={timeframe:volume},entry_context=dict(order_block_bar=int(now)//duration*duration) if signal else None,
        volume_delta_status=label+' Order Block OR Major/Minor S/R; mandatory Volume > MA 20; '+('conflicting signals skipped' if conflict else volume.get('reason','volume check unavailable')))
    if signal:
        from .entry_diagnostics import merged_qualification
        result['entry_diagnostics']=merged_qualification([value for value in reports if value.get('signal')==signal],now,mark)
    if conflict:result['signal_diagnostic']=dict(reason='conflicting volume-qualified Order Block / S/R signals',signal=None)
    return result
