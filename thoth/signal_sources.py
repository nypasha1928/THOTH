"""Entry provenance from recorded signal diagnostics, never chart inference."""
import json

RSI_LABELS={'static30_up':'RSI over 30','static30_down':'RSI below 30',
    'static70_up':'RSI over 70','static70_down':'RSI below 70',
    'ml_long_up':'RSI over Long','ml_long_down':'RSI below Long',
    'ml_short_up':'RSI over Short','ml_short_down':'RSI below Short'}


def sources_from_decision(item):
    if item.get('entry_sources'):return sorted(set(item['entry_sources']))
    d=item.get('diagnostic') or {};signal=item.get('signal')
    if signal not in ('Long','Short'):return []
    sources=[]
    if d.get('sr_signal')==signal:
        sources.extend('sr:'+key for key in d.get('sr_levels',[]))
    if d.get('rsi_signal')==signal:
        sources.extend('rsi:'+key for key in d.get('rsi_events',[]))
    return sorted(set(sources))


def source_label(sources):
    labels=[]
    for source in sorted(set(sources or [])):
        if source.startswith('htf:'):
            _,frame,nested=source.split(':',2)
            labels.append({'240':'4-hour','D':'Daily'}.get(frame,frame)+' '+source_label([nested]))
        elif source.startswith(('sr:','inverse_sr:')):
            parts=source.split(':',1)[1].split('_')
            if len(parts)==3:
                term,side,grade=parts
                labels.append(('Inverse S/R ' if source.startswith('inverse_sr:') else 'S/R ')+{'long':'long-term','short':'short-term'}.get(term,term)+' '+grade+' '+side)
            else:labels.append(source)
        elif source.startswith('ob:'):
            from .order_block import LABELS
            labels.append(LABELS.get(source[3:],source))
        elif source.startswith('volume_delta:'):
            labels.append({'bullish_strong':'Volume Delta Strong Bullish (Up)','bullish_weak':'Volume Delta Weak Bullish (Up-)','bullish_all':'Volume Delta All Bullish','bearish_strong':'Volume Delta Strong Bearish (Down)','bearish_weak':'Volume Delta Weak Bearish (Down+)','bearish_all':'Volume Delta All Bearish'}.get(source.split(':',1)[1],source))
        elif source.startswith('rsi:'):labels.append(RSI_LABELS.get(source[4:],source))
        elif source=='sma:7_50':labels.append('SMA 7/50 crossover')
        elif source=='sma:7_200':labels.append('SMA 7/200 crossover')
        else:labels.append(source)
    return ' + '.join(labels) if labels else 'Source not recorded'


def recorded_source_map(db,link=None):
    mapping={}
    query='SELECT payload FROM signal_decisions'+(" WHERE json_extract(payload,'$.alert_key')=?" if link else '')+' ORDER BY id'
    for payload, in db.execute(query,(link,) if link else ()):
        item=json.loads(payload);key=item.get('alert_key');sources=sources_from_decision(item)
        if key and sources:mapping.setdefault(key,sources)
    return mapping


def trade_records(db,rows):
    parsed=[(link,json.loads(payload)) for link,payload in rows]
    mapping=recorded_source_map(db) if any(not record.get('entry_sources') for _,record in parsed) else {}
    output=[]
    for link,record in parsed:
        sources=record.get('entry_sources') or mapping.get(link,[])
        output.append({**record,'entry_sources':sources,'entry_source_label':source_label(sources)})
    return output
