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

    def test_live_requires_reference_unless_explicitly_opted_out(self):
        with tempfile.TemporaryDirectory() as d:
            factory=Mock();credentials=Mock()
            service=ProductionSupervisor(Path(d)/'live.sqlite',enabled=True,password_configured=True,client_factory=factory,credentials=credentials)
            with self.assertRaisesRegex(ValueError,'TradingView'):service.activate('ENABLE LIVE ORDERS')
            factory.assert_not_called();credentials.assert_not_called()

    def test_unverified_opt_in_does_not_skip_server_or_account_gates(self):
        from test_testnet import Exchange
        from thoth.models import TestnetSettings
        with tempfile.TemporaryDirectory() as d:
            credentials=Mock()
            disabled=ProductionSupervisor(Path(d)/'disabled.sqlite',allow_unverified=True,credentials=credentials)
            with self.assertRaisesRegex(ValueError,'disabled'):disabled.activate('ENABLE LIVE ORDERS')
            credentials.assert_not_called()
            class LiveFixture(Exchange):
                BASE='https://api.bybit.com'
            exchange=LiveFixture(lambda:1792152000.)
            service=ProductionSupervisor(Path(d)/'live.sqlite',enabled=True,password_configured=True,
                allow_unverified=True,client_factory=lambda *a,**kw:exchange,credentials=lambda:('fixture','fixture'))
            with self.assertRaises(ValueError):service.activate('ENABLE LIVE ORDERS')
            service.configure(TestnetSettings(dedicated_account=True))
            with self.assertRaisesRegex(ValueError,'egress IP'):service.activate('ENABLE LIVE ORDERS')
            self.assertFalse(service.snapshot()['entries'])
            self.assertTrue(service.snapshot()['unverified_live_allowed'])

    def test_live_confirmation_error_names_live_and_trims_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            service=ProductionSupervisor(Path(d)/'live.sqlite')
            with self.assertRaisesRegex(ValueError,'Type ENABLE LIVE ORDERS to enable LIVE trading'):
                service.activate('ENABLE TESTNET ORDERS')
            with self.assertRaisesRegex(ValueError,'disabled'):
                service.activate('  ENABLE LIVE ORDERS  ')

    def test_journal_rejects_wrong_environment_endpoint(self):
        with tempfile.TemporaryDirectory() as d:
            client=ProductionClient()
            with self.assertRaises(ValueError):ExecutionJournal(Path(d)/'journal.sqlite',client)
            self.assertEqual(ExecutionJournal(Path(d)/'live.sqlite',client,environment='live').records(),[])


class ProductionLifecycleTests(unittest.TestCase):
    def test_live_uses_shared_engine_but_recovery_cannot_open_entries(self):
        self.exercise_lifecycle(False)

    def test_explicit_unverified_live_still_protects_and_recovers(self):
        self.exercise_lifecycle(True)

    def exercise_lifecycle(self,unverified):
        import json
        from unittest.mock import patch
        from test_testnet import Exchange
        from thoth.models import TestnetSettings,Settings
        from thoth.verification import source_fingerprint,strategy_fingerprint,execution_fingerprint
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
            if unverified:
                (root/'validation'/'fixture.json').unlink()
            def create():return ProductionSupervisor(root/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**kw:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0],allow_unverified=unverified)
            service=create();service.configure(cfg)
            with self.assertRaises(ValueError):service.activate('ENABLE TESTNET ORDERS')
            service.activate('ENABLE LIVE ORDERS')
            settings=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
            service.apply_signals(settings,{'BTCUSDT':dict(time=now[0]-60,signal=None,mark=100)})
            now[0]+=60;service.apply_signals(settings,{'BTCUSDT':dict(time=now[0]-60,signal='Long',mark=100)})
            exchange.fill(next(iter(exchange.orders)));service.monitor()
            self.assertTrue(service.snapshot()['management'])
            (root/'validation'/'fixture.json').unlink(missing_ok=True)
            restarted=create();restarted.activate('ENABLE LIVE ORDERS',recovery=True)
            self.assertFalse(restarted.snapshot()['entries'])
            exchange.prices['BTCUSDT']=98;restarted.monitor()
            self.assertTrue(any(o['reduceOnly'] for o in exchange.orders.values()))
            if not unverified:
                with self.assertRaises(ValueError):restarted.activate('ENABLE LIVE ORDERS')
