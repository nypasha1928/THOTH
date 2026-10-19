"""Production execution shares the tested account engine, behind extra gates.

A live account is never auto-activated here. Deployment must explicitly enable
the server flag, supply separate credentials, satisfy verification (or explicitly
opt into unverified live trading), and receive the live
confirmation phrase from the authenticated operator.
"""
from pathlib import Path

from .bybit_testnet import TestnetClient
from .credentials import runtime_secret
from .testnet import TestnetSupervisor
from .verification import reference_status


class ProductionClient(TestnetClient):
    BASE='https://api.bybit.com'


class ProductionSupervisor(TestnetSupervisor):
    def __init__(self,path,enabled=False,password_configured=False,client_factory=ProductionClient,credentials=None,clock=None,allow_unverified=False):
        self.allow_unverified=allow_unverified
        options=dict(enabled=enabled,password_configured=password_configured,client_factory=client_factory,
            credentials=credentials or (lambda:(runtime_secret('THOTH_BYBIT_LIVE_KEY'),runtime_secret('THOTH_BYBIT_LIVE_SECRET'))),
            environment='live')
        if clock is not None:options['clock']=clock
        super().__init__(path,**options)

    def snapshot(self):
        return super().snapshot() | {"unverified_live_allowed": self.allow_unverified}

    def activate(self,confirmation,recovery=False):
        self._recovering=recovery
        try:return super().activate(confirmation,recovery=recovery)
        finally:self._recovering=False

    def _preflight(self):
        if not self.enabled:raise ValueError('LIVE execution is disabled on the server')
        cfg=self.state['config']
        directory=Path(self.path).parent
        recovery=getattr(self,'_recovering',False) and bool(self.state['account_binding'] and self.state['management'])
        if not recovery and not self.allow_unverified:
            reference=reference_status(cfg,directory/'validation')
            if not reference['verified']:raise ValueError('Live orders require a passing TradingView reference comparison for these settings')
        cfg=super()._preflight()
        if not recovery and self.allow_unverified:
            self.event("Operator acknowledged live trading with an unverified S/R strategy")
        if not recovery:
            info=self.client.request('GET','/v5/user/query-api',{},private=True)
            ips=info.get('ips')
            if not isinstance(ips,list) or not ips or any(ip in ('*','0.0.0.0/0','::/0','') for ip in ips):
                raise ValueError('Live API key must be bound to the server egress IP')
        return cfg
