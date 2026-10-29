"""Direct minute entries, per-coin/hour limits, a timed window and close-only exits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.bybit_testnet import BybitError,AmbiguousOrder
from test_testnet import Exchange

STRATEGY='ob_sr_minute_hourly'
NOW=1793001610.


class MinuteHourlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'live.sqlite'
        self.now=[NOW];self.exchange=Exchange(lambda:self.now[0])
        self.service=self.make_service();self.cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT','ETHUSDT','SOLUSDT'],max_positions=2,dedicated_account=True,daily_loss_pct=90)
        self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.feed_cfg=Settings(**{k:v for k,v in self.cfg.model_dump().items() if k in Settings.model_fields}).model_dump()
        self.sample({'BTCUSDT':None,'ETHUSDT':None,'SOLUSDT':None})

    def make_service(self):
        return TestnetSupervisor(self.path,enabled=True,password_configured=True,
            client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])

    def tearDown(self):self.tmp.cleanup()

    def sample(self,signals):
        self.now[0]+=5
        self.service.apply_signals(self.feed_cfg,{symbol:dict(time=self.now[0],mark=100,signal=side,
            entry_sources=['ob:bullish_formed' if side=='Long' else 'sr:short_support_major'],entry_context=dict(order_block_bar=int(self.now[0])//60*60)) for symbol,side in signals.items()})

    def fill_entry(self):
        order=next(o for o in self.exchange.orders.values() if not o['reduceOnly'] and o['orderStatus']=='New')
        self.exchange.fill(order['orderLinkId']);self.service.monitor();return order

    def close(self):
        symbol=next(iter(self.service.state['owned']));self.service._close(symbol,self.exchange.active[symbol],'Fixture trailing close')
        order=next(o for o in self.exchange.orders.values() if o['reduceOnly'] and o['orderStatus']=='New')
        self.exchange.fill(order['orderLinkId']);self.service.monitor()

    def entries(self):return [o for o in self.exchange.orders.values() if not o['reduceOnly']]

    def test_profile_is_direct_minute_no_filters_close_only_and_keeps_risk(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',opposite_action='reverse',sma_enabled=True,sma_7_50_filter_enabled=True,
            sma_exit_enabled=True,major=False,minor=False,stop_dollars=.4,leverage=20,weekly_equity_trailing_enabled=True)
        self.assertEqual((cfg.timeframe,cfg.opposite_action),('1','close_only'))
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled or cfg.order_block.filter_breaks)
        self.assertTrue(cfg.major and not cfg.minor and cfg.weekly_equity_trailing_enabled)
        self.assertEqual((cfg.stop_dollars,cfg.leverage),(.4,20));self.assertEqual(self.service.state['entry_gates'],{})

    def test_each_coin_once_and_total_hour_cap_survive_closes_restart_and_new_hour(self):
        self.sample({'BTCUSDT':'Long'});self.fill_entry();self.close()
        self.sample({'BTCUSDT':'Short'});self.assertEqual(len(self.entries()),1)
        self.sample({'ETHUSDT':'Short'});self.fill_entry();self.close()
        self.assertEqual(len(self.entries()),2)
        self.assertEqual(self.service.snapshot()['hourly_entry_slot']['used_count'],2)
        self.sample({'SOLUSDT':'Long'});self.assertEqual(len(self.entries()),2)
        self.service.state.pop('minute_hour_entries');self.service.save()
        self.assertEqual(self.service._minute_hour_symbols(),{'BTCUSDT','ETHUSDT'})
        self.service=self.make_service();self.service.activate(self.service.confirmation)
        self.sample({'SOLUSDT':'Long'});self.assertEqual(len(self.entries()),2)
        self.now[0]=(int(self.now[0])//3600+1)*3600+1
        self.service.monitor();self.assertEqual(len(self.entries()),2)
        self.assertEqual(self.service.snapshot()['hourly_entry_slot']['used_count'],0)
        self.sample({'SOLUSDT':'Short'});self.assertEqual(len(self.entries()),3)

    def test_entry_windows_5_10_15_stop_new_entries_without_stopping_opposite_exit(self):
        start=int(self.now[0])//3600*3600
        for minutes in (5,10,15):
            cfg=self.cfg.model_copy(update={'minute_entry_window_minutes':minutes})
            self.now[0]=start+minutes*60-1
            self.assertIsNone(self.service._minute_hour_block_reason('BTCUSDT',cfg))
            self.now[0]+=1
            self.assertIn('window closed',self.service._minute_hour_block_reason('BTCUSDT',cfg))
        self.now[0]=start+20;self.sample({'BTCUSDT':'Long'});self.fill_entry()
        self.now[0]=start+16*60
        before=len(self.entries());self.sample({'ETHUSDT':'Long','BTCUSDT':'Short'})
        self.assertEqual(len(self.entries()),before)
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.assertFalse(self.service.state['pending_signals']);self.assertFalse(self.service.state['reversals'])

    def test_opposite_exit_works_while_slot_used_and_entries_paused_never_reverses(self):
        for side,opposite in [('Long','Short'),('Short','Long')]:
            with self.subTest(side=side):
                if self.entries():self.now[0]=(int(self.now[0])//3600+1)*3600+1
                self.service.activate(self.service.confirmation)
                self.sample({'BTCUSDT':side});self.fill_entry()
                self.sample({'BTCUSDT':side});self.assertFalse([o for o in self.exchange.orders.values() if o['reduceOnly'] and o['orderStatus']=='New'])
                self.service.pause();self.sample({'BTCUSDT':opposite})
                close=next(o for o in self.exchange.orders.values() if o['reduceOnly'] and o['orderStatus']=='New')
                self.assertFalse(self.service.state['reversals']);self.exchange.fill(close['orderLinkId']);self.service.monitor()
                self.assertEqual(self.service.history()['items'][0]['reason'],'Opposite 1m Order Block / S/R signal')
                before=len(self.entries());self.service.activate(self.service.confirmation);self.sample({'BTCUSDT':opposite});self.assertEqual(len(self.entries()),before)

    def test_definite_rejection_releases_slot_but_uncertain_submission_keeps_it(self):
        with patch.object(self.exchange,'submit',side_effect=BybitError('fixture reject',code=110125)):
            self.sample({'BTCUSDT':'Long'})
        self.assertEqual(self.service._minute_hour_symbols(),set())
        with patch.object(self.exchange,'submit',side_effect=AmbiguousOrder('fixture unknown')):
            self.sample({'ETHUSDT':'Short'})
        self.assertIn('ETHUSDT',self.service._minute_hour_symbols())
        self.sample({'BTCUSDT':'Short'});self.assertIn('ETHUSDT',self.service._minute_hour_symbols())

    def test_signal_that_expires_during_checks_cannot_submit_after_window(self):
        self.now[0]=int(self.now[0])//3600*3600+293
        original=self.exchange.request
        def slow_checks(method,path,params,private=False):
            response=original(method,path,params,private)
            if path=='/v5/market/tickers':self.now[0]+=3
            return response
        with patch.object(self.exchange,'request',side_effect=slow_checks):
            self.sample({'BTCUSDT':'Long'})
        self.assertFalse(self.entries());self.assertFalse(self.service._minute_hour_symbols())

    def test_all_four_raw_ob_events_on_minute_chart_without_volume(self):
        from types import SimpleNamespace
        from test_order_block_intrabar import rows as source_rows
        for kind,side in [('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')))
                cfg=Settings(strategy=STRATEGY);start=int(self.now[0])//60*60
                def rows(trigger):
                    data=source_rows(kind=kind,trigger=trigger,passing=False)
                    return [{**r,'time':start-(len(data)-i-1)*60,'volume':0} for i,r in enumerate(data)]
                runner.scan_cycle=1
                self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(False),self.now[0])['signal'])
                runner.scan_cycle=2
                with patch('thoth.order_block.entry_volume',side_effect=AssertionError('No volume filter')):
                    out=runner.order_block_observation('BTCUSDT',cfg,rows(True),self.now[0]+5)
                self.assertEqual(out['signal'],side);self.assertEqual(out['volume_checks'],{})

    def test_real_worker_fresh_minute_entry_and_opposite_exit_without_hourly_data(self):
        feed=FeedAdapter(self.service);runner=Runner(feed,fetch=feed.fetch);prices={'BTCUSDT':100,'ETHUSDT':100};calls=[]
        def candles(symbol,interval,limit=1000):
            calls.append(interval);self.assertEqual(interval,'1');start=int(self.now[0])//60*60
            return [dict(time=start-(199-i)*60,open=100,close=prices[symbol] if i==199 else 100,
                high=max(101,prices[symbol]),low=min(99,prices[symbol]),volume=0) for i in range(200)][-limit:]
        def analysis(symbol,cfg,rows,now,**kwargs):
            return [dict(time=int(now)//60*60-60,levels={'long_resistance_major':dict(value=103,pivot_time=0),'short_support_major':dict(value=97,pivot_time=0)})],200
        runner.analysis=analysis
        with patch.object(self.exchange,'candles',side_effect=candles),patch('thoth.runner.time.time',side_effect=lambda:self.now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
            runner.cycle(paced=False);self.assertFalse(self.entries())
            self.now[0]+=5;prices['BTCUSDT']=104;runner.cycle(paced=False)
            self.assertEqual(len(self.entries()),1);order=self.fill_entry();self.assertEqual(order['side'],'Buy')
            self.assertTrue(self.service.state['owned']['BTCUSDT']['protection_confirmed'])
            self.assertEqual(self.service.state['entry_gates'],{})
            self.now[0]=int(self.now[0])//3600*3600+299;runner.cycle(paced=False)
            self.now[0]+=2;prices['BTCUSDT']=96;prices['ETHUSDT']=104;runner.cycle(paced=False)
            self.assertEqual(len(self.entries()),1);self.assertFalse(self.service.state['reversals'])
            self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
            self.assertTrue(calls);self.assertEqual(set(calls),{'1'})
