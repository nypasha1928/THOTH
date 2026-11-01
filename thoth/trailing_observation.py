"""Read-only portfolio trailing research. No order client or execution methods.

The risk loop only queues small snapshots. A separate worker owns this database
and public-price requests; failures never block or change fixed trailing state.
"""

from .database import connect
import json
import math
import queue
import sqlite3
import statistics
import threading
import time
from copy import deepcopy
from pathlib import Path
from urllib.request import urlopen
from pydantic import BaseModel,ConfigDict,Field,model_validator


class ObservationSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool=True
    minimum: float=Field(default=.1,gt=0,le=100000)
    maximum: float=Field(default=.4,gt=0,le=100000)
    multiplier: float=Field(default=2,gt=0,le=20)
    fee_bps: float=Field(default=5.5,ge=0,le=100)

    @model_validator(mode='after')
    def ordered(self):
        if self.maximum<self.minimum:raise ValueError('Maximum distance must be at least minimum distance')
        return self


def advance_dynamic(state,pnl,activation,now,settings):
    """Volatility of 5-second-equivalent dollar P&L changes, window 60.

    Eight returns required; until then use maximum distance (explicit warm-up).
    Ratchet, rather than widening a previously protected dollar threshold.
    """
    state=deepcopy(state)
    if state.get('triggered'):return state
    if not math.isfinite(pnl):raise ValueError('Invalid simulated P&L')
    last=state.get('last_time');previous=state.get('current')
    if last is not None and now<=last:return state
    returns=state.get('returns',[])
    if previous is not None and last is not None:
        elapsed=now-last
        if elapsed<=45:returns=(returns+[(pnl-previous)/math.sqrt(elapsed/5)])[-60:]
        else:state['gap']=True;returns=[]
    ready=len(returns)>=8
    distance=max(settings['minimum'],min(settings['maximum'],settings['multiplier']*statistics.pstdev(returns))) if ready else settings['maximum']
    state.update(current=pnl,last_time=now,returns=returns,distance=distance,warming_up=not ready)
    if pnl>=activation and not state.get('active'):state.update(active=True,peak=pnl)
    if state.get('active'):
        peak=max(state['peak'],pnl)
        threshold=max(state.get('threshold',-math.inf),peak-distance)
        state.update(peak=peak,threshold=threshold)
        if pnl<=threshold:state.update(triggered=True,triggered_at=now,result_gross=pnl)
    return state


def public_marks():
    # One public batch for all virtual baskets, at most once per 15 seconds.
    with urlopen('https://api.bybit.com/v5/market/tickers?category=linear',timeout=5) as response:body=json.load(response)
    if body.get('retCode')!=0:raise ValueError('Public marks unavailable')
    observed=time.time();published=float(body.get('time',0))/1000
    if not math.isfinite(published) or abs(observed-published)>30:raise ValueError('Stale public price response')
    marks={}
    for row in body['result']['list']:
        value=float(row.get('markPrice') or 0)
        if math.isfinite(value) and value>0:marks[row['symbol']]=value
    return marks


def signature(positions):
    return sorted((p['link'],p['symbol'],p['side'],p['qty'],p['entry']) for p in positions)


def virtual_pnl(positions,marks):
    total=0.
    for p in positions:
        mark=marks[p['symbol']]
        if not math.isfinite(mark) or mark<=0:raise ValueError('Invalid public mark')
        total+=(mark-p['entry'])*p['qty']*(1 if p['side']=='Buy' else -1)
    return total


class TrailingObservation:
    def __init__(self,path,live_path,fetch=public_marks,clock=time.time):
        self.path=str(path);self.live_path=str(live_path);self.fetch=fetch;self.clock=clock
        self.inputs=queue.Queue(maxsize=256);self.stop_event=threading.Event();self.thread=None
        self.lock=threading.RLock();self.dropped=0;self.error=None
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO metadata VALUES(1,?)',(json.dumps(dict(settings=ObservationSettings().model_dump(),current=None,sequence=0)),))
            db.execute('CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY,started REAL NOT NULL,payload TEXT NOT NULL)')
        self.last_fetch=0.

    def db(self):return connect(self.path,timeout=2)

    def submit(self,active,cfg,fixed,owners,now):
        """Nonblocking callback; cannot write settings, orders, peaks or journals."""
        if not cfg.pnl_trailing_enabled:return
        try:
            positions=[dict(symbol=s,link=owners[s]['link'],side=p['side'],qty=float(p['size']),entry=float(p['avgPrice'])) for s,p in active.items()]
            marks={s:float(p['markPrice']) for s,p in active.items()}
            self.inputs.put_nowait(dict(positions=positions,marks=marks,fixed=deepcopy(fixed),
                activation=cfg.pnl_trailing_activation_dollars,fixed_drop=cfg.pnl_trailing_drop_dollars,at=now))
        except Exception:self.dropped+=1

    def configure(self,settings):
        with self.lock,self.db() as db:
            meta=json.loads(db.execute('SELECT payload FROM metadata WHERE id=1').fetchone()[0]);meta['settings']=settings.model_dump()
            db.execute('UPDATE metadata SET payload=? WHERE id=1',(json.dumps(meta),))
        return self.report()

    def _save(self,db,cycle):
        db.execute('INSERT OR REPLACE INTO cycles VALUES(?,?,?)',(cycle['id'],cycle['started'],json.dumps(cycle,allow_nan=False)))

    def _sample(self,cycle,marks,now):
        if cycle['dynamic'].get('triggered') or cycle.get('expired'):return
        if now-cycle['started']>86400:cycle['expired']=True;return
        try:
            pnl=virtual_pnl(cycle['positions'],marks)
            cycle['dynamic']=advance_dynamic(cycle['dynamic'],pnl,cycle['activation'],now,cycle['settings'])
            cycle['last_sample']=now
            if cycle['dynamic'].get('gap'):cycle['warnings']=sorted(set(cycle['warnings']+['Price sampling gap; trigger timing is uncertain']))
            if cycle['dynamic'].get('triggered'):
                notional=sum((p['entry']+marks[p['symbol']])*p['qty'] for p in cycle['positions'])
                cycle['assumed_fees']=notional*cycle['settings']['fee_bps']/10000
                cycle['estimate_after_assumed_fees_before_funding']=pnl-cycle['assumed_fees']
        except (KeyError,ValueError,TypeError):
            cycle['warnings']=sorted(set(cycle['warnings']+['Missing or invalid market prices; no exit inferred from this sample']))

    def process(self,item):
        with self.lock,self.db() as db:
            meta=json.loads(db.execute('SELECT payload FROM metadata WHERE id=1').fetchone()[0])
            if not meta['settings']['enabled']:return
            current=db.execute('SELECT payload FROM cycles WHERE id=?',(meta['current'],)).fetchone() if meta['current'] else None
            cycle=json.loads(current[0]) if current else None
            if cycle and not cycle.get('fixed_triggered') and signature(cycle['positions'])!=signature(item['positions']):
                cycle['warnings']=sorted(set(cycle['warnings']+['Position basket changed before fixed close; not a matched fixed/dynamic comparison']))
                cycle['basket_changed']=True;cycle['actual_ended']=item['at'];self._save(db,cycle)
                meta['current']=None;cycle=None
            if cycle and not cycle.get('fixed_triggered') and (not item['fixed'].get('active') or cycle['activation']!=item['activation'] or cycle['fixed_drop']!=item['fixed_drop']):
                cycle['warnings']=sorted(set(cycle['warnings']+['Fixed trailing cycle or settings changed; comparison incomplete']))
                cycle['actual_ended']=item['at'];self._save(db,cycle);meta['current']=None;cycle=None
            if cycle is None and item['positions'] and item['fixed'].get('active'):
                ongoing=sum(not json.loads(row[0]).get('dynamic',{}).get('triggered') and not json.loads(row[0]).get('expired') for row in db.execute('SELECT payload FROM cycles'))
                if ongoing>=20:self.error='Observation capacity reached (20 virtual baskets)';self.dropped+=1;return
                meta['sequence']+=1;meta['current']=meta['sequence']
                cycle=dict(id=meta['current'],started=item['at'],positions=item['positions'],settings=meta['settings'],activation=item['activation'],
                    fixed_drop=item['fixed_drop'],dynamic={},warnings=[],last_sample=item['at'])
                if item['fixed'].get('triggered'):cycle['warnings'].append('First observation arrived at fixed trigger; activation history unavailable')
                elif float(item['fixed'].get('peak') or 0)>float(item['fixed'].get('current') or 0):cycle['warnings'].append('Observation began after an earlier fixed peak; earlier dynamic path unavailable')
            if cycle:
                if self.dropped:cycle['warnings']=sorted(set(cycle['warnings']+['Observation snapshots dropped; coverage incomplete']))
                if all(p['symbol'] in item['marks'] for p in cycle['positions']):self._sample(cycle,item['marks'],item['at'])
                if item['fixed'].get('triggered') and not cycle.get('fixed_triggered'):
                    cycle.update(fixed_triggered=item['fixed']['triggered_at'],fixed_gross=float(item['fixed']['current']),fixed_peak=float(item['fixed']['peak']),fixed_threshold=float(item['fixed']['threshold']))
                if cycle.get('fixed_triggered') and not item['fixed'].get('triggered'):
                    cycle['actual_ended']=item['at'];meta['current']=None
                self._save(db,cycle)
            db.execute('UPDATE metadata SET payload=? WHERE id=1',(json.dumps(meta),))
            # Retain 100 recent records, never prune an unfinished continuation.
            db.execute('DELETE FROM cycles WHERE id NOT IN (SELECT id FROM cycles ORDER BY id DESC LIMIT 100) AND (json_extract(payload,\'$.dynamic.triggered\')=1 OR json_extract(payload,\'$.expired\')=1)')

    def _realized(self,cycle):
        if not cycle.get('fixed_triggered') or cycle.get('realized') is not None:return
        try:
            links=[p['link'] for p in cycle['positions']]
            with connect('file:'+self.live_path+'?mode=ro',uri=True,timeout=1) as db:
                rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM trade_history WHERE link IN ('+','.join('?' for _ in links)+')',links)]
            if len(rows)==len(links):
                cycle['realized']=sum(float(row['pnl']) for row in rows)
                cycle['actual_closed_at']=max(row['closed_at'] for row in rows)
                if any(row.get('reason')!='Open P&L trailing stop' for row in rows):
                    cycle['warnings']=sorted(set(cycle['warnings']+['Some actual positions closed by a different exit rule']))
        except (sqlite3.Error,ValueError,KeyError):pass

    def tick(self):
        now=self.clock()
        with self.lock,self.db() as db:
            meta=json.loads(db.execute('SELECT payload FROM metadata WHERE id=1').fetchone()[0])
            if not meta['settings']['enabled']:return
            cycles=[json.loads(row[0]) for row in db.execute('SELECT payload FROM cycles')]
            continuing=[c for c in cycles if c['id']!=meta['current'] or c.get('fixed_triggered')]
            need=any(not c['dynamic'].get('triggered') and not c.get('expired') for c in continuing)
        marks=None
        if need and now-self.last_fetch>=15:
            self.last_fetch=now
            try:marks=self.fetch();self.error=None
            except Exception as exc:self.error='Public observation prices unavailable: '+type(exc).__name__
        with self.lock,self.db() as db:
            # Re-read after network; settings/report callbacks never wait for it.
            latest=json.loads(db.execute('SELECT payload FROM metadata WHERE id=1').fetchone()[0])
            if not latest['settings']['enabled']:return
            for cycle in [json.loads(row[0]) for row in db.execute('SELECT payload FROM cycles')]:
                if marks is not None and (cycle['id']!=meta['current'] or cycle.get('fixed_triggered')):self._sample(cycle,marks,now)
                self._realized(cycle);self._save(db,cycle)

    def report(self,limit=50,offset=0):
        with self.lock,self.db() as db:
            meta=json.loads(db.execute('SELECT payload FROM metadata WHERE id=1').fetchone()[0])
            total=db.execute('SELECT COUNT(*) FROM cycles').fetchone()[0]
            records=[json.loads(row[0]) for row in db.execute('SELECT payload FROM cycles ORDER BY id DESC LIMIT ? OFFSET ?',(limit,offset))]
        for row in records:
            row['position_count']=len(row['positions'])
            row['symbols']=[p['symbol'] for p in row['positions']]
            row.pop('positions')
            row['dynamic'].pop('returns',None)
            if not row['dynamic'].get('triggered') and not row.get('expired') and self.clock()-row['last_sample']>45:row['warnings']=sorted(set(row['warnings']+['No fresh virtual observation; do not treat the displayed threshold as current']))
            if self.dropped:row['warnings']=sorted(set(row['warnings']+['Observation snapshots dropped; coverage incomplete']))
            row['gross_quote_difference']=(row['dynamic']['result_gross']-row['fixed_gross']) if row.get('fixed_gross') is not None and row['dynamic'].get('triggered') and not row['warnings'] else None
        return dict(settings=meta['settings'],items=records,total=total,offset=offset,limit=limit,dropped=self.dropped,error=self.error,mode='observation_only',executor='fixed')

    def run(self):
        while not self.stop_event.is_set():
            try:
                for _ in range(256):
                    try:item=self.inputs.get_nowait()
                    except queue.Empty:break
                    self.process(item)
                self.tick()
            except Exception as exc:self.error='Observation unavailable: '+type(exc).__name__
            self.stop_event.wait(1)

    def start(self):
        self.thread=threading.Thread(target=self.run,name='thoth-trailing-observation',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=7)
