import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.order_block_exit import observe
from thoth.models import TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.bybit_testnet import BybitError
from test_testnet import Exchange
from test_order_block import formation_rows,mirror

NOW=1793000000.

def rows(now=NOW,bearish=False,trigger=True):
    source=formation_rows()[-30:]
    if not trigger:source[-1].update(close=99,high=100)
    if bearish:source=[mirror(row) for row in source]
    start=int(now)//14400*14400
    return [{**row,'volume':0,'time':start-(len(source)-i-1)*14400} for i,row in enumerate(source)]

def sample(tracker,data,side,now,session='fixture'):
    return observe(tracker,data,side,now,session=session,source='fixture-source')

class FormationExitTests(unittest.TestCase):
    def test_opposite_formation_only_no_filters_and_rollback(self):
        for side,bearish,expected in [('Buy',True,'Bearish Order Block formed (exit Long)'),('Sell',False,'Bullish Order Block formed (exit Short)'),('Buy',False,None),('Sell',True,None)]:
            tracker={};self.assertIsNone(sample(tracker,rows(bearish=bearish,trigger=False),side,NOW))
            checkpoint=tracker['closed_state']
            self.assertEqual(sample(tracker,rows(bearish=bearish),side,NOW+5),expected)
            self.assertEqual(tracker['closed_state'],checkpoint)
            self.assertIsNone(sample(tracker,rows(bearish=bearish),side,NOW+10))
            self.assertTrue(tracker['ready'])
            self.assertLess(len(tracker['closed_state']['closes']),200) # SMA 200 is not required.

    def test_existing_formation_restart_gap_and_breaks_do_not_exit(self):
        tracker={}
        self.assertIsNone(sample(tracker,rows(bearish=True),'Buy',NOW))
        self.assertIsNone(sample(tracker,rows(bearish=True),'Buy',NOW+5))
        self.assertIsNone(sample(tracker,rows(bearish=True),'Buy',NOW+10,session='restart'))
        self.assertIsNone(sample(tracker,rows(bearish=True),'Buy',NOW+100))
        quiet=[dict(time=int(NOW)//14400*14400-(29-i)*14400,open=100,high=101,low=99,close=100,volume=1) for i in range(30)]
        tracker={};sample(tracker,quiet,'Buy',NOW)
        tracker['closed_state']['sell']=[dict(high=90,low=89,time=0,bounced=False)]
        tracker['closed_state']['buy']=[dict(high=111,low=110,time=0,bounced=False)]
        self.assertIsNone(sample(tracker,quiet,'Buy',NOW+5))
        self.assertEqual(tracker['fired'],[])
        with self.assertRaises(ValueError):sample(tracker,quiet[:-1],'Buy',NOW+10)

    def test_live_post_entry_formation_on_recovery_and_candle_publication_grace(self):
        start=int(NOW)//14400*14400
        old_entry=(start-14400+100)*1000
        reason=observe({},rows(bearish=True),'Buy',NOW,session='restart',source='source',opened_ms=old_entry)
        self.assertEqual(reason,'Bearish Order Block formed (exit Long)')
        self.assertIsNone(observe({},rows(bearish=True),'Buy',NOW,session='restart',source='source',opened_ms=NOW*1000))
        tracker={};closed=rows(now=start-1,bearish=True)
        self.assertIsNone(observe(tracker,closed,'Buy',start+3,session='s',source='src',opened_ms=old_entry))
        self.assertEqual(tracker['waiting_for_candle'],start)
        with self.assertRaises(ValueError):observe(tracker,closed,'Buy',start+11,session='s',source='src')

class FormationExitIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=[NOW];self.exchange=Exchange(lambda:self.now[0])
        self.service=TestnetSupervisor(Path(self.tmp.name)/'account.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])
        self.cfg=TestnetSettings(strategy='sma_7_50_intrabar',dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT','ETHUSDT'],stop_dollars=1)
        self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.fetch=[]
        def candles(symbol,interval,limit=1000,**kw):
            self.fetch.append((symbol,interval,limit));return rows(self.now[0],bearish=self.bearish,trigger=self.trigger)
        self.bearish=True;self.trigger=False
        self.patch=patch.object(self.exchange,'candles',side_effect=candles);self.patch.start()

    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def enter(self,side):
        self.service._entry('BTCUSDT',side,self.now[0],'fixture-first',self.cfg)
        order=next(o for o in self.exchange.orders.values() if not o['reduceOnly'])
        self.exchange.fill(order['orderLinkId']);self.service.monitor();return order

    def test_long_bearish_formation_closes_only_without_opening_replacement(self):
        self.enter('Long');self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.trigger=True;self.now[0]+=5;self.service.monitor()
        close=next(o for o in self.exchange.orders.values() if o['reduceOnly'])
        self.assertEqual(close['side'],'Sell')
        self.assertEqual(self.service.state['owned']['BTCUSDT']['reason'],'Bearish Order Block formed (exit Long)')
        self.assertEqual(self.service.state['reversals'],{})
        self.exchange.fill(close['orderLinkId']);self.now[0]+=5;self.service.monitor()
        history=self.service.history()['items'][0]
        self.assertEqual(history['reason'],'Bearish Order Block formed (exit Long)')
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
        self.assertTrue(all(interval=='240' for symbol,interval,limit in self.fetch))
        self.assertTrue(self.service.state['entries'])

    def test_short_bullish_formation_and_selectable_disable(self):
        self.bearish=False;self.enter('Short')
        self.service.pause();self.service.configure(self.cfg.model_copy(update={'sma_ob_exit_enabled':False}))
        self.trigger=True;self.now[0]+=5;self.service.monitor()
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.service.configure(self.cfg);self.now[0]+=5;self.service.monitor()
        close=next(o for o in self.exchange.orders.values() if o['reduceOnly'])
        self.assertEqual(close['side'],'Buy')
        self.assertIn('Bullish Order Block formed',self.service.state['owned']['BTCUSDT']['reason'])

    def test_single_coin_data_fault_isolated_and_entry_data_recovers(self):
        self.enter('Long');self.now[0]+=5
        with patch.object(self.exchange,'candles',side_effect=BybitError('fixture unavailable',code=10006)):
            self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.assertIn('BTCUSDT',self.service.state['order_block_exit_blocks'])
        self.assertNotIn('ETHUSDT',self.service.state['order_block_exit_blocks'])
        self.assertFalse(self.service._order_block_entry_ready('BTCUSDT',self.cfg))
        self.now[0]+=31;self.service.monitor()
        self.assertNotIn('BTCUSDT',self.service.state['order_block_exit_blocks'])
        self.assertNotIn('order_block_exit_error',self.service.state['owned']['BTCUSDT'])
