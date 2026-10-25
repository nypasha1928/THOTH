import tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.timeframes import interval_seconds
from test_testnet import Exchange

class VolumeDeltaAccountTests(unittest.TestCase):
    def test_defaults_original_source_and_no_sma_exit_profile(self):
        cfg=Settings(strategy='volume_delta_intrabar',sma_exit_enabled=True,major=False,minor=False)
        self.assertFalse(cfg.sma_exit_enabled);self.assertTrue(cfg.volume_delta.sma_filter)
        self.assertEqual((cfg.volume_delta.bull_type,cfg.volume_delta.bear_type),('strong','strong'))
        self.assertEqual(hashlib.sha256((Path(__file__).resolve().parents[1]/'reference/volume_delta_vwap_uploaded.pine').read_bytes()).hexdigest(),'53bad89b605b256d5e7a5bfec7785931cca450d9fc5cad255ad6c0d9676a95fd')
        with self.assertRaises(ValueError):Settings(strategy='volume_delta_intrabar',volume_delta={'bullish':False,'bearish':False})

    def test_ready_symbol_is_processed_before_slow_lower_history_finishes(self):
        import threading
        from types import SimpleNamespace
        now=60000.;gate=threading.Event();sent=[]
        cfg=Settings(strategy='volume_delta_intrabar',automatic_paper=True,symbols=['FASTUSDT','SLOWUSDT'])
        with tempfile.TemporaryDirectory() as d:
            def fetch(symbol,interval):
                if symbol=='SLOWUSDT' and not gate.wait(2):raise AssertionError('Ready symbol was held behind slow fetch')
                return [dict(time=60000,close=100)]
            def dispatch(config,symbol,obs):
                sent.append(symbol)
                if symbol=='FASTUSDT':gate.set()
            engine=SimpleNamespace(path=Path(d)/'fixture.sqlite',snapshot=lambda:dict(settings=cfg.model_dump(),status='running',positions=[]),
                begin_market_scan=lambda:None,market_observation=dispatch,market_cycle=lambda *args:self.fail('duplicate batch'))
            runner=Runner(engine,fetch=fetch)
            with patch('thoth.runner.time.time',return_value=now),patch.object(runner,'volume_delta_observation',return_value=dict(time=now,signal='Long',volume_delta_status='fixture')):
                runner.cycle(paced=False)
            self.assertEqual(sent,['FASTUSDT','SLOWUSDT'])
            self.assertEqual(runner.health['status'],'ok')

    def test_intrabar_entries_all_timeframes_protected_without_sma_exit(self):
        for interval in ('1','5','60','240','D'):
            for side in ('Long','Short'):
                with self.subTest(interval=interval,side=side),tempfile.TemporaryDirectory() as d:
                    duration=interval_seconds(interval);start=duration*200;now=[start+20.]
                    exchange=Exchange(lambda:now[0]);path=Path(d)/'account.sqlite'
                    service=TestnetSupervisor(path,enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                    cfg=TestnetSettings(strategy='volume_delta_intrabar',timeframe=interval,symbols=['BTCUSDT'],dedicated_account=True,sma_exit_enabled=True)
                    service.configure(cfg);service.activate(service.confirmation)
                    def fetch(symbol,tf,limit=1000,end=None):
                        if tf==interval:
                            rows=[dict(time=start-(59-i)*duration,open=100,close=100,high=101,low=99,volume=10) for i in range(60)]
                            rows[-1].update(close=110 if side=='Long' else 90,high=111,low=89)
                            return rows
                        self.assertEqual(tf,'1')
                        return [dict(time=start,open=100,close=101 if side=='Long' else 99,high=102,low=98,volume=10)]
                    adapter=FeedAdapter(service);runner=Runner(adapter,fetch=adapter.fetch)
                    with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('Volume Delta must not call SMA exit')):
                        runner.cycle(paced=False);self.assertFalse(exchange.orders)
                        now[0]+=5;runner.cycle(paced=False)
                        orders=[order for order in exchange.orders.values() if not order['reduceOnly']]
                        self.assertEqual(len(orders),1)
                        order=orders[0];self.assertEqual(order['side'],'Buy' if side=='Long' else 'Sell')
                        exchange.fill(order['orderLinkId']);service.monitor()
                        self.assertTrue(service.state['owned']['BTCUSDT']['protection_confirmed'])
                        self.assertEqual(service.state['owned']['BTCUSDT']['exit_timeframe'],'none')
                        now[0]+=5;runner.cycle(paced=False)
                        self.assertEqual(len([order for order in exchange.orders.values() if not order['reduceOnly']]),1)
                        restored=Runner(adapter,fetch=adapter.fetch)
                        now[0]+=5;restored.cycle(paced=False)
                        now[0]+=5;restored.cycle(paced=False)
                        self.assertEqual(len([order for order in exchange.orders.values() if not order['reduceOnly']]),1)
                    service.close_all()
                    for order in list(exchange.orders.values()):
                        if order.get('reduceOnly') and order['orderStatus']=='New':exchange.fill(order['orderLinkId'])
                    service.monitor()
                    trade=service.history()['items'][0]
                    self.assertEqual(trade['strategy'],'volume_delta_intrabar')
                    self.assertIn('Volume Delta',trade['entry_source_label'])
                    self.assertEqual(trade['exit_timeframe'],'none')
