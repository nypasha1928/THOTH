"""Reuse causal market replay on testnet prices, with a separate account engine."""
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

    def fetch(self,symbol,interval):
        if not self.supervisor.client:raise ValueError('Testnet is not connected')
        cfg=Settings(**{k:v for k,v in self.supervisor.load()['config'].items() if k in Settings.model_fields})
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
