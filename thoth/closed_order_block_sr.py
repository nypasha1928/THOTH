"""Closed 1h OB OR S/R with normal or inverted entries and no volume filter."""
STRATEGY='order_block_sr_1h_inverted_close'
NORMAL_STRATEGY='order_block_sr_1h_close'


def evaluate(closed,cfg,now,sr_result=None,sr_error=None):
    from .order_block import reference
    expected=int(now)//3600*3600-3600
    if not closed or closed[-1]['time']!=expected:raise ValueError('Latest closed 1h candle unavailable')
    errors={};reports=[]
    try:
        ob=reference(closed,cfg.order_block,now,interval='60',formation_filter=False)[-1]
        if not ob['ready']:raise ValueError('Closed Order Block history warming up')
        reports.append(ob)
    except ValueError as exc:errors['order_block']=str(exc)
    if sr_error:errors['sr']=sr_error
    elif sr_result:reports.append(sr_result)
    else:errors['sr']='Confirmed closed S/R history unavailable'
    if not reports:raise ValueError('Closed Order Block and S/R histories unavailable')
    sides={r['signal'] for r in reports if r.get('signal') in ('Long','Short')}
    conflict=len(sides)>1 or any(r.get('conflict') for r in reports)
    permission=cfg.strategy=='ob_sr_hour_gate'
    original=(sorted(sides)[0] if sides else None) if permission else next(iter(sides)) if len(sides)==1 and not conflict else None
    inverted=cfg.strategy==STRATEGY
    signal=(('Short' if original=='Long' else 'Long') if inverted else original) if original else None
    sources=list(dict.fromkeys(s for r in reports if original and (permission or r.get('signal')==original) for s in r.get('entry_sources',[])))
    result=dict(time=now,mark=closed[-1]['close'],signal=signal,source_signal=original,entry_sources=sources,source_errors=errors,
        volume_checks={},closed_signal_bar=expected,
        entry_context=dict(closed_signal_bar=expected,primary_timeframe='60',source_signal=original) if signal else None,
        volume_delta_status='1h CLOSED Order Block OR Major/Minor S/R; '+('INVERTED' if inverted else 'NORMAL')+' entries; NO volume filter; '+('conflicting signals skipped' if conflict else 'waiting for fresh closed candle'))
    if signal:result['volume_delta_status']+='; source '+original+' opens '+signal
    if conflict and not permission:result['signal_diagnostic']=dict(reason='conflicting closed Order Block / S/R signals',signal=None)
    return result
