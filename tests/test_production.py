import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from thoth.production import ProductionSupervisor,ProductionClient
from thoth.execution import ExecutionJournal


class ProductionGateTests(unittest.TestCase):
    def test_default_live_gate_does_not_read_credentials_or_make_requests(self):
        with tempfile.TemporaryDirectory() as d:
            factory=Mock();credentials=Mock()
            service=ProductionSupervisor(Path(d)/'live.sqlite',client_factory=factory,credentials=credentials)
            with self.assertRaises(ValueError):service.activate('ENABLE LIVE ORDERS')
            factory.assert_not_called();credentials.assert_not_called()
            self.assertFalse(service.snapshot()['entries'])

    def test_live_requires_reference_and_actual_testnet_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            factory=Mock();credentials=Mock()
            service=ProductionSupervisor(Path(d)/'live.sqlite',enabled=True,password_configured=True,client_factory=factory,credentials=credentials)
            with self.assertRaisesRegex(ValueError,'TradingView'):service.activate('ENABLE LIVE ORDERS')
            factory.assert_not_called();credentials.assert_not_called()

    def test_journal_rejects_wrong_environment_endpoint(self):
        with tempfile.TemporaryDirectory() as d:
            client=ProductionClient()
            with self.assertRaises(ValueError):ExecutionJournal(Path(d)/'journal.sqlite',client)
            self.assertEqual(ExecutionJournal(Path(d)/'live.sqlite',client,environment='live').records(),[])


class ProductionLifecycleTests(unittest.TestCase):
    def test_live_uses_shared_engine_but_recovery_cannot_open_entries(self):
        import json
        from unittest.mock import patch
        from test_testnet import Exchange
        from thoth.models import TestnetSettings,Settings
        from thoth.verification import source_fingerprint,strategy_fingerprint
        class LiveFixture(Exchange):
            BASE='https://api.bybit.com'
            def request(self,method,path,params,private=False):
                result=super().request(method,path,params,private=private)
                if path=='/v5/user/query-api':result['ips']=['203.0.113.10']
                return result
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);now=[1792152000.];exchange=LiveFixture(lambda:now[0])
            cfg=TestnetSettings(dedicated_account=True)
            (root/'validation').mkdir()
            receipt=dict(format=1,source_fingerprint=source_fingerprint(),strategy_fingerprint=strategy_fingerprint(cfg),mismatch_count=0,bars=600,
                coverage={'long_signals':2,'short_signals':2,'level_types':[f+'_'+s+'_'+k for f in ('long','short') for s in ('support','resistance') for k in ('major','minor')]})
            (root/'validation'/'fixture.json').write_text(json.dumps(receipt))
            # Synthetic local guard fixture, never an actual acceptance claim.
            (root/'testnet-acceptance.json').write_text(json.dumps(dict(environment='testnet',source_fingerprint=source_fingerprint(),strategy_fingerprint=strategy_fingerprint(cfg),protected_and_closed=1,unresolved_orders=0,real_exchange=True)))
            def create():return ProductionSupervisor(root/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**kw:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            service=create();service.configure(cfg)
            with self.assertRaises(ValueError):service.activate('ENABLE TESTNET ORDERS')
            service.activate('ENABLE LIVE ORDERS')
            settings=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
            service.apply_signals(settings,{'BTCUSDT':dict(time=now[0]-60,signal=None,mark=100)})
            now[0]+=60;service.apply_signals(settings,{'BTCUSDT':dict(time=now[0]-60,signal='Long',mark=100)})
            exchange.fill(next(iter(exchange.orders)));service.monitor()
            self.assertTrue(service.snapshot()['management'])
            (root/'validation'/'fixture.json').unlink();(root/'testnet-acceptance.json').unlink()
            restarted=create();restarted.activate('ENABLE LIVE ORDERS',recovery=True)
            self.assertFalse(restarted.snapshot()['entries'])
            exchange.prices['BTCUSDT']=98;restarted.monitor()
            self.assertTrue(any(o['reduceOnly'] for o in exchange.orders.values()))
            with self.assertRaises(ValueError):restarted.activate('ENABLE LIVE ORDERS')
