"""Single-process, opt-in automatic paper loop. Never submits exchange orders."""
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .indicator import evaluate
from .indicator_stream import IndicatorState,validate
from .verification import source_fingerprint,strategy_fingerprint
from .market import candles
from .models import Settings


class Runner:
    def __init__(self, engine, fetch=candles, interval=5):
        self.engine, self.fetch, self.interval = engine, fetch, interval
        self.stop_event = threading.Event()
        self.thread = None
        self.health = {'status': 'idle', 'symbols': {}, 'last_cycle': None}
        self.source_fingerprint=source_fingerprint()
        self.history_path = str(Path(engine.path).with_suffix('.candles.sqlite'))
        with sqlite3.connect(self.history_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS candles(symbol TEXT, interval TEXT, time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,time))')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints(symbol TEXT, interval TEXT, strategy TEXT, source TEXT, stamp INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,strategy))')
            db.execute('CREATE TABLE IF NOT EXISTS analyses(symbol TEXT, interval TEXT, strategy TEXT, source TEXT, stamp INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,strategy,stamp))')

    def history(self, symbol, interval, rows):
        """Persist history so moving API windows don't reset major/minor state."""
        duration = 60 if interval == '1' else 86400
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
        strategy=strategy_fingerprint(cfg);duration=60 if cfg.timeframe=='1' else 86400
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

    def cycle(self, paced=True):
        state = self.engine.snapshot()
        cfg = Settings(**state['settings'])
        if not cfg.automatic_paper:
            self.health = {'status': 'disabled', 'symbols': {}, 'last_cycle': time.time()}
            return
        # Paused entries still require position management; no unnecessary network when empty.
        symbols = cfg.symbols if state['status'] == 'running' else [p['symbol'] for p in state['positions']]
        observations, statuses = {}, {}
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
        duration = 60 if cfg.timeframe == '1' else 86400
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
                if count < (201 if cfg.strategy=='sma_cross' else max(cfg.sma_length if cfg.sma_enabled else 1, 2*cfg.pivot_long+1)):
                    raise ValueError('Insufficient confirmed candle warmup')
                latest = result[-1]
                observations[symbol] = dict(time=latest['time'], mark=rows[-1]['close'], signal=latest['signal'])
                statuses[symbol] = {'status': 'ok', 'bar': latest['time'], 'signal': latest['signal']}
            except Exception as exc:
                statuses[symbol] = {'status': 'error', 'message': str(exc)[:240]}

        # Missing held-position data blocks new entries. An unavailable unheld symbol
        # does not suppress otherwise valid signals on unrelated symbols.
        held={p['symbol'] for p in state['positions']}
        if held-set(observations):
            for observation in observations.values(): observation['signal'] = None
        self.engine.market_cycle(cfg.model_dump(), observations)
        self.health = {'status': 'degraded' if len(observations)!=len(symbols) else 'ok',
                       'symbols': statuses, 'last_cycle': time.time(),
                       'note': 'Confirmed-candle strategy feed'}

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
