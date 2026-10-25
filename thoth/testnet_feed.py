"""Reuse causal market replay on testnet prices, with a separate account engine."""
from .staged_entry import STAGED_STRATEGIES,ORDER_BLOCK_STRATEGY
from .models import Settings,TestnetSettings
import sqlite3
from pathlib import Path


class FeedAdapter:
    def __init__(self,supervisor):
        self.supervisor=supervisor
        self.path=supervisor.path
        self.volume_delta_data={}

    def snapshot(self):
        state=self.supervisor.load()
        cfg=Settings(**{k:v for k,v in state['config'].items() if k in Settings.model_fields})
        cfg.automatic_paper=bool(state['management'] and self.supervisor.client)
        # The feed continues observing selected symbols during pauses to avoid
        # replaying signals that happened while entries were blocked.
        return dict(settings=cfg.model_dump(),status='running' if cfg.automatic_paper else 'paused',positions=[{'symbol':s} for s in state['owned']])

    def market_cycle(self,cfg,observations):
        cfg={**cfg,'automatic_paper':False}
        self.supervisor.apply_signals(cfg,observations)

    def volume_delta_inputs(self,symbol,interval,bar):
        data=self.volume_delta_data.get(symbol)
        if not data or data['interval']!=interval or data['bar']!=bar:raise ValueError('Volume Delta lower-timeframe sample unavailable')
        return data['rows']

    def entry_delay_allowed(self,symbol):
        state=self.supervisor.load()
        from .account_risk import symbol_paused
        cfg=TestnetSettings(**state['config'])
        return bool(state['entries'] and state['status']=='running' and not state.get('flatten_reason')
            and symbol not in state.get('blocked_symbols',{})
            and not symbol_paused(state['risk'],symbol,cfg,self.supervisor.clock()))

    def begin_market_scan(self):
        # The supervisor's independent management loop continues monitoring risk
        # during the scan. Do not repeat account polling for every watched coin.
        with self.supervisor.lock:
            try:self.supervisor.monitor()
            except Exception as exc:self.supervisor._monitor_failure(exc)

    def market_observation(self,cfg,symbol,observation):
        self.supervisor.apply_signals({**cfg,'automatic_paper':False},
            {symbol:observation},monitor_account=False)

    def staged_armed_symbols(self):
        state=self.supervisor.load()
        if state['config']['strategy'] not in STAGED_STRATEGIES or not state['management']:return []
        return [symbol for symbol in state['config']['symbols']
            if state.get('entry_gates',{}).get(symbol,{}).get('phase')=='armed']

    def staged_minute_inputs(self,symbol,interval='1'):
        """Read only when the runner is ready to evaluate this coin, not in prefetch."""
        if not self.supervisor.client:raise ValueError('Account is not connected')
        limit=60
        cfg=Settings(**{k:v for k,v in self.supervisor.load()['config'].items() if k in Settings.model_fields})
        if cfg.strategy==ORDER_BLOCK_STRATEGY:
            interval='5';limit=1000
            path=Path(self.path).with_suffix('.candles.sqlite')
            if path.exists():
                from .verification import strategy_fingerprint
                try:
                    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
                        row=db.execute('SELECT stamp FROM order_block_checkpoints WHERE symbol=? AND strategy=?',(symbol,strategy_fingerprint(cfg))).fetchone()
                        count=db.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND interval='5'",(symbol,)).fetchone()[0]
                    if row and count>=200:limit=min(1000,max(60,int((self.supervisor.clock()-row[0])//300)+3))
                except sqlite3.OperationalError:pass
        return self.supervisor.client.candles(symbol,interval,limit=limit)

    def staged_history_limit(self,symbol,interval):
        """Bootstrap once, then refresh overlap plus any elapsed higher candles."""
        from .timeframes import interval_seconds
        path=Path(self.path).with_suffix('.candles.sqlite')
        if not path.exists():return 1000
        try:
            with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
                count,last=db.execute('SELECT COUNT(*),MAX(time) FROM candles WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
            if count<50 or last is None or last>self.supervisor.clock():return 1000
            return min(1000,max(3,int((self.supervisor.clock()-last)//interval_seconds(interval))+3))
        except sqlite3.OperationalError:return 1000

    def staged_primary_inputs(self,symbol,timeframe):
        if not self.supervisor.client:raise ValueError('Account is not connected')
        return self.supervisor.client.candles(symbol,timeframe,limit=self.staged_history_limit(symbol,timeframe))

    def fetch(self,symbol,interval):
        if not self.supervisor.client:raise ValueError('Testnet is not connected')
        cfg=Settings(**{k:v for k,v in self.supervisor.load()['config'].items() if k in Settings.model_fields})
        if cfg.strategy in STAGED_STRATEGIES:
            gate=self.supervisor.load().get('entry_gates',{}).get(symbol,{})
            armed=gate.get('phase')=='armed'
            packet=dict(primary={},minute_on_demand=armed,minute_not_needed=not armed,refresh_primary=True)
            needs_primary=gate.get('phase')!='armed' and self.supervisor.clock()>=(gate.get('cooldown_until') or 0)
            if needs_primary:
                for timeframe in ('240','D'):
                    try:packet['primary'][timeframe]=self.staged_primary_inputs(symbol,timeframe)
                    except Exception as exc:packet['primary'][timeframe]=exc
            return packet
        rows=self.supervisor.client.candles(symbol,interval,limit=60) if cfg.strategy=='volume_delta_intrabar' else self.supervisor.client.candles(symbol,interval)
        if cfg.strategy=='volume_delta_intrabar':
            from .volume_delta import lower_inputs
            lower=lower_inputs(self.supervisor.client.candles,symbol,interval,rows,self.supervisor.clock(),self.volume_delta_data.get(symbol,{}).get('rows',[]))
            self.volume_delta_data[symbol]=dict(interval=interval,bar=rows[-1]['time'],rows=lower)
            return rows
        if cfg.strategy!='rsi_hourly_intrabar' or not cfg.rsi.ml_signals or cfg.rsi.calculation_mode!='corrected':return rows
        required=cfg.rsi.max_data+cfg.rsi.rsi_length+6*cfg.rsi.smooth_period+2
        path=str(Path(self.path).with_suffix('.candles.sqlite'))
        with sqlite3.connect(path) as db:
            stored=[r[0] for r in db.execute('SELECT time FROM candles WHERE symbol=? AND interval=?',(symbol,interval))]
        known=set(stored)|{row['time'] for row in rows}
        if len(known)>=required:return rows
        merged={row['time']:row for row in rows}
        # Initial bootstrap only; later cycles reuse the durable history/checkpoint.
        for _ in range(4):
            if len(known)>=required or not merged:break
            oldest=min(known)
            older=self.supervisor.client.candles(symbol,interval,limit=1000,end=oldest*1000-1)
            new=[row for row in older if row['time']<oldest]
            if not new:break
            merged.update((row['time'],row) for row in new);known.update(row['time'] for row in new)
        return sorted(merged.values(),key=lambda row:row['time'])
