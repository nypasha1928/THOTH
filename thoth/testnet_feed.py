"""Reuse causal market replay on testnet prices, with a separate account engine."""
from .models import Settings
import sqlite3
from pathlib import Path


class FeedAdapter:
    def __init__(self,supervisor):
        self.supervisor=supervisor
        self.path=supervisor.path

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

    def fetch(self,symbol,interval):
        if not self.supervisor.client:raise ValueError('Testnet is not connected')
        rows=self.supervisor.client.candles(symbol,interval)
        cfg=Settings(**{k:v for k,v in self.supervisor.load()['config'].items() if k in Settings.model_fields})
        if cfg.strategy!='rsi_hourly_intrabar' or not cfg.rsi.ml_signals:return rows
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
