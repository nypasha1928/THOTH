"""Reuse causal market replay on testnet prices, with a separate account engine."""
from .models import Settings


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
        return self.supervisor.client.candles(symbol,interval)
