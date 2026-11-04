"""Read-only :25/:30 Volume Delta research; no order client or execution writes."""
from .database import connect
from .verification import strategy_fingerprint
from .volume_delta import signal
from .volume_delta_midpoint import STRATEGY,STRATEGIES
from .entry_diagnostics import number
import json
import queue
import threading
import time


def early_window(now):return 1500<=now%3600<1560

def compared(record,now):
    a,b=record.get('at25'),record.get('at30')
    result=dict(status='Waiting for :30' if now<record['bar']+1860 else 'Missing :30 observation',early_price_advantage_pct=None)
    if not a:result['status']='Missing :25 observation'
    elif b:
        if not a.get('valid') or not b.get('valid'):result['status']='Unverified: missing or invalid data'
        elif a['signal'] and b['signal']:
            result['status']='Same direction' if a['signal']==b['signal'] else 'Direction changed'
            if a['signal']==b['signal']:
                result['early_price_advantage_pct']=(b['price']-a['price'])/a['price']*100*(1 if a['signal']=='Long' else -1)
        elif a['signal']:result['status']=':25 only; signal absent at :30'
        elif b['signal']:result['status']=':30 only; no signal at :25'
        else:result['status']='Neither qualified'
    close=record.get('hour_close')
    result['move_to_hour_close_pct']={phase:((close['price']-sample['price'])/sample['price']*100*(1 if sample['signal']=='Long' else -1)) if close and sample and sample.get('valid') and sample.get('signal') else None for phase,sample in [('at25',a),('at30',b)]}
    return result


class VolumeDeltaComparison:
    def __init__(self,path,clock=time.time):
        self.path=str(path);self.clock=clock;self.lock=threading.RLock()
        self.inputs=queue.Queue(maxsize=256);self.stop_event=threading.Event();self.thread=None;self.dropped=0;self.error=None
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(json.dumps({'enabled':True}),))
            db.execute('CREATE TABLE IF NOT EXISTS observations(symbol TEXT,bar INTEGER,identity TEXT,payload TEXT,PRIMARY KEY(symbol,bar,identity))')
            self.enabled=json.loads(db.execute('SELECT payload FROM settings WHERE id=1').fetchone()[0])['enabled']
    def db(self):return connect(self.path,timeout=2)
    def configure(self,settings):
        with self.lock,self.db() as db:
            self.enabled=settings.enabled
            db.execute('UPDATE settings SET payload=? WHERE id=1',(json.dumps({'enabled':self.enabled}),))
        return self.report()
    def submit(self,phase,symbol,cfg,rows,lower,observed_at,decision=None,reason=None):
        if not self.enabled or cfg.strategy not in STRATEGIES:return
        if phase=='at25' and not early_window(observed_at):return
        if phase=='at30' and not 1800<=observed_at%3600<1860:return
        try:self.inputs.put_nowait((phase,symbol,cfg,tuple(dict(r) for r in rows[-60:]),tuple(dict(r) for r in lower),observed_at,dict(decision) if decision is not None else None,reason))
        except queue.Full:self.dropped+=1
        except Exception:self.dropped+=1
    def process(self,item):
        phase,symbol,cfg,rows,lower,observed_at,decision,reason=item
        if cfg.strategy not in STRATEGIES or symbol not in cfg.symbols:return
        identity=strategy_fingerprint(cfg);bar=int(observed_at)//3600*3600
        if phase=='close':
            closed=next((r for r in reversed(rows) if r['time']==bar-3600),None)
            if closed is None or number(closed.get('close')) is None or closed['close']<=0:return
            bar-=3600
        else:
            if phase not in ('at25','at30'):return
            if phase=='at25' and not early_window(observed_at):return
            if phase=='at30' and not 1800<=observed_at%3600<1860:return
            price=number(rows[-1].get('close')) if rows else None
            sample=dict(observed_at=observed_at,processed_at=self.clock(),price=price,signal=None,valid=False,reason=reason)
            if phase=='at30':
                if decision is not None and price is not None and price>0:
                    sample.update(signal=decision.get('signal'),valid=bool(decision.get('ready')),details=decision,reason=decision.get('status'),basis='Actual LIVE source decision; account/risk eligibility not evaluated')
            else:
                try:
                    if not 0<=self.clock()-observed_at<=30:raise ValueError('Observation processed too late; not a timely :25 decision')
                    if not rows or rows[-1]['time']!=bar:raise ValueError('Current hourly candle unavailable')
                    v=cfg.volume_delta
                    checked=signal(rows,lower,'60',observed_at,sma_filter=v.sma_filter,bull_type=v.bull_type,bear_type=v.bear_type,bullish_enabled=v.bullish,bearish_enabled=v.bearish)
                    sample.update(signal=checked['signal'],valid=bool(checked.get('ready')),details=checked,reason=checked['status'],basis='Read-only :25 source decision; account/risk eligibility not evaluated')
                except (ValueError,KeyError,TypeError) as exc:sample['reason']=str(exc)
            if not sample.get('reason'):sample['reason']='Observation data unavailable'
        with self.lock:
            if not self.enabled:return
            with self.db() as db:
                stored=db.execute('SELECT payload FROM observations WHERE symbol=? AND bar=? AND identity=?',(symbol,bar,identity)).fetchone()
                if phase=='close' and not stored:return
                record=json.loads(stored[0]) if stored else dict(symbol=symbol,bar=bar,identity=identity,filters=cfg.volume_delta.model_dump(),strategy=cfg.strategy)
                key='hour_close' if phase=='close' else phase
                if key in record:return  # First snapshot, even if it did not qualify.
                record[key]=dict(price=closed['close'],closed_at=bar+3600,checked_at=observed_at) if phase=='close' else sample
                db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?)',(symbol,bar,identity,json.dumps(record,allow_nan=False)))
                db.execute('DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations ORDER BY bar DESC LIMIT -1 OFFSET 20000)')
    def report(self,limit=50,offset=0):
        with self.lock,self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM observations ORDER BY bar DESC,symbol,identity LIMIT ? OFFSET ?',(limit,offset))]
            latest=db.execute("SELECT MAX(MAX(COALESCE(json_extract(payload,'$.at25.observed_at'),0),COALESCE(json_extract(payload,'$.at30.observed_at'),0))) FROM observations").fetchone()[0]
        for row in rows:row['comparison']=compared(row,self.clock())
        return dict(enabled=self.enabled,mode='signal_comparison_only',items=rows,total=total,limit=limit,offset=offset,dropped=self.dropped,error=self.error,latest_observed_at=latest,server_time=self.clock(),
            limitations='Sampled first :25 snapshot versus actual first :30 source decision. No orders, simulated fills or hypothetical P&L. Hour-close moves exclude fees, funding, slippage and all exit/risk rules. Missing observations remain unverified. Filter changes are not paired.')
    def run(self):
        while not self.stop_event.is_set():
            try:item=self.inputs.get(timeout=.5)
            except queue.Empty:continue
            try:self.process(item);self.error=None
            except Exception as exc:self.error=str(exc)[:240]
            finally:self.inputs.task_done()
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self.thread=threading.Thread(target=self.run,daemon=True,name='volume-delta-comparison');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
