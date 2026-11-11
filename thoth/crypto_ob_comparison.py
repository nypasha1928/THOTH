"""Private sampled OB research. No execution client or live database writes."""
import json
import queue
import threading
import time
import hashlib
from copy import deepcopy
from .database import connect
from .crypto_ob_strength import STRATEGY,THRESHOLDS,StrengthState
from .indicator_stream import validate


class CryptoOBComparison:
    def __init__(self,path,clock=time.time):
        self.path=str(path);self.clock=clock;self.lock=threading.RLock()
        self.inputs=queue.Queue(maxsize=64);self.halt=threading.Event();self.thread=None
        self.error=None;self.dropped=0
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY,enabled INTEGER)')
            db.execute('INSERT OR IGNORE INTO control VALUES(1,1)')
            db.execute('CREATE TABLE IF NOT EXISTS states(symbol TEXT,identity TEXT,payload TEXT,PRIMARY KEY(symbol,identity))')
            db.execute('CREATE TABLE IF NOT EXISTS samples(symbol TEXT,bar INTEGER,identity TEXT,payload TEXT,PRIMARY KEY(symbol,bar,identity))')
            self.enabled=bool(db.execute('SELECT enabled FROM control WHERE id=1').fetchone()[0])
    def db(self):return connect(self.path,timeout=2)
    def configure(self,value):
        with self.lock,self.db() as db:
            self.enabled=value.enabled;db.execute('UPDATE control SET enabled=? WHERE id=1',(int(self.enabled),))
            if not self.enabled:db.execute("UPDATE states SET payload=json_set(payload,'$.observed_at',0)")
        return self.report()
    def submit(self,symbol,cfg,rows,observed_at):
        if not self.enabled or cfg.strategy!=STRATEGY or symbol not in THRESHOLDS:return
        try:self.inputs.put_nowait((symbol,cfg,tuple(dict(r) for r in rows),observed_at))
        except queue.Full:self.dropped+=1
    @staticmethod
    def settings(cfg):
        return dict(notional=cfg.position_value,stop=cfg.stop_dollars,
            trailing=cfg.coin_pnl_trailing_enabled,activation=cfg.coin_pnl_trailing_activation_dollars,
            drop=cfg.coin_pnl_trailing_drop_dollars,fee_bps=5.5,slippage_bps=2.)
    @staticmethod
    def paper(leg,price,signal,now,settings,allow=True):
        """Observed prices only; never claim an exchange fill or tick replay."""
        events=[];position=leg.get('position');exited=False
        if position:
            side=position['side'];gross=side*(price-position['entry'])*position['qty']
            position['peak']=max(position['peak'],gross)
            if settings['trailing'] and gross>=settings['activation']:position['active']=True
            reason='Opposite qualified formation' if signal and signal!=side else None
            if gross<=-settings['stop']:reason='Sampled hard stop'
            if position['active'] and gross<=position['peak']-settings['drop']:reason='Sampled per-coin trailing'
            if reason:
                fill=price*(1-side*settings['slippage_bps']/10000)
                net=side*(fill-position['entry'])*position['qty']-(fill+position['entry'])*position['qty']*settings['fee_bps']/10000
                leg['closed_net']=leg.get('closed_net',0)+net;leg['closed_trades']=leg.get('closed_trades',0)+1
                events.append(dict(kind='paper_close',at=now,reason=reason,net=net,side=side,entry=position['entry'],exit=fill))
                leg['position']=None;exited=True
        if signal and allow and not leg.get('position') and (not exited or events[-1]['reason']=='Opposite qualified formation'):
            entry=price*(1+signal*settings['slippage_bps']/10000)
            leg['position']=dict(side=signal,entry=entry,qty=settings['notional']/entry,opened=now,peak=0,active=False)
            events.append(dict(kind='paper_entry',at=now,side=signal,price=entry))
        return events
    def process(self,item):
        symbol,cfg,rows,observed=item
        if not self.enabled or cfg.strategy!=STRATEGY or symbol not in cfg.symbols:return
        now=self.clock();bar=int(observed)//3600*3600
        if not 0<=now-observed<=30:raise ValueError('Observation older than 30 seconds')
        if not rows or rows[-1]['time']!=bar:raise ValueError('Current hourly candle unavailable')
        for row in rows:validate(row)
        if any(b['time']!=a['time']+3600 for a,b in zip(rows,rows[1:])):raise ValueError('Observation history gap')
        settings=self.settings(cfg);identity=hashlib.sha256(json.dumps(dict(settings=settings,minimum=THRESHOLDS[symbol]),sort_keys=True).encode()).hexdigest()
        with self.lock,self.db() as db:
            if not self.enabled:return
            saved=db.execute('SELECT payload FROM states WHERE symbol=? AND identity=?',(symbol,identity)).fetchone()
            state=json.loads(saved[0]) if saved else {}
            last=state.get('observed_at');continuous=last is not None and 0<observed-last<=120
            if last is not None and observed<=last:return
            revised=any(old!=next((r for r in rows if r['time']==old['time']),old) for old in state.get('recent',[]))
            if revised:continuous=False
            machine=StrengthState(symbol,None if revised else state.get('checkpoint'));old_bar=machine.last_time
            closed_signal=None;closed_result=None
            for row in rows[:-1]:
                if machine.last_time is not None and row['time']<=machine.last_time:continue
                if machine.last_time is not None and row['time']!=machine.last_time+3600:raise ValueError('Missing confirmed hourly history')
                result=machine.step(row)
                stored=db.execute('SELECT payload FROM samples WHERE symbol=? AND bar=? AND identity=?',(symbol,row['time'],identity)).fetchone()
                if stored:
                    rec=json.loads(stored[0]);early=rec.get('early')
                    rec['closed']=dict(signal=result['signal'],price=row['close'],checked_at=observed,evidence=result['crypto_ob_strength'])
                    rec['survived']=bool(early and result['signal']==early['signal'])
                    if early and result['signal']==early['signal']:
                        rec['early_price_advantage_pct']=(row['close']-early['price'])/early['price']*100*(1 if early['signal']=='Long' else -1)
                    db.execute('UPDATE samples SET payload=? WHERE symbol=? AND bar=? AND identity=?',(json.dumps(rec),symbol,row['time'],identity))
                if continuous and row['time']==bar-3600 and old_bar==bar-7200:
                    closed_signal=1 if result['signal']=='Long' else -1 if result['signal']=='Short' else None;closed_result=result
            if machine.last_time!=bar-3600 or machine.count<14:raise ValueError('Hourly comparison warming up')
            preview=StrengthState(symbol,machine.checkpoint()).step(rows[-1]);price=rows[-1]['close']
            stored=db.execute('SELECT payload FROM samples WHERE symbol=? AND bar=? AND identity=?',(symbol,bar,identity)).fetchone()
            rec=json.loads(stored[0]) if stored else dict(symbol=symbol,bar=bar,identity=identity,settings=settings,minimum_strength=THRESHOLDS[symbol],early=None)
            signal=preview['signal'];prior=state.get('preview_signal') if state.get('bar')==bar else None;early_signal=None
            consumed=state.get('consumed',[]) if state.get('bar')==bar else []
            if continuous and signal and signal!=prior and signal not in consumed:
                consumed.append(signal);early_signal=1 if signal=='Long' else -1
                sample=dict(signal=signal,price=price,observed_at=observed,evidence=preview['crypto_ob_strength'])
                if not rec['early']:rec['early']=sample
                rec.setdefault('direction_changes',[]).append(sample)
            rec['latest']=dict(signal=signal,price=price,observed_at=observed)
            rec['baseline_only']=not continuous;rec['gap_seconds']=observed-last if last else None
            legs=state.get('paper',{'intrabar':{},'closed':{}})
            # Missing intervals cannot be simulated honestly; stop those paper episodes.
            if not continuous:
                for leg in legs.values():
                    if leg.get('position'):leg['unverified_episodes']=leg.get('unverified_episodes',0)+1
                    leg['position']=None
            events={name:self.paper(legs[name],price,sig,observed,settings) for name,sig in [('intrabar',early_signal),('closed',closed_signal)]}
            rec['paper_events']={name:(rec.get('paper_events',{}).get(name,[])+events[name])[-100:] for name in events}
            if closed_result:rec['closed_entry_observation']=dict(signal=closed_result['signal'],price=price,observed_at=observed)
            state.update(recent=[dict(r) for r in rows[-4:-1]],checkpoint=machine.checkpoint(),observed_at=observed,preview_signal=signal,bar=bar,consumed=consumed,paper=legs)
            db.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?,?)',(symbol,bar,identity,json.dumps(rec,allow_nan=False)))
            db.execute('INSERT OR REPLACE INTO states VALUES(?,?,?)',(symbol,identity,json.dumps(state,allow_nan=False)))
            db.execute('DELETE FROM samples WHERE rowid IN (SELECT rowid FROM samples ORDER BY bar DESC LIMIT -1 OFFSET 5000)')
            db.execute('DELETE FROM states WHERE rowid IN (SELECT rowid FROM states ORDER BY json_extract(payload,\'$.observed_at\') DESC LIMIT -1 OFFSET 300)')
    def report(self,limit=50,offset=0):
        with self.lock,self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
            items=[json.loads(x[0]) for x in db.execute('SELECT payload FROM samples ORDER BY bar DESC,symbol LIMIT ? OFFSET ?',(limit,offset))]
            states=[json.loads(x[0]) for x in db.execute('SELECT payload FROM states')]
        totals={name:dict(closed_net=sum(s.get('paper',{}).get(name,{}).get('closed_net',0) for s in states),closed_trades=sum(s.get('paper',{}).get(name,{}).get('closed_trades',0) for s in states),unverified_episodes=sum(s.get('paper',{}).get(name,{}).get('unverified_episodes',0) for s in states)) for name in ('intrabar','closed')}
        return dict(enabled=self.enabled,mode='ob_strength_observation_only',items=items,total=total,limit=limit,offset=offset,paper=totals,error=self.error,dropped=self.dropped,latest_observed_at=max((s.get('observed_at',0) for s in states),default=None),server_time=self.clock(),limitations='Sampled independent per-coin paper episodes; no orders or actual fills. Same saved sizing/hard stop/per-coin trailing; modeled 5.5bps fee + 2bps slippage per side. No combined trailing, account limits, capacity, schedules, breakeven, funding or liquidation replay. First/reconnected sample establishes baseline; gaps over 120s discard open paper episodes as unverified. Hour-close price advantage compares candle close, not actual fill. Paper totals cover retained settings identities; open P&L excluded. Scans may miss intrabar signals or stop/peak touches.')
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.halt.clear();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        while not self.halt.is_set():
            try:item=self.inputs.get(timeout=.25)
            except queue.Empty:continue
            try:self.process(item);self.error=None
            except Exception as exc:self.error=type(exc).__name__+': '+str(exc)[:160]
            finally:self.inputs.task_done()
    def stop(self):
        self.halt.set()
        if self.thread:self.thread.join(timeout=3)
