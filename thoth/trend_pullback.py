"""Isolated prospective research. No exchange client, private API or live writes."""
import hashlib,json,queue,threading,time
from math import fsum,isfinite
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .database import connect
from .models import RSISettings
from .rsi import RSIState
from .indicator_stream import IndicatorState,validate
from .sr_bounce import evaluate as bounce
from .entry_schedule import reason as schedule_reason,daily_exit_period,FIELDS as SCHEDULE_FIELDS

SUPPORTED=('trend_major_pullback_30m_close','price_sma50_30m_close','ob_sr_30m_delayed','sr_rsi_30m_delayed','ob_sr_30m_close_two_direction')

class ResearchSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool=True
    risk_usdt: float=Field(default=.4,gt=0,le=1000,allow_inf_nan=False)
    max_notional_usdt: float=Field(default=1000,gt=0,le=100000,allow_inf_nan=False)
    max_distance_atr: float=Field(default=.25,gt=0,le=5,allow_inf_nan=False)
    stop_buffer_atr: float=Field(default=.1,gt=0,le=5,allow_inf_nan=False)
    breakeven_trigger_r: float=Field(default=1,gt=0,le=20,allow_inf_nan=False)
    breakeven_floor_r: float=Field(default=.25,ge=0,le=20,allow_inf_nan=False)
    trailing_trigger_r: float=Field(default=1.5,gt=0,le=20,allow_inf_nan=False)
    trailing_drop_r: float=Field(default=.5,gt=0,le=20,allow_inf_nan=False)
    fee_bps: float=Field(default=5.5,ge=0,le=100,allow_inf_nan=False)
    slippage_bps: float=Field(default=2,ge=0,le=100,allow_inf_nan=False)
    @model_validator(mode='after')
    def ordered(self):
        if self.breakeven_floor_r>=self.breakeven_trigger_r:raise ValueError('Breakeven floor must be below trigger')
        return self


def hourly(rows):
    by={r['time']:r for r in rows};out=[]
    for stamp in sorted(by):
        if stamp%3600 or stamp+1800 not in by:continue
        a,b=by[stamp],by[stamp+1800]
        out.append(dict(time=stamp,open=a['open'],close=b['close'],high=max(a['high'],b['high']),low=min(a['low'],b['low']),volume=a['volume']+b['volume']))
    return out


def atr14(rows):
    atr=None;seed=[];previous=None
    for r in rows:
        tr=r['high']-r['low'] if previous is None else max(r['high']-r['low'],abs(r['high']-previous),abs(r['low']-previous))
        if atr is None:
            seed.append(tr)
            if len(seed)==14:atr=sum(seed)/14
        else:atr=(atr*13+tr)/14
        previous=r['close']
    return atr


def qualify(rows,price,cfg,max_distance_atr=.25,slippage_bps=2,*,context_rsi=False):
    """Only closed data; use levels known BEFORE the deciding candle."""
    price=float(price)
    if not isfinite(price) or price<=0:raise ValueError('Pullback entry price must be positive and finite')
    result=dict(signal=None,reason='Insufficient closed hourly history')
    hours=hourly(rows)
    if len(hours)<200:return result
    hours=hours[-200:]
    if any(b['time']-a['time']!=3600 for a,b in zip(hours,hours[1:])):return result
    sma=fsum(r['close'] for r in hours)/200;trend=hours[-1]['close']
    result.update(sma200=sma,hour_close=trend,hour_closed_at=hours[-1]['time']+3600)
    if trend==sma:return {**result,'reason':'Hourly close equals SMA200'}
    direction='Long' if trend>sma else 'Short';sign=1 if direction=='Long' else -1
    source=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe='30',major=True,minor=False,sma_enabled=False,sma_7_50_filter_enabled=False))
    machine=IndicatorState(source);levels={}
    for row in rows[:-1]:levels=machine.step(row)['levels']
    if context_rsi:
        context=RSIState(RSISettings(smooth=False));item=None
        for candle in rows:item=context.step(candle,1800)
        result['context_rsi14']=item['rsi']
    last=rows[-1];raw=bounce(last,rows[-2]['close'],levels,source)
    atr=atr14(rows);result.update(trend=direction,atr14=atr,signal_candle=last,levels_before=levels,sources=raw['entry_sources'])
    if raw['conflict'] or raw['signal']!=direction:return {**result,'reason':'No trend-aligned Major bounce/rejection'}
    if not atr or atr<=0:return {**result,'reason':'ATR unavailable'}
    entry=price*(1+sign*slippage_bps/10000)
    result.update(observed_entry_price=price,modeled_entry_price=entry,max_distance_atr=max_distance_atr,entry_buffer_bps=slippage_bps,source_distances=[])
    refs=[]
    for src in raw['entry_sources']:
        key=src.split(':',1)[1];value=levels[key]['value'];distance=sign*(entry-value)/atr
        result['source_distances'].append(dict(source=src,boundary=value,distance_atr=distance))
        if 0<=distance<=max_distance_atr:refs.append((distance,src,value))
    if not refs:return {**result,'reason':'Entry outside allowed ATR distance'}
    distance,src,boundary=min(refs)
    return {**result,'signal':direction,'reason':'Would enter: trend + Major pullback confirmed','source':src,'boundary':boundary,'distance_atr':distance,'entry':entry}


def setup(rows,price,cfg,rules):
    result=qualify(rows,price,cfg,rules.max_distance_atr,rules.slippage_bps,context_rsi=True)
    if not result['signal']:return result
    direction=result['signal'];sign=1 if direction=='Long' else -1
    last=rows[-1];boundary=result['boundary'];entry=result['entry'];atr=result['atr14']
    stop=min(last['low'],boundary)-rules.stop_buffer_atr*atr if sign==1 else max(last['high'],boundary)+rules.stop_buffer_atr*atr
    risk_unit=sign*(entry-stop)
    if stop<=0 or risk_unit<=0:return {**result,'signal':None,'reason':'Invalid structural stop'}
    qty=rules.risk_usdt/risk_unit
    if qty*entry>rules.max_notional_usdt:return {**result,'signal':None,'reason':'Risk sizing exceeds simulated notional cap'}
    return {**result,'stop':stop,'qty':qty,'risk':rules.risk_usdt,
        'entry_notional':entry*qty}


def advance(position,price,now,rules,forced_reason=None):
    p=dict(position);sign=1 if p['side']=='Long' else -1;gross=sign*(price-p['entry'])*p['qty'];r=gross/p['risk']
    p.update(last_price=price,last_sample=now,current_r=r,mfe_r=max(p.get('mfe_r',0),r),mae_r=min(p.get('mae_r',0),r))
    if 'first_positive_at' not in p:
        p['pre_profit_mae_r']=min(p.get('pre_profit_mae_r',0),r)
        if r>0:p['first_positive_at']=now
    if r>=rules.breakeven_trigger_r:p['breakeven_armed']=True
    if r>=rules.trailing_trigger_r:p['trailing_armed']=True
    reason=forced_reason
    if not reason and sign*(price-p['stop'])<=0:reason='Structural stop'
    if not reason and p.get('trailing_armed') and r<=p['mfe_r']-rules.trailing_drop_r:reason='Simulated trailing'
    if not reason and p.get('breakeven_armed') and r<=rules.breakeven_floor_r:reason='Simulated breakeven'
    if reason:
        exit_price=price*(1-sign*rules.slippage_bps/10000)
        gross_exit=sign*(exit_price-p['entry'])*p['qty'];fees=(p['entry']+exit_price)*p['qty']*rules.fee_bps/10000
        p.update(status='closed',closed_at=now,exit=exit_price,exit_reason=reason,gross_usdt=gross_exit,fees_usdt=fees,net_usdt=gross_exit-fees,net_r=(gross_exit-fees)/p['risk'])
    return p


class TrendPullbackObservation:
    def __init__(self,path,clock=time.time):
        self.path=str(path);self.clock=clock;self.lock=threading.RLock();self.inputs=queue.Queue(maxsize=64)
        self.stop_event=threading.Event();self.thread=None;self.dropped=0;self.error=None;self.last_sample=None;self.baselines={}
        source=Path(__file__).parent
        self.model_hash=hashlib.sha256(b''.join((source/name).read_bytes() for name in ('trend_pullback.py','indicator_stream.py','indicator_core.py','sr_bounce.py','rsi.py'))).hexdigest()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(json.dumps(ResearchSettings().model_dump()),))
            self.settings=ResearchSettings(**json.loads(db.execute('SELECT payload FROM settings WHERE id=1').fetchone()[0]))
            db.execute('CREATE TABLE IF NOT EXISTS records(symbol TEXT,bar INTEGER,identity TEXT,payload TEXT,PRIMARY KEY(symbol,bar,identity))')
            self._interrupt(db,'Restart: continuous observation unavailable')
    def db(self):return connect(self.path,timeout=2)
    def _interrupt(self,db,reason):
        for symbol,bar,identity,payload in db.execute("SELECT symbol,bar,identity,payload FROM records WHERE json_extract(payload,'$.position.status')='open'").fetchall():
            item=json.loads(payload);item['position'].update(status='interrupted',unverified_reason=reason)
            self._store(db,item)
    def _store(self,db,item):
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',(item['symbol'],item['bar'],item['identity'],json.dumps(item,allow_nan=False)))
    def configure(self,settings):
        with self.lock,self.db() as db:
            self._interrupt(db,'Research settings changed');self.settings=settings;self.baselines={}
            db.execute('UPDATE settings SET payload=? WHERE id=1',(json.dumps(settings.model_dump()),))
        return self.report()
    def submit(self,symbol,cfg,history_path,latest,observed_at):
        if not self.settings.enabled or cfg.strategy not in SUPPORTED:return
        try:self.inputs.put_nowait((symbol,cfg,str(history_path),dict(latest),observed_at))
        except queue.Full:self.dropped+=1
        except Exception:self.dropped+=1
    def process(self,item):
        symbol,cfg,history_path,latest,observed_at=item
        if not self.settings.enabled or cfg.strategy not in SUPPORTED or symbol not in cfg.symbols:return
        if not 0<=self.clock()-observed_at<=30:raise ValueError('Research sample processed too late')
        validate(latest);current=int(observed_at)//1800*1800
        if latest['time']!=current:raise ValueError('Research requires fresh forming 30m snapshot')
        rules=self.settings
        signature={k:getattr(cfg,k) for k in (*SCHEDULE_FIELDS,'pivot_long','pivot_short','active_symbol_list')}
        identity=hashlib.sha256(json.dumps(dict(version=1,model_hash=self.model_hash,settings=rules.model_dump(exclude={'enabled'}),source=signature),sort_keys=True).encode()).hexdigest()
        key=(symbol,identity);previous=self.baselines.get(key);closed_bar=current-1800
        with self.lock,self.db() as db:
            if not self.settings.enabled or self.settings!=rules:return
            for payload, in db.execute("SELECT payload FROM records WHERE symbol=? AND json_extract(payload,'$.position.status')='open'",(symbol,)).fetchall():
                old=json.loads(payload);p=old['position'];gap=observed_at-p['last_sample']
                if gap<=0:continue
                if old['identity']!=identity or gap>120:p.update(status='interrupted',unverified_reason='Settings changed or observation gap over 120s')
                else:
                    p['max_sample_gap_seconds']=max(p.get('max_sample_gap_seconds',0),gap)
                    cutoff=daily_exit_period(cfg,observed_at)
                    forced='Scheduled daily cutoff' if cutoff and p['opened_at']<cutoff['cutoff_at']<=observed_at else None
                    old['position']=advance(p,latest['close'],observed_at,rules,forced)
                self._store(db,old)
            self.last_sample=observed_at
            if previous and observed_at<=previous['observed_at']:return
            self.baselines[key]=dict(bar=closed_bar,observed_at=observed_at)
            existing=db.execute('SELECT 1 FROM records WHERE symbol=? AND bar=? AND identity=?',(symbol,closed_bar,identity)).fetchone()
            if existing or previous and previous['bar']==closed_bar:return
            record=dict(symbol=symbol,bar=closed_bar,identity=identity,model_hash=self.model_hash,observed_at=observed_at,processed_at=self.clock(),live_strategy=cfg.strategy,group='TradFi watchlist' if cfg.active_symbol_list.startswith('tradfi') else 'Main watchlist',settings=rules.model_dump(),signal=None)
            if previous is None or observed_at-previous['observed_at']>120 or previous['bar']!=closed_bar-1800:
                record['reason']='Baseline/recovery: old setup not replayed'
            elif observed_at-current>120:record['reason']='Missed post-close observation window'
            elif schedule_reason(cfg,observed_at):record['reason']=schedule_reason(cfg,observed_at)
            else:
                with connect('file:'+history_path+'?mode=ro',uri=True,timeout=1) as history:
                    rows=[json.loads(r[0]) for r in history.execute("SELECT payload FROM candles WHERE symbol=? AND interval='30' AND time<=? ORDER BY time DESC LIMIT 1200",(symbol,closed_bar))][::-1]
                for row in rows:validate(row)
                if not rows or rows[-1]['time']!=closed_bar or any(b['time']-a['time']!=1800 for a,b in zip(rows,rows[1:])):
                    record['reason']='Missing/gapped closed 30m history'
                else:
                    active=db.execute("SELECT 1 FROM records WHERE symbol=? AND json_extract(payload,'$.position.status')='open'",(symbol,)).fetchone()
                    decision=setup(rows,latest['close'],cfg,rules);record.update(decision)
                    if decision['signal'] and not active:
                        record['position']=dict(status='open',opened_at=observed_at,last_sample=observed_at,side=decision['signal'],entry=decision['entry'],qty=decision['qty'],risk=decision['risk'],stop=decision['stop'],mfe_r=0,mae_r=0)
                        initial_r=(latest['close']-decision['entry'])*decision['qty']*(1 if decision['signal']=='Long' else -1)/decision['risk']
                        record['position'].update(mae_r=min(0,initial_r),pre_profit_mae_r=min(0,initial_r),current_r=initial_r,last_price=latest['close'])
                    elif active:record.update(signal=None,reason='Existing simulated position; no stacking')
            self._store(db,record)
            # Preserve all open positions; bound retained completed/skip records.
            db.execute("DELETE FROM records WHERE rowid IN (SELECT rowid FROM records WHERE COALESCE(json_extract(payload,'$.position.status'),'')!='open' ORDER BY bar DESC LIMIT -1 OFFSET 20000)")
    def report(self,limit=50,offset=0):
        with self.lock,self.db() as db:
            count=db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records ORDER BY bar DESC,symbol LIMIT ? OFFSET ?',(limit,offset))]
            simulations=[json.loads(r[0]) for r in db.execute("SELECT payload FROM records WHERE json_type(payload,'$.position') IS NOT NULL")]
            positions=[r['position'] for r in simulations]
        closed=[p for p in positions if p['status']=='closed'];net=sum(p['net_usdt'] for p in closed)
        return dict(enabled=self.settings.enabled,settings=self.settings.model_dump(),items=items,total=count,limit=limit,offset=offset,
            summary=dict(closed=len(closed),open=sum(p['status']=='open' for p in positions),interrupted=sum(p['status']=='interrupted' for p in positions),wins=sum(p['net_usdt']>0 for p in closed),net_usdt=net),
            by_group={group:dict(closed=sum(r['position']['status']=='closed' for r in simulations if r['group']==group),net_usdt=sum(r['position'].get('net_usdt',0) for r in simulations if r['group']==group and r['position']['status']=='closed')) for group in {r['group'] for r in simulations}},
            error=self.error,dropped=self.dropped,last_sample=self.last_sample,limitations='Observation only; no orders. Sampled last prices, not continuous tick fills. Estimated fees/slippage; no funding, exchange lot sizes, account eligibility, portfolio capacity or liquidation simulation. Interruptions have no invented result. Watchlist scopes are labelled separately; scope is not verified asset classification. Reuses cached 30m data; hourly SMA200 uses completed pairs only.')
    def run(self):
        while not self.stop_event.is_set():
            try:item=self.inputs.get(timeout=.5)
            except queue.Empty:continue
            try:self.process(item);self.error=None
            except Exception as exc:self.error=str(exc)[:240]
            finally:self.inputs.task_done()
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self.thread=threading.Thread(target=self.run,daemon=True,name='trend-pullback-research');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
