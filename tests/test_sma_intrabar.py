import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.sma_intrabar import observe,STRATEGY
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange

class IntrabarSMATests(unittest.TestCase):
    def rows(self,now,price=100):
        start=int(now)//14400*14400
        return [dict(time=start-(49-i)*14400,open=100,high=max(100,price) if i==49 else 100,low=min(100,price) if i==49 else 100,close=price if i==49 else 100,volume=100) for i in range(50)]

    def test_both_intrabar_crosses_no_duplicate_or_restart_replay(self):
        now=1793000000.;tracker={}
        self.assertIsNone(observe(tracker,self.rows(now),now)['signal'])
        self.assertEqual(observe(tracker,self.rows(now,107),now+5)['signal'],'Long')
        self.assertIsNone(observe(tracker,self.rows(now,108),now+10)['signal'])
        self.assertEqual(observe(tracker,self.rows(now,93),now+15)['signal'],'Short')
        self.assertEqual(observe(tracker,self.rows(now,107),now+20)['signal'],'Long')
        self.assertIsNone(observe(tracker,self.rows(now,93),now+100)['signal'])
        self.assertIsNone(observe({},self.rows(now,107),now)['signal'])
        with self.assertRaises(ValueError):observe({},self.rows(now)[:-1],now)

    def test_fixed_4h_reversal_no_exit_and_no_sr_filters(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',sma_exit_enabled=True,opposite_action='close_only',sma_enabled=True,sma_7_50_filter_enabled=True)
        self.assertEqual(cfg.timeframe,'240');self.assertEqual(cfg.opposite_action,'reverse')
        self.assertFalse(cfg.sma_exit_enabled or cfg.sma_enabled or cfg.sma_7_50_filter_enabled)

    def test_worker_places_protected_long_then_closes_for_reversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[1793000000.];price=[100];exchange=Exchange(lambda:now[0])
            service=TestnetSupervisor(Path(tmp)/'account.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service)
            runner=Runner(feed,fetch=lambda symbol,interval:self.rows(now[0],price[0]))
            with patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                runner.cycle(paced=False);self.assertFalse(exchange.orders)
                now[0]+=5;price[0]=107;runner.cycle(paced=False)
                order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],'Buy')
                exchange.fill(order['orderLinkId']);service.monitor()
                owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                self.assertEqual(owner['exit_timeframe'],'none');self.assertEqual(owner['entry_timeframe'],'240')
                now[0]+=5;price[0]=93;runner.cycle(paced=False)
                self.assertEqual(service.state['reversals']['BTCUSDT']['side'],'Short')
                close=next(o for o in exchange.orders.values() if o['reduceOnly'])
                exchange.fill(close['orderLinkId']);service.monitor()
                now[0]+=5;runner.cycle(paced=False)
                short=next(o for o in exchange.orders.values() if o['side']=='Sell' and not o['reduceOnly'])
                self.assertEqual(short['orderStatus'],'New')
                runner.observation_session='restart';now[0]+=5;price[0]=107
                self.assertIsNone(runner.sma_intrabar_observation('BTCUSDT',cfg,self.rows(now[0],price[0]),now[0])['signal'])
