"""Read-only review of saved symbols; never changes trading eligibility."""

from .database import connect
import collections
import json
import sqlite3
import statistics
import time
from pathlib import Path


def read(path, scope='current'):
    path=Path(path)
    with connect(f'file:{path}?mode=ro',uri=True) as db:
        saved=db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()
        state=json.loads(saved[0]) if saved else {}
        cfg=state.get('config',{});strategy=cfg.get('strategy')
        trades=[json.loads(r[0]) for r in db.execute('SELECT payload FROM trade_history')]
        decisions=[json.loads(r[0]) for r in db.execute('SELECT payload FROM signal_decisions ORDER BY id')]
    symbols=list(dict.fromkeys(cfg.get('symbols',[])))
    groups={s:dict(symbol=s,trades=0,wins=0,losses=0,realized_pnl=0.0,first_opened_at=None,last_closed_at=None,
        strategy_results={},signal_events=0,skipped_events=0,skipped_reasons={},entry_check_failures=0,rejected_orders=0,rejection_events=0,rejection_event_reasons={},rejection_codes={},check_failure_reasons={},timing_entries=0,
        median_check_gap_seconds=None,median_qualified_to_submit_seconds=None) for s in symbols}
    eligible=lambda r:r.get('symbol') in groups and (scope=='all' or r.get('strategy')==strategy)
    for t in trades:
        if not eligible(t):continue
        g=groups[t['symbol']];p=float(t.get('pnl') or 0);g['trades']+=1;g['wins']+=p>0;g['losses']+=p<0;g['realized_pnl']+=p
        bucket=g['strategy_results'].setdefault(t.get('strategy') or 'unknown',dict(trades=0,realized_pnl=0.0))
        bucket['trades']+=1;bucket['realized_pnl']+=p
        if t.get('opened_at') is not None:g['first_opened_at']=min(g['first_opened_at'] or t['opened_at'],t['opened_at'])
        if t.get('closed_at') is not None:g['last_closed_at']=max(g['last_closed_at'] or t['closed_at'],t['closed_at'])
    # One lifecycle per exact key; legacy rows without identity remain separate observations.
    events={}
    for i,d in enumerate(decisions):
        if eligible(d):events[d.get('alert_key') or ('legacy',i)]=d
    for d in events.values():
        g=groups[d['symbol']];reason=d.get('reason','')
        g['signal_events']+=1
        if 'skipped' in reason or 'expired' in reason:
            g['skipped_events']+=1;g['skipped_reasons'][reason]=g['skipped_reasons'].get(reason,0)+1
        if 'rejected by Bybit' in reason:
            g['rejection_events']+=1;g['rejection_event_reasons'][reason]=g['rejection_event_reasons'].get(reason,0)+1
        if any(s in reason.lower() for s in ('leverage is unsupported','minimum order','minimum quantity','minimum notional')):
            g['entry_check_failures']+=1;g['check_failure_reasons'][reason]=g['check_failure_reasons'].get(reason,0)+1
    journal=path.with_suffix('.execution.sqlite')
    if journal.exists():
        with connect(f'file:{journal}?mode=ro',uri=True) as db:
            for payload,phase,instrument,result in db.execute('SELECT payload,phase,instrument,result FROM execution'):
                p=json.loads(payload);inst=json.loads(instrument);result=json.loads(result)
                r={'symbol':p.get('symbol'),'strategy':inst.get('thoth_strategy')}
                if p.get('reduceOnly') or not eligible(r) or phase!='rejected':continue
                g=groups[r['symbol']];g['rejected_orders']+=1
                code=str(result.get('exchange_code') or 'not recorded');g['rejection_codes'][code]=g['rejection_codes'].get(code,0)+1
    from .entry_diagnostics import read as timings,fast_movers
    measured=timings(path,limit=-1)['items'];buckets=collections.defaultdict(lambda:collections.defaultdict(list))
    for r in measured:
        if not eligible(r):continue
        groups[r['symbol']]['timing_entries']+=1
        for k in ('observation_gap_seconds','qualified_to_submit_seconds'):
            v=r['measurements'].get(k)
            if v is not None:buckets[r['symbol']][k].append(v)
    movers=fast_movers(path,time.time(),limit=300)
    speeds={r['symbol']:r for r in movers['items']}
    for s,g in groups.items():
        for src,dst in [('observation_gap_seconds','median_check_gap_seconds'),('qualified_to_submit_seconds','median_qualified_to_submit_seconds')]:
            vals=buckets[s][src]
            if vals:g[dst]=statistics.median(vals)
        g['latest_speed']=speeds.get(s)
        g['review_note']=('Review recorded rejection/check failure' if g['rejected_orders'] or g['rejection_events'] or g['entry_check_failures'] else 'No retained closed trades' if not g['trades'] else 'Limited sample: fewer than 3 trades' if g['trades']<3 else 'Compare results and timing')
    items=sorted(groups.values(),key=lambda g:(-g['trades'],g['symbol']))
    return dict(items=items,scope=scope,strategy=strategy,generated_at=time.time(),selected=len(items),
        traded=sum(g['trades']>0 for g in items),closed_trades=sum(g['trades'] for g in items),
        realized_pnl=sum(g['realized_pnl'] for g in items),
        limitations='Retained records only; no fixed two-week window. Current strategy scope excludes older or unknown strategy attribution. Signal lifecycles are retained observations, not every market crossing. Rejected journal orders, rejection signal events and check failures are separate counts and may overlap; do not add them together. Rejection codes are evidence, not proof of a permanent restriction. Median timings cover recorded submitted entries only, not every scan. Missing timing is unknown. No trades does not prove exchange rejection. Latest speed is a current-strategy snapshot, not historical volatility. No automatic exclusions or trading changes.')
