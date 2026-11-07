import unittest
from thoth.delayed_30m import candidate,ready
from thoth.models import Settings

BASE=1790000000//3600*3600

class DelayScheduleTests(unittest.TestCase):
    def test_due_is_fixed_seven_minutes_after_actual_close_not_detection(self):
        cfg=Settings()
        for close in (BASE,BASE+1800):
            obs=dict(signal='Long',closed_bar=close-1800,entry_sources=['ob:bullish_formed'])
            item=candidate(obs,cfg,close+75)
            self.assertEqual(item['due_at'],close+420)
            self.assertFalse(ready(item,cfg,close+419));self.assertTrue(ready(item,cfg,close+420))
            self.assertTrue(ready(item,cfg,close+479));self.assertFalse(ready(item,cfg,close+480))
    def test_old_quarter_hour_candidate_cannot_execute(self):
        cfg=Settings();item=candidate(dict(signal='Long',closed_bar=BASE-1800),cfg,BASE+5)
        item.update(due_at=BASE+900,expires_at=BASE+960)
        self.assertFalse(ready(item,cfg,BASE+900))

    def test_late_future_off_grid_or_absent_signal_is_not_scheduled(self):
        cfg=Settings();obs=dict(signal='Short',closed_bar=BASE-1800,entry_sources=[])
        for bad,now in [(obs,BASE+420),(obs,BASE-1),({**obs,'closed_bar':BASE-1799},BASE+5),({**obs,'signal':None},BASE+5)]:
            self.assertIsNone(candidate(bad,cfg,now))

import tempfile
from pathlib import Path
from unittest.mock import patch
from thoth.delayed_30m import STRATEGY
from thoth.models import TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.order_block import select as original_select
from test_testnet import Exchange

class DelayedExecutionTests(unittest.TestCase):
    def fixture(self,tmp,now,symbols=None):
        e=Exchange(lambda:now[0]);e.fixture_now=now
        s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        s.configure(TestnetSettings(strategy=STRATEGY,symbols=symbols or ['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,max_positions=1))
        s.activate(s.confirmation);cfg=Settings(**{k:v for k,v in s.state['config'].items() if k in Settings.model_fields}).model_dump()
        def capture(side='Long',symbol='BTCUSDT'):
            report=dict(signal=side,closed_bar=int(now[0])//1800*1800-1800,entry_sources=['ob:bullish_formed' if side=='Long' else 'ob:bearish_formed'])
            s.apply_signals(cfg,{symbol:dict(time=now[0],signal=None,mark=100,observation_channel='delayed_capture',delayed_capture=report)})
        return s,e,cfg,capture
    def fill(self,s,e):
        order=list(e.orders.values())[-1];e.fill(order['orderLinkId']);e.fixture_now[0]+=.1;s.monitor();return order
    def test_direct_saved_direction_at_both_due_times_with_no_minute_entry_or_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,capture=self.fixture(tmp,now)
            self.assertEqual(cfg['timeframe'],'30');self.assertFalse(cfg['sma_exit_enabled'])
            with patch.object(e,'candles',side_effect=AssertionError('No minute candles needed for scheduled execution')):
                capture();self.assertEqual(s.state['delayed_30m']['BTCUSDT']['due_at'],BASE+420)
                now[0]=BASE+419;s.monitor();self.assertFalse(e.orders)
                e.prices['BTCUSDT']=125;now[0]=BASE+420;s.monitor();self.assertEqual(len(e.orders),1,s.state['events'][-5:])
                order=self.fill(s,e);self.assertEqual(order['side'],'Buy');self.assertAlmostEqual(float(order['qty']),.4)
                self.assertEqual(s.state['owned']['BTCUSDT']['entry_timeframe'],'30');self.assertEqual(s.state['owned']['BTCUSDT']['exit_timeframe'],'none')
                now[0]=BASE+1805;capture('Short');self.assertEqual(len(e.orders),1)
                self.assertEqual(s.state['delayed_30m']['BTCUSDT']['due_at'],BASE+2220)
                now[0]=BASE+2219;s.monitor();self.assertEqual(len(e.orders),1)
                now[0]=BASE+2220;s.monitor();self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
                self.fill(s,e);self.assertEqual(self.fill(s,e)['side'],'Sell');self.assertEqual(len(e.orders),3)
                now[0]+=5;s.monitor();self.assertEqual(len(e.orders),3)
                self.assertIn('htf:30:ob:bearish_formed',s.state['owned']['BTCUSDT']['entry_sources'])
    def test_missed_window_pause_restart_and_due_risk_block_do_not_replay(self):
        for mode in ('expired','pause','restart','schedule'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                now=[BASE+5];s,e,cfg,capture=self.fixture(tmp,now);capture()
                if mode=='pause':s.pause();s.activate(s.confirmation)
                elif mode=='restart':
                    s=TestnetSupervisor(s.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                    s.activate(s.confirmation,recovery=True)
                now[0]=BASE+(481 if mode=='expired' else 420)
                if mode=='schedule':
                    with patch.object(s,'_schedule_entry_reason',return_value='Daily schedule blocks entry'):s.monitor()
                else:s.monitor()
                self.assertFalse(e.orders);self.assertFalse(s.state['delayed_30m'])
                now[0]+=5;s.monitor();self.assertFalse(e.orders)
    def test_capacity_does_not_create_extra_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,capture=self.fixture(tmp,now,['BTCUSDT','ETHUSDT'])
            capture();now[0]+=1;capture(symbol='ETHUSDT')
            now[0]=BASE+420;s.monitor();self.assertEqual(len(e.orders),1)
            self.fill(s,e);self.assertIn('ETHUSDT',s.state['delayed_30m']);self.assertEqual(len(e.active),1)
            now[0]=BASE+480;s.monitor();self.assertEqual(len(e.orders),1);self.assertFalse(s.state['delayed_30m'])
    def test_final_guard_rejects_order_after_execution_minute_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,capture=self.fixture(tmp,now);capture();original=e.reconcile
            def reconcile(symbol,link):
                answer=original(symbol,link);now[0]=BASE+480.1;return answer
            now[0]=BASE+479.8
            with patch.object(e,'reconcile',side_effect=reconcile):s.monitor()
            self.assertFalse(e.orders);self.assertTrue(s.journal.records()[0]['result']['not_submitted'])
    def test_closed_source_schedules_but_monitor_not_market_scan_places_due_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE-5];s,e,cfg,capture=self.fixture(tmp,now);feed=FeedAdapter(s);main=Runner(feed,fetch=feed.fetch);minute=Runner(feed,confirmation_only=True);calls=[]
            def fetch(symbol,interval,limit=1000,end=None):
                self.assertEqual(interval,'30');calls.append(interval);start=int(now[0])//1800*1800
                return [dict(time=start-(199-i)*1800,open=100,close=100,high=101,low=99,volume=0) for i in range(200)][-limit:]
            def selected(result,settings,**kw):return original_select({**result,'events':['bullish_formed'] if result['time']==BASE-1800 else []},settings,**kw)
            with patch.object(e,'candles',side_effect=fetch),patch('thoth.closed_two_direction.select',side_effect=selected),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                main.cycle(paced=False);self.assertFalse(s.state['delayed_30m'])
                now[0]=BASE+5;main.cycle(paced=False);self.assertIn('BTCUSDT',s.state['delayed_30m']);self.assertFalse(e.orders)
                minute.cycle();self.assertEqual(minute.health['status'],'not_required')
                now[0]=BASE+420;s.monitor();self.assertEqual(len(e.orders),1,s.state['events'][-5:])
                self.assertEqual(list(e.orders.values())[-1]['side'],'Buy');self.assertTrue(calls and all(frame=='30' for frame in calls))
    def test_loss_cooldown_after_delayed_reversal_close_blocks_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,capture=self.fixture(tmp,now);capture();now[0]=BASE+420;s.monitor();self.fill(s,e)
            s.state['risk']['symbols']['BTCUSDT']=dict(streak=s.state['config']['consecutive_losses']-1,pause_until=None);s.save()
            now[0]=BASE+1805;capture('Short');e.prices['BTCUSDT']=99
            now[0]=BASE+2220;s.monitor();self.fill(s,e)
            self.assertEqual(len(e.orders),2);self.assertFalse(s.state['reversals']);self.assertFalse(s.state['owned'])
            self.assertGreater(s.state['risk']['symbols']['BTCUSDT']['pause_until'],now[0])
