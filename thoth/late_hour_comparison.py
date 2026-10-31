"""Independent signal-only research. No exchange client, order methods or live writes."""
import json
import queue
import sqlite3
import threading
import time
from copy import deepcopy
from pathlib import Path
from pydantic import BaseModel,ConfigDict
from .models import Settings
from .verification import strategy_fingerprint
from .indicator_stream import IndicatorState,validate
from .order_block import OrderBlockState,select
from .signal_stretch import snapshot

STRATEGY='order_block_sr_1h_close'
RULES=dict(start_minute=55,max_opposing_wick_pct=30.,minimum_distance_atr=.05,maximum_distance_atr=.50,ob_thickness_filter=False)

class ComparisonSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool=True

class SourceState:
    """Private incremental replay; never touches the LIVE indicator checkpoints."""
    def __init__(self,cfg):
        self.cfg=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe='60',major=True,minor=False,sma_enabled=False,sma_7_50_filter_enabled=False))
        self.sr=IndicatorState(self.cfg);self.ob=OrderBlockState(interval='60',formation_filter=False)
        self.recent={};self.sr_report=None;self.ob_report=None
    def advance(self,rows,now):
        for row in rows:validate(row)
        if any(row['time'] in self.recent and self.recent[row['time']]!=row for row in rows if row['time']+3600<=now):
            self.__init__(self.cfg)
        for row in rows:
            if row['time']+3600>now:break
            last=self.ob.s['last_time']
            if last is not None and row['time']<=last:continue
            if last is not None and row['time']!=last+3600:raise ValueError('Comparison history gap')
            self.sr_report=self.sr.step(row);self.ob_report=select(self.ob.step(row),self.cfg.order_block,formation_filter=False)
            self.recent[row['time']]=dict(row)
        self.recent=dict(sorted(self.recent.items())[-60:])


def decision(rows,cfg,observed_at,processed_at,state=None):
    """Evaluate current provisional conditions, not a fabricated exact crossing time."""
    bar=int(observed_at)//3600*3600
    if not rows or rows[-1]['time']!=bar:raise ValueError('Current hourly candle unavailable')
    state=state or SourceState(cfg);state.advance(rows,observed_at)
    if state.ob.s['last_time']!=bar-3600 or state.sr.count<max(14,2*cfg.pivot_long+1):raise ValueError('Comparison history warming up')
    ob=select(OrderBlockState(state.ob.checkpoint(),interval='60',formation_filter=False).step(rows[-1]),cfg.order_block,formation_filter=False)
    levels=state.sr_report['levels'];previous=state.sr.buffer[-1]['close'];price=rows[-1]['close']
    sr_sources=[];sr_sides=set()
    for key,level in levels.items():
        if not key.endswith('_major'):continue
        value=level['value']
        if 'resistance' in key and previous<=value<price:sr_sources.append('sr:'+key);sr_sides.add('Long')
        if 'support' in key and previous>=value>price:sr_sources.append('sr:'+key);sr_sides.add('Short')
    candidates=[(source,event['side']) for event in ob['events'] for source in ['ob:'+event['kind']]]
    candidates.extend((s,'Long' if 'resistance' in s else 'Short') for s in sr_sources)
    sides={side for _,side in candidates}
    result=dict(bar=bar,observed_at=observed_at,processed_at=processed_at,price=price,signal=None,
        raw_sources=sorted({s for s,_ in candidates}),passed_sources=[],source_checks=[],provisional=True,rules=RULES)
    if observed_at<bar+3300 or observed_at>=bar+3600 or processed_at>=bar+3600 or processed_at<observed_at or processed_at-observed_at>30:
        result['reason']='Skip: outside :55–:00 window or observation older than 30 seconds';return result
    if len(sides)>1 or ob['conflict'] or len(sr_sides)>1:
        result['reason']='Skip: conflicting Long and Short signals';return result
    if not candidates:result['reason']='Skip: no provisional Order Block or Major S/R signal';return result
    side=next(iter(sides));m=snapshot(rows,side,[s for s,_ in candidates],dict(levels=levels),ob)
    if not m or not m.get('atr14') or m['atr14']<=0 or m.get('opposing_wick_pct') is None:
        result['reason']='Skip: ATR/wick measurements unavailable';return result
    m.update(provisional=True,atr_basis='Wilder ATR14 including forming hourly candle')
    result['metrics']=m
    for ref in m['references']:
        distance=(price-ref['price'])*(1 if side=='Long' else -1)/m['atr14']
        reasons=[]
        if m['opposing_wick_pct']>RULES['max_opposing_wick_pct']+1e-10:reasons.append('opposing wick above 30%')
        if distance<RULES['minimum_distance_atr']-1e-10:reasons.append('clearance below 0.05 ATR')
        if distance>RULES['maximum_distance_atr']+1e-10:reasons.append('entry beyond 0.50 ATR')
        result['source_checks'].append(dict(source=ref['source'],label=ref['label'],boundary=ref['price'],distance_atr=distance,passed=not reasons,reasons=reasons))
    result['passed_sources']=sorted({r['source'] for r in result['source_checks'] if r['passed']})
    if result['passed_sources']:
        result.update(signal=side,reason='Would enter '+side+' (signal conditions only; account/risk eligibility not evaluated)')
    else:result['reason']='Skip: no source passed rejection/distance rules'
    return result


class LateHourComparison:
    def __init__(self,path,live_path,clock=time.time):
        self.path=str(path);self.live_path=Path(live_path);self.clock=clock
        self.inputs=queue.Queue(maxsize=64);self.stop_event=threading.Event();self.thread=None
        self.lock=threading.RLock();self.states={};self.dropped=0;self.error=None
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(json.dumps(ComparisonSettings().model_dump()),))
            db.execute('CREATE TABLE IF NOT EXISTS observations(symbol TEXT,bar INTEGER,identity TEXT,updated REAL,payload TEXT,PRIMARY KEY(symbol,bar,identity))')
            self.enabled=json.loads(db.execute('SELECT payload FROM settings WHERE id=1').fetchone()[0])['enabled']
    def db(self):return sqlite3.connect(self.path,timeout=2)
    def configure(self,value):
        with self.lock,self.db() as db:
            self.enabled=value.enabled
            db.execute('UPDATE settings SET payload=? WHERE id=1',(json.dumps(value.model_dump()),))
        return self.report()
    def submit(self,symbol,cfg,history,observed_at):
        """Bounded nonblocking callback. No database or network work on LIVE thread."""
        if not self.enabled or cfg.strategy!=STRATEGY:return
        # Compare last five minutes; first five minutes are for closed-hour matching.
        if not (observed_at%3600>=3300 or observed_at%3600<300):return
        try:self.inputs.put_nowait((symbol,cfg,tuple(history),observed_at))
        except queue.Full:self.dropped+=1
        except Exception:self.dropped+=1
    def process(self,item):
        symbol,cfg,rows,observed_at=item
        with self.lock:
            if not self.enabled or cfg.strategy!=STRATEGY or symbol not in cfg.symbols:return
            identity=strategy_fingerprint(cfg);bar=int(observed_at)//3600*3600
            key=(symbol,identity)
            state=self.states.get(key)
            if state is None:state=self.states[key]=SourceState(cfg)
            if len(self.states)>300:
                oldest=next(iter(self.states))
                if oldest!=key:self.states.pop(oldest)
            with self.db() as db:
                if observed_at%3600<300:
                    old=db.execute('SELECT payload FROM observations WHERE symbol=? AND bar=? AND identity=?',(symbol,bar-3600,identity)).fetchone()
                    if not old:return
                    record=json.loads(old[0])
                    if record.get('closed'):return
                    state.advance(rows,observed_at)
                    if not state.sr_report or state.sr_report['time']!=bar-3600:return
                    sources=list(state.sr_report.get('entry_sources',[]))+list(state.ob_report.get('entry_sources',[]))
                    sides={r['signal'] for r in (state.sr_report,state.ob_report) if r.get('signal')}
                    conflict=len(sides)>1 or any(r.get('conflict') for r in (state.sr_report,state.ob_report))
                    signal=next(iter(sides)) if len(sides)==1 and not conflict else None
                    early=record.get('first_pass')
                    record['closed']=dict(bar=bar-3600,checked_at=self.clock(),signal=signal,sources=sorted(set(sources)),conflict=conflict,
                        same_direction=bool(early and signal==early['signal']),same_sources=sorted(set(early['passed_sources'])&set(sources)) if early else [])
                    self._save(db,symbol,bar-3600,identity,record);return
                if observed_at%3600<3300:return
                sample=decision(rows,cfg,observed_at,self.clock(),state)
                old=db.execute('SELECT payload FROM observations WHERE symbol=? AND bar=? AND identity=?',(symbol,bar,identity)).fetchone()
                record=json.loads(old[0]) if old else dict(symbol=symbol,bar=bar,identity=identity,first_seen_at=observed_at,first_pass=None)
                if record.get('latest',{}).get('observed_at',0)>observed_at:return
                record['latest']=sample
                if sample['signal'] and not record['first_pass']:record['first_pass']=deepcopy(sample)
                self._save(db,symbol,bar,identity,record)
    def _save(self,db,symbol,bar,identity,record):
        db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',(symbol,bar,identity,self.clock(),json.dumps(record,allow_nan=False)))
        db.execute('DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations ORDER BY bar DESC LIMIT -1 OFFSET 5000)')
    def _live_matches(self,items):
        """Exact symbol/hour/profile match; never infer a fill from chart arrows."""
        path=self.live_path.with_suffix('.execution.sqlite')
        if not path.exists():return
        pairs={}
        for row in items:pairs.setdefault((row['symbol'],row['bar']+3600),[]).append(row)
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
            for payload,phase,instrument,result in db.execute("SELECT payload,phase,instrument,result FROM execution WHERE json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_strategy')=? AND json_extract(instrument,'$.thoth_entry_diagnostics.signal_bar_closed_at')>=?",(STRATEGY,min((b for _,b in pairs),default=0))):
                p=json.loads(payload);meta=json.loads(instrument);stamp=(meta.get('thoth_entry_diagnostics') or {}).get('signal_bar_closed_at')
                for row in pairs.get((p['symbol'],stamp),[]):row.setdefault('live_orders',[]).append(dict(side=p['side'],phase=phase,link=p.get('orderLinkId'),fill_price=json.loads(result).get('entry_timing',{}).get('fill_price')))
    def report(self,limit=50,offset=0):
        with self.lock,self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute('SELECT payload FROM observations ORDER BY bar DESC,symbol LIMIT ? OFFSET ?',(limit,offset))]
        matching_error=None
        try:self._live_matches(items)
        except (sqlite3.Error,ValueError,TypeError):matching_error='Linked LIVE order lookup unavailable'
        for row in items:
            chosen=row.get('first_pass') or row['latest']
            row.update(decision=chosen['reason'],observed_at=chosen['observed_at'],signal=chosen['signal'],price=chosen['price'])
        return dict(enabled=self.enabled,mode='signal_comparison_only',strategy=STRATEGY,rules=RULES,items=items,total=total,limit=limit,offset=offset,
            dropped=self.dropped,error=self.error,matching_error=matching_error,server_time=self.clock(),limitations='Sampled observations; no orders, simulated fills or hypothetical P&L. Risk checks are not evaluated. Uses existing downloads only; a signal between scans can be missed. Missing closed-hour observations remain unverified.')
    def run(self):
        while not self.stop_event.is_set():
            try:item=self.inputs.get(timeout=.5)
            except queue.Empty:continue
            try:self.process(item);self.error=None
            except Exception as exc:self.error=str(exc)[:160]
            finally:self.inputs.task_done()
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self.run,daemon=True,name='late-hour-comparison');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
