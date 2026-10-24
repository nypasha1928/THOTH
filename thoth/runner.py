"""Single-process, opt-in automatic paper loop. Never submits exchange orders."""
from .timeframes import interval_seconds
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .indicator import evaluate
from .indicator_stream import IndicatorState,validate
from .verification import source_fingerprint,strategy_fingerprint
from .market import candles
from .models import Settings
from .intrabar_entry import observe_breakout,STRATEGY
from .daily_sr_rsi import observe as observe_daily,STRATEGY as DAILY_COMBINED


class Runner:
    def __init__(self, engine, fetch=candles, interval=5):
        self.engine, self.fetch, self.interval = engine, fetch, interval
        self.stop_event = threading.Event()
        self.thread = None
        self.health = {'status': 'idle', 'symbols': {}, 'last_cycle': None}
        self.observation_session=uuid.uuid4().hex
        self.scan_cycle=0
        self.previous_scan_seconds=0.
        self.source_fingerprint=source_fingerprint()
        self.history_path = str(Path(engine.path).with_suffix('.candles.sqlite'))
        with sqlite3.connect(self.history_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS intrabar_observations(symbol TEXT, strategy TEXT, payload TEXT, PRIMARY KEY(symbol,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS candles(symbol TEXT, interval TEXT, time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,time))')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints(symbol TEXT, interval TEXT, strategy TEXT, source TEXT, stamp INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS analyses(symbol TEXT, interval TEXT, strategy TEXT, source TEXT, stamp INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,strategy,stamp))')

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

    def analysis(self,symbol,cfg,history,now):
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
            if state.last_time is not None:
                db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?)',(symbol,cfg.timeframe,strategy,self.source_fingerprint,state.last_time,json.dumps(state.checkpoint(),allow_nan=False)))
                db.executemany('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?)',[(symbol,cfg.timeframe,strategy,self.source_fingerprint,b['time'],json.dumps(b,allow_nan=False)) for b in output])
                db.execute('DELETE FROM analyses WHERE symbol=? AND interval=? AND stamp<?',(symbol,cfg.timeframe,state.last_time-duration*1000))
            cached=[json.loads(r[0]) for r in db.execute('SELECT payload FROM analyses WHERE symbol=? AND interval=? AND strategy=? AND source=? ORDER BY stamp',(symbol,cfg.timeframe,strategy,self.source_fingerprint))]
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
            observation=observe_breakout(tracker,history,result[-1]['levels'],cfg,now)
            observation['entry_sources']=['sr:'+key for key in sorted(set(tracker.get('fired',[]))-before) if ('resistance' in key)==(observation['signal']=='Long')] if observation['signal'] else []
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
            db.execute('INSERT OR REPLACE INTO intrabar_observations VALUES(?,?,?)',(symbol,identity,json.dumps(tracker,allow_nan=False)))
        return observation

    def cycle(self, paced=True):
        scan_started=time.monotonic()
        state = self.engine.snapshot()
        cfg = Settings(**state['settings'])
        if not cfg.automatic_paper:
            self.health = {'status': 'disabled', 'symbols': {}, 'last_cycle': time.time()}
            return
        # Paused entries still require position management; no unnecessary network when empty.
        symbols = cfg.symbols if state['status'] == 'running' else [p['symbol'] for p in state['positions']]
        self.scan_cycle+=1
        observations, statuses = {}, {}
        streaming=(cfg.strategy in ('rsi_hourly_intrabar',DAILY_COMBINED) and
                   callable(getattr(self.engine,'begin_market_scan',None)) and
                   callable(getattr(self.engine,'market_observation',None)))
        if streaming:self.engine.begin_market_scan()
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
            return self.fetch(symbol, cfg.timeframe)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for symbol in symbols:
                if self.stop_event.is_set(): return
                futures[pool.submit(fetch_limited, symbol)] = symbol
            downloaded = {}
            for future in as_completed(futures):
                symbol = futures[future]
                try: downloaded[symbol] = future.result()
                except Exception as exc: downloaded[symbol] = exc
        duration = interval_seconds(cfg.timeframe)
        for symbol in symbols:
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
                observations[symbol] = self.daily_sr_rsi_observation(symbol,cfg,history,result,now) if cfg.strategy==DAILY_COMBINED else self.rsi_observation(symbol,cfg,history,now) if cfg.strategy=='rsi_hourly_intrabar' else self.intrabar_observation(symbol,cfg,history,result,now) if cfg.strategy==STRATEGY else dict(time=latest['time'], mark=rows[-1]['close'], signal=latest['signal'],entry_sources=latest.get('entry_sources',[]))
                statuses[symbol] = {'status': 'ok', 'bar': latest['time'], 'signal': observations[symbol]['signal']}
                if streaming:self.engine.market_observation(cfg.model_dump(),symbol,observations[symbol])
            except Exception as exc:
                observations.pop(symbol,None)
                statuses[symbol] = {'status': 'error', 'message': str(exc)[:240]}

        # Missing held-position data blocks new entries. An unavailable unheld symbol
        # does not suppress otherwise valid signals on unrelated symbols.
        held={p['symbol'] for p in state['positions']}
        if held-set(observations) and cfg.strategy not in ('rsi_hourly_intrabar',DAILY_COMBINED):
            for observation in observations.values(): observation['signal'] = None
        if not streaming:self.engine.market_cycle(cfg.model_dump(), observations)
        self.previous_scan_seconds=max(0.,time.monotonic()-scan_started)
        self.health = {'status': 'degraded' if len(observations)!=len(symbols) else 'ok',
                       'symbols': statuses, 'last_cycle': time.time(),'scan_seconds':self.previous_scan_seconds,
                       'note': 'Daily intrabar S/R OR fixed RSI; mandatory 1m SMA exits' if cfg.strategy==DAILY_COMBINED else 'Sampled selected-timeframe RSI entries; 1m SMA exits' if cfg.strategy=='rsi_hourly_intrabar' else 'Sampled selected-timeframe intrabar S/R entries; 1m SMA exits' if cfg.strategy==STRATEGY else 'Confirmed-candle strategy feed'}

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.cycle()
            except Exception as exc:
                self.health = {'status':'error','message':str(exc)[:240], 'last_cycle':time.time()}
            self.stop_event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self.run, name='thoth-paper', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=15)
