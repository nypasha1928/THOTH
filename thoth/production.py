"""Production execution shares the tested account engine, behind extra gates.

A live account is never auto-activated here. Deployment must explicitly enable
the server flag, supply separate credentials, have a passing reference receipt
and a completed testnet execution acceptance receipt, and receive the live
confirmation phrase from the authenticated operator.
"""
import json
from pathlib import Path

from .bybit_testnet import TestnetClient
from .credentials import runtime_secret
from .testnet import TestnetSupervisor
from .verification import reference_status, source_fingerprint, strategy_fingerprint, execution_fingerprint


class ProductionClient(TestnetClient):
    BASE='https://api.bybit.com'


class ProductionSupervisor(TestnetSupervisor):
    def __init__(self,path,enabled=False,password_configured=False,client_factory=ProductionClient,credentials=None,clock=None):
        options=dict(enabled=enabled,password_configured=password_configured,client_factory=client_factory,
            credentials=credentials or (lambda:(runtime_secret('THOTH_BYBIT_LIVE_KEY'),runtime_secret('THOTH_BYBIT_LIVE_SECRET'))),
            environment='live')
        if clock is not None:options['clock']=clock
        super().__init__(path,**options)

    def activate(self,confirmation,recovery=False):
        self._recovering=recovery
        try:return super().activate(confirmation,recovery=recovery)
        finally:self._recovering=False

    def _preflight(self):
        if not self.enabled:raise ValueError('LIVE execution is disabled on the server')
        cfg=self.state['config']
        directory=Path(self.path).parent
        recovery=getattr(self,'_recovering',False) and bool(self.state['account_binding'] and self.state['management'])
        if not recovery:
            reference=reference_status(cfg,directory/'validation')
            if not reference['verified']:raise ValueError('Live orders require a passing TradingView reference comparison for these settings')
            try:
                receipt=json.loads((directory/'testnet-acceptance.json').read_text())
                valid=(receipt.get('environment')=='testnet' and receipt.get('source_fingerprint')==source_fingerprint()
                       and receipt.get('strategy_fingerprint')==strategy_fingerprint(cfg) and receipt.get('execution_fingerprint')==execution_fingerprint() and receipt.get('protected_and_closed',0)>=1
                       and receipt.get('unresolved_orders',1)==0 and receipt.get('real_exchange') is True)
            except (OSError,ValueError):valid=False
            if not valid:raise ValueError('Live orders require completed real-exchange testnet acceptance for the current strategy')
        cfg=super()._preflight()
        if not recovery:
            info=self.client.request('GET','/v5/user/query-api',{},private=True)
            ips=info.get('ips')
            if not isinstance(ips,list) or not ips or any(ip in ('*','0.0.0.0/0','::/0','') for ip in ips):
                raise ValueError('Live API key must be bound to the server egress IP')
        return cfg
