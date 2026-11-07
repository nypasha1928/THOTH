"""Read-only signal lifecycle projection, joined by exact execution identity."""

from .database import connect
import json
import hashlib
import sqlite3
from pathlib import Path

PHASES={
    'prepared':'Order prepared; submission pending',
    'submitted':'Order submitted; awaiting fill/protection',
    'partial':'Partially filled; protection/reconciliation pending',
    'protected':'Position filled; Bybit stop loss verified',
    'stop_error':'Protective stop failed; managed close/reconciliation required',
    'mismatch':'Execution mismatch; reconciliation required',
    'unknown':'Order outcome unknown; reconciliation required',
    'rejected':'Order rejected by Bybit',
    'cancelled':'Order cancelled',
    'closed':'Position closed; realized P&L reconciled',
}


def is_no_signal_check(item):
    """Only hide a check when the saved record establishes that no event fired."""
    d=item.get('diagnostic') or {}
    if item.get('signal') in ('Long','Short') or item.get('entry_sources'):return False
    if d.get('signal') in ('Long','Short') or d.get('conflict'):return False
    if any(d.get(k) for k in ('rsi_events','sr_sources','bounce_sources','sr_levels','source_checks','raw_sources','sr_signal','rsi_signal')):return False
    if item.get('source_status')=='no_qualifying_signal':return True
    if item.get('strategy')=='sr_rsi_30m_delayed' and d.get('candle'):return True
    return bool(item.get('strategy','').startswith('volume_delta') and 'no qualifying signal' in item.get('reason','').lower())


def decision_groups(records):
    groups={}
    for identity,payload in records:
        item=json.loads(payload);key=item.get('alert_key') or 'legacy-'+str(identity)
        if key not in groups:
            groups[key]={**item,'id':identity,'update_id':identity,'original_reason':item['reason']}
        else:
            previous=groups[key]
            duplicate_capture=(item.get('strategy')=='sr_rsi_30m_delayed' and item['reason']=='skipped: closed 30m source' and previous['reason']!=item['reason'])
            groups[key]={**previous,'reason':previous['reason'] if duplicate_capture else item['reason'],'time':item['time'],'update_id':identity,
                'diagnostic':previous.get('diagnostic') or item.get('diagnostic')}
        if not item.get('alert_key') and item['reason'].startswith('queued:'):
            groups[key]['reason']='Historical queued update; final outcome not linked'
    for item in groups.values():
        d=item.get('diagnostic') or {}
        if (item.get('strategy')=='sr_rsi_30m_delayed' and not item.get('entry_sources') and d.get('candle') and d.get('signal') is None
                and not d.get('conflict') and not any(d.get(k) for k in ('rsi_events','sr_sources','bounce_sources'))):
            item['source_status']='no_qualifying_signal'
            if item['reason']=='skipped: closed 30m source':item['reason']='skipped: no qualifying CLOSED 30m S/R or RSI signal'
    return groups


def read_alerts(path,symbol=None,limit=50,offset=0,watch_ids=()):
    with connect(f'file:{path}?mode=ro',uri=True) as db:
        saved=db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()
        state=json.loads(saved[0]) if saved else {}
        records=db.execute('SELECT id,payload FROM signal_decisions'+(' WHERE symbol=?' if symbol else '')+' ORDER BY id', (symbol,) if symbol else ()).fetchall()
    groups=decision_groups(records)
    items=sorted((item for item in groups.values() if not is_no_signal_check(item)),key=lambda item:item['update_id'],reverse=True)
    selected=items[offset:] if limit is None else items[offset:offset+limit]
    watched=[item for item in items if item['id'] in watch_ids]
    candidates={item['id']:item for item in selected+watched}
    execution=Path(path).with_suffix('.execution.sqlite')
    links={item['alert_key'] for item in candidates.values() if item.get('alert_key')}
    if execution.exists() and links:
        with connect(f'file:{execution}?mode=ro',uri=True) as db:
            by_link={item.get('alert_key'):item for item in candidates.values() if item.get('alert_key')}
            ordered=sorted(links)
            for index in range(0,len(ordered),200):
                chunk=ordered[index:index+200];placeholders=','.join('?' for _ in chunk)
                for link,phase,result,instrument in db.execute('SELECT link,phase,result,instrument FROM execution WHERE link IN ('+placeholders+')',chunk):
                    item=by_link[link]
                    item['execution_phase']=phase
                    item['reason']=PHASES.get(phase,'Execution state: '+phase)
                    execution_result=json.loads(result)
                    if execution_result.get('not_submitted'):item['reason']='Not submitted: '+execution_result.get('reason','entry eligibility changed')
                    from .signal_stretch import with_fill
                    metrics=json.loads(instrument).get('thoth_signal_stretch') or item.get('signal_stretch')
                    if metrics:item['signal_stretch']=with_fill(metrics,execution_result.get('entry_timing',{}))
                    code=execution_result.get('exchange_code')
                    if code is not None:item['reason']+=' (code '+str(code)+')'
    for item in candidates.values():
        key=item.get('alert_key');owner=state.get('owned',{}).get(item['symbol'],{})
        if key and owner.get('link')==key and owner.get('close_link'):
            item['reason']='Closing position; awaiting settlement'
        elif key and 'execution_phase' not in item and item['reason'].startswith('queued:'):
            intent=state.get('pending_signals',{}).get(item['symbol']) or state.get('reversals',{}).get(item['symbol'])
            intent_key=('thoth-'+hashlib.sha256((intent['identity']+':'+item['symbol']+':'+str(intent['time'])).encode()).hexdigest()[:28]) if intent else None
            if intent_key!=key:item['reason']='No longer queued; no linked order outcome recorded'
    return dict(items=selected,updates=watched,total=len(items),limit=limit,offset=offset)


def read_scan_checks(path,limit=50,offset=0):
    with connect(f'file:{path}?mode=ro',uri=True) as db:
        legacy=db.execute('SELECT id,payload FROM signal_decisions ORDER BY id').fetchall()
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_checks'").fetchone()
        current=db.execute('SELECT id,payload FROM scan_checks ORDER BY id').fetchall() if exists else []
    items=[item for rows in (legacy,current) for item in decision_groups(rows).values() if is_no_signal_check(item)]
    items.sort(key=lambda item:(item.get('observed_at') or item['time'],item['id']),reverse=True)
    return dict(items=items[offset:] if limit is None else items[offset:offset+limit],total=len(items),limit=limit,offset=offset,
        note='Routine checks only; not trade signals. Retained diagnostics, not a complete historical scan log.')
