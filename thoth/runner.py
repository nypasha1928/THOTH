"""Single-process, opt-in automatic paper loop. Never submits exchange orders."""
from .timeframes import interval_seconds
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path

from .indicator import evaluate
from .indicator_stream import IndicatorState,validate
from .verification import source_fingerprint,strategy_fingerprint
from .market import candles
from .models import Settings
from .staged_entry import STAGED_STRATEGIES,ORDER_BLOCK_STRATEGY,HOUR_GATE
from .intrabar_entry import observe_breakout,STRATEGY
from .daily_sr_rsi import observe as observe_daily,STRATEGY as DAILY_COMBINED


def bounded_downloads(pool,symbols,fetch,stop_event,heartbeat=None):
    """Keep at most four higher-history packets in flight or awaiting analysis."""
    symbols=iter(symbols);pending={}
    def submit():
        symbol=next(symbols,None)
        if symbol is not None:pending[pool.submit(fetch,symbol)]=symbol
    for _ in range(4):submit()
    try:
        while pending and not stop_event.is_set():
            if heartbeat:heartbeat()
            completed,_=wait(pending,timeout=1 if heartbeat else None,return_when=FIRST_COMPLETED)
            for future in completed:
                symbol=pending.pop(future)
                yield symbol,future
                if stop_event.is_set():return
                submit()
    finally:
        for future in pending:future.cancel()


class Runner:
    def __init__(self, engine, fetch=candles, interval=5, *, confirmation_only=False):
        self.engine, self.fetch, self.interval = engine, fetch, interval
        self.confirmation_only=confirmation_only
        self.confirmation_runner=None
        self.confirmation_last_checked={}
        self.confirmation_symbol_cycles={}
        self.confirmation_epochs={}
        self.confirmation_statuses={}
        self.confirmation_checks=0
        self.stop_event = threading.Event()
        self.thread = None
        self.health = {'status': 'idle', 'symbols': {}, 'last_cycle': None}
        self.observation_session=uuid.uuid4().hex
        self.scan_cycle=0
        self.previous_scan_seconds=0.
        self.source_fingerprint=source_fingerprint()
        self.history_path = str(Path(engine.path).with_suffix('.candles.sqlite'))
        with sqlite3.connect(self.history_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS price_observations(symbol TEXT,strategy TEXT,payload TEXT,PRIMARY KEY(symbol,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS intrabar_observations(symbol TEXT, strategy TEXT, payload TEXT, PRIMARY KEY(symbol,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS order_block_checkpoints(symbol TEXT,strategy TEXT,source TEXT,stamp INTEGER,payload TEXT,PRIMARY KEY(symbol,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS candles(symbol TEXT, interval TEXT, time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,time))')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints(symbol TEXT, interval TEXT, strategy TEXT, source TEXT, stamp INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS analyses(symbol TEXT, interval TEXT, strategy TEXT, source TEXT, stamp INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,strategy,stamp))')

    @property
    def health(self):
        report=dict(self._health)
        if self.confirmation_runner:
            confirmation=self.confirmation_runner.health
            report['confirmation']=confirmation
            report['priority_minute_checks']=self.confirmation_runner.confirmation_checks
            report['priority_check_count_basis']='since worker start'
            if confirmation.get('status') in ('error','degraded') and report.get('status') in ('ok','scanning'):
                report['status']='degraded'
        return report

    @health.setter
    def health(self,value):self._health=value

    def history(self, symbol, interval, rows):
        """Persist history so moving API windows don't reset major/minor state."""
        duration = interval_seconds(interval)
        with sqlite3.connect(self.history_path) as db:
            previous = db.execute('SELECT MAX(time) FROM candles WHERE symbol=? AND interval=?', (symbol,interval)).fetchone()[0]
            if previous is not None and rows[0]['time'] > previous + duration:
                raise ValueError('Market history gap exceeds API window; backfill required')
            checkpoint=db.execute('SELECT MAX(stamp) FROM checkpoints WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()[0]
            if checkpoint is not None:
                existing={r[0]:json.loads(r[1]) for r in db.execute('SELECT time,payload FROM candles WHERE symbol=? AND interval=? AND time>=? AND time<=?',(symbol,interval,rows[0]['time'],checkpoint))}
                if any(r['time'] in existing and existing[r['time']]!=r for r in rows if r['time']<=checkpoint):
                    db.execute('DELETE FROM checkpoints WHERE symbol=? AND interval=?',(symbol,interval))
                    db.execute('DELETE FROM analyses WHERE symbol=? AND interval=?',(symbol,interval))
            db.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?)',
                [(symbol, interval, r['time'], json.dumps(r)) for r in rows])
            db.execute('DELETE FROM candles WHERE symbol=? AND interval=? AND time<?',(symbol,interval,rows[-1]['time']-duration*5000))
            stored = [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM candles WHERE symbol=? AND interval=? ORDER BY time', (symbol,interval))]
        if any(b['time']-a['time'] != duration for a,b in zip(stored,stored[1:])):
            raise ValueError('Candle history contains a gap; no automatic entries')
        return stored

    def analysis(self,symbol,cfg,history,now,*,latest_only=False):
        strategy=strategy_fingerprint(cfg);duration=interval_seconds(cfg.timeframe)
        for row in history:validate(row)
        with sqlite3.connect(self.history_path) as db:
            checkpoint=db.execute('SELECT source,stamp,payload FROM checkpoints WHERE symbol=? AND interval=? AND strategy=?',(symbol,cfg.timeframe,strategy)).fetchone()
            valid=checkpoint and checkpoint[0]==self.source_fingerprint
            state=IndicatorState(cfg,json.loads(checkpoint[2]) if valid else None)
            if valid and history[0]['time']>state.last_time+duration:
                raise ValueError('Indicator checkpoint history gap; backfill required')
            output=[]
            for row in history:
                if row['time']+duration>now:break
                if state.last_time is not None and row['time']<=state.last_time:continue
                output.append(state.step(row))
            if state.last_time is not None and (output or not valid):
                db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?)',(symbol,cfg.timeframe,strategy,self.source_fingerprint,state.last_time,json.dumps(state.checkpoint(),allow_nan=False)))
                db.executemany('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?)',[(symbol,cfg.timeframe,strategy,self.source_fingerprint,b['time'],json.dumps(b,allow_nan=False)) for b in output])
                db.execute('DELETE FROM analyses WHERE symbol=? AND interval=? AND stamp<?',(symbol,cfg.timeframe,state.last_time-duration*1000))
            query='SELECT payload FROM analyses WHERE symbol=? AND interval=? AND strategy=? AND source=? ORDER BY stamp'+(' DESC LIMIT 1' if latest_only else '')
            cached=[json.loads(r[0]) for r in db.execute(query,(symbol,cfg.timeframe,strategy,self.source_fingerprint))]
        return cached,state.count

    def intrabar_observation(self,symbol,cfg,history,result,now):
        duration=interval_seconds(cfg.timeframe)
        if not result or result[-1]['time']!=int(now)//duration*duration-duration:
            raise ValueError('Latest confirmed entry-timeframe levels unavailable')
        identity=strategy_fingerprint(cfg)
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            before=set(tracker.get('fired',[])) if tracker.get('bar')==int(now)//duration*duration else set()
            inverse=cfg.strategy=='sr_inverse_intrabar'
            if inverse:
                continuous=(tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (self.scan_cycle-1,self.scan_cycle))
                budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
                observation=observe_breakout(tracker,history,result[-1]['levels'],cfg,now,max_gap=budget if continuous else 0)
                tracker.update(session=self.observation_session,scan_cycle=self.scan_cycle)
            else:
                observation=observe_breakout(tracker,history,result[-1]['levels'],cfg,now)
            observation['entry_sources']=['sr:'+key for key in sorted(set(tracker.get('fired',[]))-before) if ('resistance' in key)==(observation['signal']=='Long')] if observation['signal'] else []
            if inverse:
                raw=observation['signal']
                observation['source_signal']=raw
                observation['signal']={'Long':'Short','Short':'Long'}.get(raw)
                observation['entry_sources']=[source.replace('sr:','inverse_sr:',1) for source in observation['entry_sources']]
                from .inverse_entry_delay import apply_delay
                allowed=getattr(self.engine,'entry_delay_allowed',lambda symbol:False)(symbol)
                observation=apply_delay(tracker,observation,cfg,result[-1]['levels'],now,continuous=continuous,entries_allowed=allowed,rows=history)
            # Persist consumption before dispatch: a crash must not replay an entry.
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return observation

    def rsi_preview(self,symbol,cfg,history,now):
        strategy=strategy_fingerprint(cfg);duration=interval_seconds(cfg.timeframe)
        with sqlite3.connect(self.history_path) as db:
            checkpoint=db.execute('SELECT source,payload FROM checkpoints WHERE symbol=? AND interval=? AND strategy=?',(symbol,cfg.timeframe,strategy)).fetchone()
        if not checkpoint or checkpoint[0]!=self.source_fingerprint:raise ValueError('RSI warm-up checkpoint unavailable')
        state=IndicatorState(cfg,json.loads(checkpoint[1]))
        start=int(now)//duration*duration
        if not history or history[-1]['time']!=start or state.last_time!=start-duration:raise ValueError('RSI forming candle or contiguous history unavailable')
        return state.step(history[-1])  # Temporary state; never commits an unfinished candle.

    def rsi_observation(self,symbol,cfg,history,now):
        preview=self.rsi_preview(symbol,cfg,history,now)
        if preview['rsi'] is None or (cfg.rsi.calculation_mode=='corrected' and cfg.rsi.ml_signals and not preview['ml_ready']):raise ValueError('RSI/ML history warming up; no entry')
        identity=strategy_fingerprint(cfg);start=preview['time'];duration=interval_seconds(cfg.timeframe)
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            fresh=0<now-tracker.get('observed_at',0)<=30 and tracker.get('bar') in (start,start-duration)
            fired=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
            events=[event for event in preview['events'] if event['kind'] not in fired]
            fired.update(event['kind'] for event in preview['events'])
            sides={event['side'] for event in events}
            signal=next(iter(sides)) if fresh and len(sides)==1 and not preview['conflict'] else None
            tracker.update(bar=start,observed_at=now,fired=sorted(fired))
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return dict(time=now,mark=history[-1]['close'],signal=signal,entry_sources=['rsi:'+event['kind'] for event in events] if signal else [],entry_bar=start,entry_timeframe=cfg.timeframe,exit_timeframe='1')

    def daily_sr_rsi_observation(self,symbol,cfg,history,result,now):
        duration=interval_seconds(cfg.timeframe)
        if not result or result[-1]['time']!=int(now)//duration*duration-duration:
            raise ValueError('Latest confirmed Daily S/R levels unavailable')
        preview=self.rsi_preview(symbol,cfg,history,now)
        identity=strategy_fingerprint(cfg)
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            # Only consecutive scans in this worker session may use the measured
            # scan budget. Restarted/missed observations still establish a baseline.
            continuous=(tracker.get('session')==self.observation_session and
                        tracker.get('scan_cycle') in (self.scan_cycle-1,self.scan_cycle))
            budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
            observation=observe_daily(tracker,history,result[-1]['levels'],preview,cfg,now,
                max_gap=budget,continuous=continuous)
            tracker.update(session=self.observation_session,scan_cycle=self.scan_cycle)
            observation['sampling']=dict(continuous=continuous,gap=tracker['rsi'].get('observation_gap'),limit=budget)
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return observation

    def volume_delta_observation(self,symbol,cfg,rows,now,lower=None):
        from .volume_delta import signal
        duration=interval_seconds(cfg.timeframe);start=int(now)//duration*duration
        if lower is None:lower=self.engine.volume_delta_inputs(symbol,cfg.timeframe,start)
        settings=cfg.volume_delta
        result=signal(rows,lower,cfg.timeframe,now,sma_filter=settings.sma_filter,
            bull_type=settings.bull_type,bear_type=settings.bear_type,
            bullish_enabled=settings.bullish,bearish_enabled=settings.bearish)
        identity=strategy_fingerprint(cfg)
        cycle=self.confirmation_symbol_cycles.get(symbol,0) if self.confirmation_only else self.scan_cycle
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            gap=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
            fresh=(result['ready'] and tracker.get('ready') and tracker.get('session')==self.observation_session and
                tracker.get('scan_cycle') in (cycle-1,cycle) and
                0<now-tracker.get('observed_at',0)<=gap)
            fired=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
            new=result['signal'] and not fired.intersection(result['entry_sources'])
            emitted=result['signal'] if fresh and new else None
            if emitted:fired.update(result['entry_sources'])
            tracker.update(bar=start,observed_at=now,ready=result['ready'],session=self.observation_session,
                scan_cycle=cycle,fired=sorted(fired))
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return dict(time=now,mark=rows[-1]['close'],signal=emitted,entry_sources=result['entry_sources'] if emitted else [],
            volume_delta_status=result['status'])

    def order_block_observation(self,symbol,cfg,rows,now,*,history=None):
        from .order_block import OrderBlockState,select,entry_volume
        duration=interval_seconds(cfg.timeframe);start=int(now)//duration*duration
        minute_gate=cfg.strategy in (HOUR_GATE,'ob_sr_minute_hourly')
        unfiltered=minute_gate or cfg.strategy in ('order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close')
        if not rows or rows[-1]['time']!=start:raise ValueError('Current '+cfg.timeframe+' Order Block candle unavailable')
        history=self.history(symbol,cfg.timeframe,rows) if history is None else history
        volume=({'passed':True,'reason':'no volume filter'} if minute_gate else entry_volume(history,cfg.timeframe)) if unfiltered else None
        identity=strategy_fingerprint(cfg)
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT source,stamp,payload FROM order_block_checkpoints WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            checkpoint=json.loads(saved[2]) if saved and saved[0]==self.source_fingerprint else None
            known={r['time']:r for r in history}
            if checkpoint and any(r['time'] in known and known[r['time']]!=r for r in checkpoint['recent']):checkpoint=None
            state=OrderBlockState(checkpoint['state'] if checkpoint else None,interval=cfg.timeframe,formation_filter=not unfiltered)
            changed=checkpoint is None
            for row in history:
                if row['time']+duration>now:break
                if state.s['last_time'] is not None and row['time']<=state.s['last_time']:continue
                state.step(row);changed=True
            if state.s['last_time']!=start-duration:raise ValueError('Order Block closed history is not current; baseline required')
            if changed:
                recent=[r for r in history if r['time']<=state.s['last_time']][-60:]
                db.execute('INSERT OR REPLACE INTO order_block_checkpoints VALUES(?,?,?,?,?)',
                    (symbol,identity,self.source_fingerprint,state.s['last_time'],json.dumps(dict(state=state.checkpoint(),recent=recent),allow_nan=False)))
            preview=select(OrderBlockState(state.checkpoint(),interval=cfg.timeframe,formation_filter=not unfiltered).step(history[-1]),cfg.order_block,formation_filter=not unfiltered)
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            cycle=self.confirmation_symbol_cycles.get(symbol,0) if self.confirmation_only else self.scan_cycle
            budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
            fresh=(not unfiltered or checkpoint is not None) and preview['ready'] and tracker.get('ready') and tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (cycle-1,cycle) and 0<now-tracker.get('observed_at',0)<=budget
            if unfiltered:
                from .entry_diagnostics import record_speed
                try:record_speed(db,symbol,cfg.strategy,now,history[-1]['close'],self.observation_session,budget)
                except Exception:pass
            previous_observed=tracker.get('observed_at')
            from .entry_diagnostics import observe_sources,qualification
            seen=observe_sources(tracker,['ob:'+event['kind'] for event in preview['events']],now,history[-1]['close'],start)
            fired=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
            new=[event for event in preview['events'] if event['kind'] not in fired and (not unfiltered or volume['passed'])]
            # An event already present at startup/after a data gap is a baseline,
            # not a new trigger. Forming preview never mutates closed zone state.
            # Low-volume events are not queued or consumed during a continuous
            # scan: they can qualify later only while the raw event still holds.
            # Recovery baselines consume existing events regardless of volume.
            if not fresh or not unfiltered or volume['passed']:
                fired.update(event['kind'] for event in preview['events'])
            sides={event['side'] for event in new}
            # In the direct profile, an already-consumed wick break must not
            # suppress a NEW opposite event later in the same forming candle.
            conflict=len(sides)>1 if unfiltered else preview['conflict']
            emitted=next(iter(sides)) if fresh and len(sides)==1 and not conflict else None
            tracker.update(bar=start,observed_at=now,ready=preview['ready'],session=self.observation_session,scan_cycle=cycle,fired=sorted(fired))
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return dict(time=now,mark=history[-1]['close'],signal=emitted,entry_sources=['ob:'+event['kind'] for event in new if event['side']==emitted],
            volume_delta_status='1m Order Block intrabar; NO filters' if minute_gate else (('1h' if cfg.timeframe=='60' else '4h')+' Order Block intrabar; Volume > MA 20 required; '+volume['reason'] if preview['ready'] else 'Order Block ATR/history warming up') if unfiltered else '5m Order Block: formation inverted vs SMA 200; supply break above / demand break below' if preview['ready'] else 'Order Block SMA 200 warming up',
            entry_diagnostics=qualification(['ob:'+event['kind'] for event in new if event['side']==emitted],seen,now,history[-1]['close'],previous_observed) if emitted else None,
            entry_context=dict(order_block_bar=start) if unfiltered and emitted else None,
            volume_checks={cfg.timeframe:volume} if unfiltered and not minute_gate else {},order_block_events=new,order_block_conflict=conflict)

    def sr_only_permission_observation(self,symbol,cfg,history,result,now):
        duration=interval_seconds(cfg.timeframe);start=int(now)//duration*duration
        if not result or result[-1]['time']!=start-duration:raise ValueError('Latest confirmed S/R levels unavailable')
        identity=strategy_fingerprint(cfg)
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            continuous=tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (self.scan_cycle-1,self.scan_cycle)
            previous=tracker.get('observed_at');before=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
            budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
            observation=observe_breakout(tracker,history,result[-1]['levels'],cfg,now,max_gap=budget if continuous else 0)
            crossed=sorted(set(tracker.get('fired',[]))-before)
            tracker.update(session=self.observation_session,scan_cycle=self.scan_cycle)
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        if observation['signal']:
            observation['signal_diagnostic']=dict(entry_bar=start,sr_signal=observation['signal'],rsi_signal=None,sr_levels=crossed,rsi_events=[])
        observation['sampling']=dict(continuous=continuous,gap=now-previous if previous is not None else None,limit=budget)
        return observation

    def sma_intrabar_observation(self,symbol,cfg,rows,now):
        from .sma_intrabar import observe
        history=self.history(symbol,'240',rows)
        identity=strategy_fingerprint(cfg)
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            continuous=tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (self.scan_cycle-1,self.scan_cycle)
            budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
            observation=observe(tracker,history,now,budget if continuous else 0)
            tracker.update(session=self.observation_session,scan_cycle=self.scan_cycle)
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        observation['volume_delta_status']='4h SMA 7/50 intrabar · Reversal · no SMA exit'
        return observation

    def order_block_sr_branch(self,symbol,cfg,rows,now,*,history=None):
        """Independent source-timeframe Major/Minor observations, qualified before entering a queue."""
        from .sr_volume import observe as observe_volume
        history=self.history(symbol,cfg.timeframe,rows) if history is None else history
        duration=interval_seconds(cfg.timeframe);start=int(now)//duration*duration
        source_cfg=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe=cfg.timeframe,major=True,minor=True,
            sma_enabled=False,sma_7_50_filter_enabled=False,sr_volume_period=20))
        result,count=self.analysis(symbol,source_cfg,history,now,latest_only=True)
        if count<2*cfg.pivot_long+1 or not result or result[-1]['time']!=start-duration:
            raise ValueError('Confirmed source-timeframe Major/Minor S/R history unavailable')
        identity=strategy_fingerprint(cfg)+(':sr-bounce' if cfg.strategy==HOUR_GATE else ':sr')
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(saved[0]) if saved else {}
            cycle=self.confirmation_symbol_cycles.get(symbol,0) if self.confirmation_only else self.scan_cycle
            continuous=tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (cycle-1,cycle)
            budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
            if cfg.strategy==HOUR_GATE:
                from .sr_bounce import observe as observe_bounce
                observation=observe_bounce(tracker,history,result[-1]['levels'],source_cfg,now,budget if continuous else 0)
            elif cfg.strategy=='ob_sr_minute_hourly':
                before=set(tracker.get('fired',[])) if tracker.get('bar')==start else set()
                observation=observe_breakout(tracker,history,result[-1]['levels'],source_cfg,now,max_gap=budget if continuous else 0)
                observation['entry_sources']=['sr:'+key for key in sorted(set(tracker['fired'])-before)] if observation.get('signal') else []
                observation['volume_conflict']=len({'Long' if 'resistance' in key else 'Short' for key in set(tracker['fired'])-before})>1
            else:observation=observe_volume(tracker,history,result[-1]['levels'],source_cfg,now,budget if continuous else 0)
            tracker.update(session=self.observation_session,scan_cycle=cycle)
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return observation

    def combined_order_block_sr_observation(self,symbol,cfg,rows,now):
        from .order_block_sr import combine
        history=self.history(symbol,cfg.timeframe,rows)
        if not history or history[-1]['time']!=int(now)//interval_seconds(cfg.timeframe)*interval_seconds(cfg.timeframe):
            raise ValueError('Current '+cfg.timeframe+' candle unavailable')
        errors={};branches={}
        for name,observe in (('order_block',self.order_block_observation),('sr',self.order_block_sr_branch)):
            try:branches[name]=observe(symbol,cfg,rows,now,history=history)
            except ValueError as exc:errors[name]=str(exc)[:200]
        if not branches:raise ValueError('Order Block and S/R histories unavailable')
        result=combine(branches.get('order_block',{}),branches.get('sr',{}),now,history[-1]['close'],errors,timeframe=cfg.timeframe,require_volume=cfg.strategy not in (HOUR_GATE,'ob_sr_minute_hourly'))
        if cfg.strategy=='ob_sr_minute_hourly' and now%3600>=cfg.minute_entry_window_minutes*60:
            held_reader=getattr(self.engine,'minute_hour_position_open',None)
            if callable(held_reader) and not held_reader(symbol):
                result.update(signal=None,entry_sources=[],entry_context=None,signal_diagnostic=None)
                result['volume_delta_status']='Hourly entry window closed; tracking sources without queuing entries'
        return result

    def closed_inverted_order_block_sr_observation(self,symbol,cfg,rows,now):
        from .closed_order_block_sr import evaluate
        history=self.history(symbol,'60',rows)
        closed=[r for r in history if r['time']+3600<=now]
        latest=int(now)//3600*3600-3600
        if not closed or closed[-1]['time']!=latest:raise ValueError('Latest CLOSED 1h candle unavailable')
        identity=strategy_fingerprint(cfg)
        with sqlite3.connect(self.history_path) as db:
            row=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
            tracker=json.loads(row[0]) if row else {}
        # Completed candles are immutable; consecutive scans may exceed the
        # intrabar five-minute cap. Still require the same session/cycle and
        # no more than one hour between observations. Adjacency below prevents
        # missed-hour catch-up; the final entry guard checks latest closed bar.
        budget=3600.
        continuous=(tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (self.scan_cycle-1,self.scan_cycle)
            and 0<now-tracker.get('observed_at',0)<=budget)
        if tracker.get('closed_bar')==latest and continuous:
            result={**tracker['report'],'time':now,'signal':None,'entry_sources':[],'entry_context':None,'signal_diagnostic':None}
            result['volume_delta_status']=result['volume_delta_status'].split('; recovery baseline',1)[0].split('; source ',1)[0]
        else:
            source=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe='60',major=True,minor=True,sma_enabled=False,sma_7_50_filter_enabled=False))
            sr_error=None;sr=None
            try:
                analysis,count=self.analysis(symbol,source,history,now,latest_only=True)
                if count<2*cfg.pivot_long+1 or not analysis or analysis[-1]['time']!=latest:raise ValueError('Confirmed closed Major/Minor S/R history unavailable')
                sr=analysis[-1]
            except ValueError as exc:sr_error=str(exc)
            result=evaluate(closed,cfg,now,sr,sr_error)
            if not continuous or tracker.get('closed_bar')!=latest-3600:
                result.update(signal=None,entry_sources=[],entry_context=None,signal_diagnostic=None)
                result['volume_delta_status']+='; recovery baseline, old candle not replayed'
            elif result['signal']:
                from .entry_diagnostics import qualification
                seen={s:dict(first_seen_at=now,first_seen_price=closed[-1]['close']) for s in result['entry_sources']}
                result['entry_diagnostics']=qualification(result['entry_sources'],seen,now,closed[-1]['close'],tracker.get('observed_at'))
                result['entry_diagnostics']['signal_bar_closed_at']=latest+3600
                result['entry_diagnostics']['price_basis']='closed_signal_candle_close'
        result['sampling']=dict(continuous=bool(continuous),gap=now-tracker['observed_at'] if tracker.get('observed_at') is not None else None,limit=budget,closed_bar=latest)
        tracker.update(closed_bar=latest,observed_at=now,session=self.observation_session,scan_cycle=self.scan_cycle,report=result)
        with sqlite3.connect(self.history_path) as db:
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return result

    def sr_volume_observation(self,symbol,cfg,packet,now):
        from .sr_volume import observe as observe_volume
        candidates=[];errors={};reports={};sources=[];frames=[]
        for timeframe in ('240','D'):
            try:
                rows=packet['primary'][timeframe]
                if isinstance(rows,Exception):raise rows
                source_now=time.time()
                duration=interval_seconds(timeframe);start=int(source_now)//duration*duration
                if not rows or rows[-1]['time']!=start:
                    rows=self.engine.staged_primary_inputs(symbol,timeframe)
                    source_now=time.time()
                source_cfg=cfg.model_copy(update={'timeframe':timeframe})
                history=self.history(symbol,timeframe,rows)
                result,count=self.analysis(symbol,source_cfg,history,source_now,latest_only=True)
                if not result or result[-1]['time']!=int(source_now)//duration*duration-duration:
                    raise ValueError('Latest confirmed S/R levels unavailable')
                identity=strategy_fingerprint(source_cfg)
                with sqlite3.connect(self.history_path) as db:
                    saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
                    tracker=json.loads(saved[0]) if saved else {}
                    continuous=tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (self.scan_cycle-1,self.scan_cycle)
                    budget=min(300.,max(30.,len(cfg.symbols)/8+20,self.previous_scan_seconds*1.5+self.interval+10))
                    observation=observe_volume(tracker,history,result[-1]['levels'],source_cfg,source_now,budget if continuous else 0)
                    tracker.update(session=self.observation_session,scan_cycle=self.scan_cycle)
                    db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
                reports[timeframe]=observation['volume_check']
                if observation['volume_conflict']:reports[timeframe]['conflict']=True
                if observation['signal']:
                    candidates.append(observation['signal']);frames.append(timeframe)
                    sources.extend('htf:'+timeframe+':'+source for source in observation['entry_sources'])
            except Exception as exc:errors[timeframe]=str(exc)[:200]
        sides=set(candidates);signal=next(iter(sides)) if len(sides)==1 else None
        reason='conflicting volume-qualified S/R signals' if len(sides)>1 or any(report.get('conflict') for report in reports.values()) else None
        output=dict(time=time.time(),mark=None,signal=signal,entry_sources=sources if signal else [],source_errors=errors,
            volume_delta_status='Direct 4h/Daily S/R · forming volume must exceed MA '+str(cfg.sr_volume_period),volume_checks=reports,
            entry_context=dict(primary_timeframe=frames[0]) if signal else None)
        if reason:output['signal_diagnostic']=dict(reason=reason,signal=None,volume_checks=reports)
        return output

    def fresh_staged_minute(self,symbol,interval='1'):
        """Bounded retry only when Bybit has not published the newly opened minute."""
        for attempt in range(3):
            rows=self.engine.staged_minute_inputs(symbol) if interval=='1' else self.engine.staged_minute_inputs(symbol,interval=interval)
            duration=interval_seconds(interval)
            now=time.time();expected=int(now)//duration*duration
            actual=rows[-1]['time'] if rows else None
            if actual==expected:return rows,now
            # Never accept an old/future candle, or retry a multi-minute outage.
            rollover=actual==expected-duration and now-expected<=5
            if not rollover or attempt==2:
                raise ValueError(('Current 1m confirmation candle unavailable' if interval=='1' else 'Current 5m Order Block candle unavailable')+': expected '+str(expected)+', received '+str(actual))
            if self.stop_event.wait((.25,.75)[attempt]):raise RuntimeError('Worker stopping')

    def closed_minute_exit_observation(self,symbol,cfg,rows,now):
        from .order_block import OrderBlockState,select
        from .order_block_sr import combine
        history=self.history(symbol,'1',rows)
        closed=[row for row in history if row['time']+60<=now]
        latest=int(now)//60*60-60
        if not closed or closed[-1]['time']!=latest:raise ValueError('Latest CLOSED 1m exit candle unavailable')
        identity=strategy_fingerprint(cfg)+':closed-minute-exit'
        with sqlite3.connect(self.history_path) as db:
            saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
        tracker=json.loads(saved[0]) if saved else {}
        cycle=self.confirmation_symbol_cycles.get(symbol,0)
        gap=now-tracker.get('observed_at',0)
        continuous=(tracker.get('session')==self.observation_session and tracker.get('scan_cycle') in (cycle-1,cycle) and 0<gap<=300)
        signal=None;sources=[];errors=tracker.get('errors',{})
        if tracker.get('closed_bar')!=latest or not continuous:
            errors={}
            known={row['time']:row for row in closed}
            valid=tracker.get('source')==self.source_fingerprint and all(known.get(row['time'],row)==row for row in tracker.get('recent',[]))
            state=OrderBlockState(tracker.get('state') if valid else None,interval='1',formation_filter=False)
            ob=tracker.get('ob')
            for row in closed:
                if state.s['last_time'] is not None and row['time']<=state.s['last_time']:continue
                ob=select(state.step(row),cfg.order_block,formation_filter=False)
            if state.s['last_time']!=latest or not ob or not ob['ready']:
                ob={};errors['closed_exit_order_block']='Closed Order Block exit history warming up'
            sr={}
            try:
                source=cfg.model_copy(update=dict(strategy='sr_breakout',timeframe='1',major=True,minor=True,sma_enabled=False,sma_7_50_filter_enabled=False))
                analysis,count=self.analysis(symbol,source,history,now,latest_only=True)
                if count<2*cfg.pivot_long+1 or not analysis or analysis[-1]['time']!=latest:
                    raise ValueError('Closed Major/Minor S/R exit history unavailable')
                from .sr_bounce import evaluate as bounce_rejection
                if len(closed)<2 or closed[-2]['time']!=latest-60:raise ValueError('Previous closed minute unavailable for close[1]')
                sr=bounce_rejection(closed[-1],closed[-2]['close'],analysis[-1].get('levels',{}),source)
            except ValueError as exc:errors['closed_exit_sr']=str(exc)
            if not ob and not sr:raise ValueError('Closed 1m Order Block and S/R exit histories unavailable')
            report=combine(ob,sr,now,closed[-1]['close'],errors,timeframe='1',require_volume=False)
            if continuous and valid and tracker.get('closed_bar')==latest-60:
                signal=report.get('signal');sources=['htf:1:'+s for s in report.get('entry_sources',[])]
            tracker.update(state=state.checkpoint(),ob=ob,source=self.source_fingerprint,recent=closed[-60:],errors=errors)
        tracker.update(closed_bar=latest,observed_at=now,session=self.observation_session,scan_cycle=cycle)
        with sqlite3.connect(self.history_path) as db:
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return dict(exit_signal=signal,exit_sources=sources,closed_exit_bar=latest,closed_exit_errors=errors)

    def hour_gate_observation(self,symbol,cfg,packet,now):
        if packet.get('minute_on_demand'):
            epoch_reader=getattr(self.engine,'staged_permission_epoch',None)
            if callable(epoch_reader):
                epoch=epoch_reader(symbol)
                if symbol in self.confirmation_epochs and self.confirmation_epochs[symbol]!=epoch:
                    # A new permission must baseline both minute sources again,
                    # even when the previous check was recent in this process.
                    self.confirmation_symbol_cycles[symbol]=self.confirmation_symbol_cycles.get(symbol,0)+2
                self.confirmation_epochs[symbol]=epoch
            requested_at=time.time()
            rows,now=self.fresh_staged_minute(symbol,interval='1')
            result=self.combined_order_block_sr_observation(symbol,cfg,rows,now)
            exit_report=self.closed_minute_exit_observation(symbol,cfg,rows,now)
            result.update(exit_report)
            result['source_errors']={**result.get('source_errors',{}),**exit_report.get('closed_exit_errors',{})}
            if result.get('signal'):
                result['entry_diagnostics']={**(result.get('entry_diagnostics') or {}),
                    'sample_requested_at':requested_at,'sample_received_at':now,'processed_at':time.time()}
            return {**result,'signal':None,'primary_events':[],
                'volume_signal':result.get('signal'),'volume_sources':['htf:1:'+s for s in result.get('entry_sources',[])]}
        rows=packet.get('primary',{}).get('60')
        if isinstance(rows,Exception):raise rows
        if rows is None:return dict(time=now,mark=None,signal=None,primary_events=[],volume_signal=None,
            volume_sources=[],source_errors={},volume_delta_status='Waiting for fresh CLOSED 1h permission')
        source=cfg.model_copy(update={'timeframe':'60'})
        result=self.closed_inverted_order_block_sr_observation(symbol,source,rows,now)
        primary=[]
        if result.get('signal'):
            bar=result['closed_signal_bar']
            primary=[dict(id='closed-hour:'+str(bar),signal=result['signal'],time=now,timeframe='60',
                sources=['htf:60:'+s for s in result['entry_sources']],window_start=bar+3600,closed_bar=bar)]
        return {**result,'signal':None,'primary_events':primary,'volume_signal':None,'volume_sources':[],
            'volume_delta_status':'CLOSED 1h Order Block / S/R permission; then fresh 1m entry; NO filters'}

    def staged_observation(self,symbol,cfg,packet,now):
        if cfg.strategy==HOUR_GATE:return self.hour_gate_observation(symbol,cfg,packet,now)
        from .signal_sources import sources_from_decision
        primary=[];errors={}
        for timeframe,rows in packet['primary'].items():
            try:
                if isinstance(rows,Exception):raise rows
                source_now=now
                if packet.get('refresh_primary'):
                    source_now=time.time()
                    expected=int(source_now)//interval_seconds(timeframe)*interval_seconds(timeframe)
                    if not rows or rows[-1]['time']!=expected:
                        rows=self.engine.staged_primary_inputs(symbol,timeframe)
                        source_now=time.time()
                        expected=int(source_now)//interval_seconds(timeframe)*interval_seconds(timeframe)
                        if not rows or rows[-1]['time']!=expected:raise ValueError('Current higher candle unavailable: expected '+str(expected)+', received '+str(rows[-1]['time'] if rows else None))
                source_cfg=cfg.model_copy(update={'strategy':'sr_hourly_intrabar' if cfg.strategy==ORDER_BLOCK_STRATEGY else DAILY_COMBINED,'timeframe':timeframe})
                identity=strategy_fingerprint(source_cfg)
                with sqlite3.connect(self.history_path) as db:
                    saved=db.execute('SELECT payload FROM intrabar_observations WHERE symbol=? AND strategy=?',(symbol,identity)).fetchone()
                previous=json.loads(saved[0]) if saved else {}
                window_start=previous.get('observed_at',0) if cfg.strategy==ORDER_BLOCK_STRATEGY else min(previous.get(part,{}).get('observed_at',0) for part in ('sr','rsi'))
                history=self.history(symbol,timeframe,rows)
                result,count=self.analysis(symbol,source_cfg,history,source_now,latest_only=True)
                observation=self.sr_only_permission_observation(symbol,source_cfg,history,result,source_now) if cfg.strategy==ORDER_BLOCK_STRATEGY else self.daily_sr_rsi_observation(symbol,source_cfg,history,result,source_now)
                # Either family supplies permission, including simultaneous opposing
                # families: direction is irrelevant at this first stage.
                sampling=observation.get('sampling',{})
                if sampling.get('continuous') and sampling.get('gap') is not None and sampling['gap']>sampling['limit']:
                    errors[timeframe]='Higher observation gap '+str(round(sampling['gap'],2))+'s exceeds '+str(sampling['limit'])+'s freshness limit; baseline refreshed'
                diagnostic=observation.get('signal_diagnostic') or {}
                for family in (('sr',) if cfg.strategy==ORDER_BLOCK_STRATEGY else ('sr','rsi')):
                    direction=diagnostic.get(family+'_signal')
                    if direction not in ('Long','Short'):continue
                    item={'signal':direction,'diagnostic':{**diagnostic,('rsi_signal' if family=='sr' else 'sr_signal'):None}}
                    sources=['htf:'+timeframe+':'+source for source in sources_from_decision(item)]
                    primary.append(dict(id=identity+':'+str(diagnostic['entry_bar'])+':'+family+':'+','.join(sources),
                        signal=direction,time=source_now,timeframe=timeframe,sources=sources,window_start=window_start))
            except Exception as exc:errors[timeframe]=str(exc)[:200]
        if packet.get('refresh_primary'):now=time.time()
        volume={}
        try:
            if packet.get('minute_not_needed'):
                return dict(time=now,mark=None,signal=None,primary_events=primary,volume_signal=None,volume_sources=[],source_errors=errors,
                    volume_delta_status='No minute request: waiting for higher-timeframe permission or cooldown')
            if packet.get('minute_on_demand'):
                minute,now=self.fresh_staged_minute(symbol,interval=cfg.timeframe)
            else:
                if packet.get('minute_error'):raise packet['minute_error']
                minute=packet['minute']
            volume=self.order_block_observation(symbol,cfg,minute,now) if cfg.strategy==ORDER_BLOCK_STRATEGY else self.volume_delta_observation(symbol,cfg,minute,now,lower=[])
        except Exception as exc:errors['1']=str(exc)[:200]
        return dict(time=now,mark=volume.get('mark'),signal=None,primary_events=primary,
            volume_signal=volume.get('signal'),volume_sources=volume.get('entry_sources',[]),source_errors=errors,
            volume_delta_status=volume.get('volume_delta_status','1m data unavailable'))

    def confirmation_cycle(self):
        started=time.monotonic()
        state=self.engine.snapshot();cfg=Settings(**state['settings'])
        if not cfg.automatic_paper or cfg.strategy not in STAGED_STRATEGIES:
            self.health=dict(status='disabled',symbols={},last_cycle=time.time())
            return
        self.scan_cycle+=1
        armed=self.engine.staged_armed_symbols()
        self.confirmation_statuses={s:v for s,v in self.confirmation_statuses.items() if s in armed}
        due=[s for s in armed if started-self.confirmation_last_checked.get(s,float('-inf'))>=self.interval]
        selected=sorted(due,key=lambda s:self.confirmation_last_checked.get(s,float('-inf')))[:4]
        for symbol in selected:
            if self.stop_event.is_set():return
            self.confirmation_symbol_cycles[symbol]=self.confirmation_symbol_cycles.get(symbol,0)+1
            try:
                obs=self.staged_observation(symbol,cfg,dict(primary={},minute_on_demand=True),time.time())
                self.engine.market_observation(cfg.model_dump(),symbol,obs)
                self.confirmation_checks+=1
                self.confirmation_statuses[symbol]=dict(status='degraded' if obs.get('source_errors') else 'ok',
                    observed_at=obs['time'],source_errors=obs.get('source_errors',{}),note=obs['volume_delta_status'])
            except Exception as exc:
                self.confirmation_statuses[symbol]=dict(status='error',observed_at=time.time(),message=str(exc)[:240])
            finally:self.confirmation_last_checked[symbol]=time.monotonic()
        duration=max(0.,time.monotonic()-started)
        self.previous_scan_seconds=max(duration,self.interval)*max(1,(len(armed)+3)//4)
        ages={s:time.monotonic()-self.confirmation_last_checked[s] for s in armed if s in self.confirmation_last_checked}
        self.health=dict(status='degraded' if any(v['status']!='ok' for v in self.confirmation_statuses.values()) or any(age>300 for age in ages.values()) else 'ok' if armed else 'waiting_for_permission',
            symbols=dict(self.confirmation_statuses),last_cycle=time.time(),scan_seconds=duration,estimated_round_seconds=self.previous_scan_seconds,
            timeframe=cfg.timeframe,armed_coins=len(armed),checked_this_cycle=len(selected),total_checks=self.confirmation_checks,
            max_check_age_seconds=max(ages.values(),default=0),stale_symbols=[s for s,age in ages.items() if age>300],
            pending_first_check=sum(s not in self.confirmation_last_checked for s in armed))

    def cycle(self, paced=True):
        if self.confirmation_only:return self.confirmation_cycle()
        scan_started=time.monotonic()
        state = self.engine.snapshot()
        cfg = Settings(**state['settings'])
        if not cfg.automatic_paper:
            self.health = {'status': 'disabled', 'symbols': {}, 'last_cycle': time.time()}
            return
        # Paused entries still require position management; no unnecessary network when empty.
        symbols = cfg.symbols if state['status'] == 'running' else [p['symbol'] for p in state['positions']]
        from .tradfi import EXCLUDED_SYMBOLS
        symbols=[s for s in symbols if s not in EXCLUDED_SYMBOLS]
        self.scan_cycle+=1
        observations, statuses = {}, {}
        sample_timings={}
        streaming=(cfg.strategy in ('rsi_hourly_intrabar',DAILY_COMBINED,'sr_inverse_intrabar','volume_delta_intrabar','sr_volume_intrabar','sma_7_50_intrabar','order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close',*STAGED_STRATEGIES) and
                   callable(getattr(self.engine,'begin_market_scan',None)) and
                   callable(getattr(self.engine,'market_observation',None)))
        if streaming:self.engine.begin_market_scan()
        def record(symbol,observation):
            if observation.get('signal') and observation.get('entry_diagnostics'):
                details={**observation['entry_diagnostics'],**sample_timings.get(symbol,{}),'processed_at':time.time(),'previous_scan_seconds':self.previous_scan_seconds}
                observation['entry_context']={**(observation.get('entry_context') or {}),'entry_diagnostics':details}
            observations[symbol]=observation
            statuses[symbol]=dict(status='degraded' if observation.get('source_errors') else 'ok',bar=int(observation['time'])//60*60,
                signal=observation['signal'],note=observation['volume_delta_status'],source_errors=observation.get('source_errors',{}),volume_checks=observation.get('volume_checks',{}))
            if cfg.strategy in (HOUR_GATE,'order_block_sr_1h_close','order_block_sr_1h_inverted_close'):
                statuses[symbol]['sampling']=observation.get('sampling',{})
            if streaming:self.engine.market_observation(cfg.model_dump(),symbol,observation)
        if cfg.strategy in STAGED_STRATEGIES:
            self.health={**self._health,'status':'scanning','scan_started':time.time()}
        futures = {}
        # Four in-flight fetches, globally spaced starts: maximum eight requests/sec.
        # Download concurrently; indicator state and SQLite remain on this single thread.
        request_lock = threading.Lock()
        next_request = [time.monotonic()]
        def fetch_limited(symbol):
            if paced:
                with request_lock:
                    slot = max(time.monotonic(), next_request[0])
                    next_request[0] = slot + 0.125
                if self.stop_event.wait(max(0, slot-time.monotonic())):
                    raise RuntimeError('Worker stopping')
            started=time.time()
            try:return self.fetch(symbol, cfg.timeframe)
            finally:sample_timings[symbol]=dict(sample_requested_at=started,sample_received_at=time.time())
        with ThreadPoolExecutor(max_workers=4) as pool:
            if cfg.strategy in (*STAGED_STRATEGIES,'sr_volume_intrabar','sma_7_50_intrabar','order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close'):
                completed=bounded_downloads(pool,symbols,fetch_limited,self.stop_event)
            else:
                for symbol in symbols:
                    if self.stop_event.is_set(): return
                    futures[pool.submit(fetch_limited, symbol)] = symbol
                completed=((futures[future],future) for future in as_completed(futures))
            downloaded = {}
            for symbol,future in completed:
                if self.stop_event.is_set():return
                try:
                    rows=future.result()
                    if cfg.strategy in STAGED_STRATEGIES and rows.get('minute_on_demand'):
                        # Confirmation worker owns this tracker/cursor. Never send
                        # empty primary observations that could race a minute signal.
                        observations[symbol]=dict(signal=None)
                        statuses[symbol]=dict(status='ok',signal=None,note='Armed; checked by independent minute worker')
                        continue
                    if cfg.strategy in ('volume_delta_intrabar','sr_volume_intrabar','sma_7_50_intrabar','order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close',*STAGED_STRATEGIES):
                        # Delta needs current 1m inputs: process ready symbols now,
                        # rather than age their samples behind the whole download.
                        now=time.time()
                        observation=self.closed_inverted_order_block_sr_observation(symbol,cfg,rows,now) if cfg.strategy in ('order_block_sr_1h_inverted_close','order_block_sr_1h_close') else self.combined_order_block_sr_observation(symbol,cfg,rows,now) if cfg.strategy in ('ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close') else self.order_block_observation(symbol,cfg,rows,now) if cfg.strategy=='order_block_intrabar' else self.sma_intrabar_observation(symbol,cfg,rows,now) if cfg.strategy=='sma_7_50_intrabar' else self.sr_volume_observation(symbol,cfg,rows,now) if cfg.strategy=='sr_volume_intrabar' else self.staged_observation(symbol,cfg,rows,now) if cfg.strategy in STAGED_STRATEGIES else self.volume_delta_observation(symbol,cfg,rows,now)
                        record(symbol,observation)
                    else:downloaded[symbol]=rows
                except Exception as exc:
                    if cfg.strategy in ('volume_delta_intrabar','sr_volume_intrabar','sma_7_50_intrabar','order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close',*STAGED_STRATEGIES):
                        observations.pop(symbol,None)
                        statuses[symbol]=dict(status='error',message=str(exc)[:240])
                    else:downloaded[symbol]=exc
        duration = interval_seconds(cfg.timeframe)
        for symbol in (() if cfg.strategy in ('volume_delta_intrabar','sr_volume_intrabar','sma_7_50_intrabar','order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close',*STAGED_STRATEGIES) else symbols):
            if self.stop_event.is_set(): return
            try:
                rows = downloaded[symbol]
                if isinstance(rows, Exception): raise rows
                now = time.time()
                if not rows or not (rows[-1]['time'] <= now < rows[-1]['time']+duration+10):
                    raise ValueError('Stale or future market data')
                history = self.history(symbol, cfg.timeframe, rows)
                result,count = self.analysis(symbol,cfg,history,now)
                if count < (2 if cfg.strategy==DAILY_COMBINED else cfg.rsi.rsi_length+1 if cfg.strategy=='rsi_hourly_intrabar' else 201 if cfg.strategy=='sma_cross' else 51 if cfg.strategy in ('sma_7_50','sr_sma_7_50') else max(cfg.sma_length if cfg.sma_enabled else 1,50 if cfg.sma_7_50_filter_enabled else 1, 2*cfg.pivot_long+1)):
                    raise ValueError('Insufficient confirmed candle warmup')
                latest = result[-1]
                observations[symbol] = self.daily_sr_rsi_observation(symbol,cfg,history,result,now) if cfg.strategy==DAILY_COMBINED else self.rsi_observation(symbol,cfg,history,now) if cfg.strategy=='rsi_hourly_intrabar' else self.intrabar_observation(symbol,cfg,history,result,now) if cfg.strategy in (STRATEGY,'sr_inverse_intrabar') else dict(time=latest['time'], mark=rows[-1]['close'], signal=latest['signal'],entry_sources=latest.get('entry_sources',[]))
                statuses[symbol] = {'status': 'ok', 'bar': latest['time'], 'signal': observations[symbol]['signal']}
                if streaming:self.engine.market_observation(cfg.model_dump(),symbol,observations[symbol])
            except Exception as exc:
                observations.pop(symbol,None)
                statuses[symbol] = {'status': 'error', 'message': str(exc)[:240]}

        # Missing held-position data blocks new entries. An unavailable unheld symbol
        # does not suppress otherwise valid signals on unrelated symbols.
        held={p['symbol'] for p in state['positions']}
        if held-set(observations) and cfg.strategy not in ('rsi_hourly_intrabar',DAILY_COMBINED,'sr_inverse_intrabar','volume_delta_intrabar','sr_volume_intrabar','sma_7_50_intrabar','order_block_intrabar','ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close',*STAGED_STRATEGIES):
            for observation in observations.values(): observation['signal'] = None
        if not streaming:self.engine.market_cycle(cfg.model_dump(), observations)
        self.previous_scan_seconds=max(0.,time.monotonic()-scan_started)
        self.health = {'status': 'degraded' if len(observations)!=len(symbols) or any(s['status']!='ok' for s in statuses.values()) else 'ok',
                       'symbols': statuses, 'last_cycle': time.time(),'scan_seconds':self.previous_scan_seconds,
                       'note': 'Intrabar 1m Order Block / S/R; one entry per coin per UTC hour; hourly total capped by Maximum Positions; entry window ' +str(cfg.minute_entry_window_minutes)+' minutes; opposite 1m closes only; NO filters' if cfg.strategy=='ob_sr_minute_hourly' else 'Single-use CLOSED 1h permission -> intrabar 1m entry / CLOSED 1m opposite exit; minute S/R Bounce/Rejection ONLY; OB unchanged; NO filters' if cfg.strategy==HOUR_GATE else '1h CLOSED Order Block OR S/R; '+('INVERTED' if cfg.strategy=='order_block_sr_1h_inverted_close' else 'NORMAL')+' entries; NO volume filter' if cfg.strategy in ('order_block_sr_1h_inverted_close','order_block_sr_1h_close') else ('1h' if cfg.timeframe=='60' else '4h')+' Order Block OR Major/Minor S/R; both require Volume > MA 20; intrabar' if cfg.strategy in ('ob_sr_minute_hourly','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close') else '4h Order Block intrabar; all four signals require Volume > MA 20; no SMA exit' if cfg.strategy=='order_block_intrabar' else '4h SMA 7/50 intrabar reversal; no SMA exit' if cfg.strategy=='sma_7_50_intrabar' else 'Direct 4h/Daily S/R intrabar entries; forming volume > MA '+str(cfg.sr_volume_period)+'; no SMA exit' if cfg.strategy=='sr_volume_intrabar' else '4h/Daily S/R ONLY permission; 5m Order Block chooses either direction; SMA 200: formation inverted, supply break above / demand break below; no SMA exit' if cfg.strategy==ORDER_BLOCK_STRATEGY else '4h/Daily S/R or RSI permission; 1m Volume Delta chooses either direction; mandatory SMA 7/50 filter; no SMA exit' if cfg.strategy in STAGED_STRATEGIES else 'Volume Delta LTF proxy; forming candle, SMA 7/50 entry filter; no SMA exit' if cfg.strategy=='volume_delta_intrabar' else 'Inverse S/R intrabar: Long signal opens Short; Short signal opens Long; inverted 1m exits: Short below / Long above' if cfg.strategy=='sr_inverse_intrabar' else 'Daily intrabar S/R OR fixed RSI; mandatory 1m SMA exits' if cfg.strategy==DAILY_COMBINED else 'Sampled selected-timeframe RSI entries; 1m SMA exits' if cfg.strategy=='rsi_hourly_intrabar' else 'Sampled selected-timeframe intrabar S/R entries; 1m SMA exits' if cfg.strategy==STRATEGY else 'Confirmed-candle strategy feed'}

    def run(self):
        while not self.stop_event.is_set():
            started=time.monotonic()
            try:
                self.cycle()
            except Exception as exc:
                self.health = {'status':'error','message':str(exc)[:240], 'last_cycle':time.time()}
            self.stop_event.wait(max(0,self.interval-(time.monotonic()-started)) if self.confirmation_only else self.interval)

    def start(self):
        if not self.confirmation_only:
            self.confirmation_runner=Runner(self.engine,fetch=self.fetch,interval=self.interval,confirmation_only=True)
            self.confirmation_runner.start()
        self.thread = threading.Thread(target=self.run, name='thoth-confirmation' if self.confirmation_only else 'thoth-market', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.confirmation_runner:self.confirmation_runner.stop()
        if self.thread: self.thread.join(timeout=15)
