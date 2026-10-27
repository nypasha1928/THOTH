"""Unfiltered fixed-4h Order Block entries; exchange behavior is simulated."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.verification import strategy_fingerprint
from test_testnet import Exchange
from test_order_block import formation_rows,mirror

STRATEGY='order_block_intrabar'
NOW=1793000000.


def rows(now=NOW,kind='bullish_formed',trigger=True,passing=True):
    source=formation_rows()[-30:]
    if kind.endswith('_formed'):
        if not trigger:source[-1].update(close=99,high=100)
    else:
        source.append(dict(time=0,open=108,close=108,high=109,low=96 if trigger else 100,volume=0))
    if kind in ('bearish_formed','supply_breakout'):source=[mirror(row) for row in source]
    start=int(now)//14400*14400
    return [{**row,'time':start-(len(source)-i-1)*14400,'volume':40 if passing and i==len(source)-1 else 10} for i,row in enumerate(source)]


class DirectOrderBlockTests(unittest.TestCase):
    def test_fixed4h_all_events_no_filters_and_preserves_opposite_action(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',opposite_action='close_only',sma_enabled=True,sma_7_50_filter_enabled=True,
            sma_exit_enabled=True,major=False,minor=False,order_block=dict(filter_breaks=True,bullish_formed=False,bearish_formed=False,supply_breakout=False,demand_breakdown=False))
        self.assertEqual(cfg.timeframe,'240');self.assertEqual(cfg.opposite_action,'close_only')
        self.assertFalse(cfg.sma_exit_enabled or cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.order_block.filter_breaks)
        self.assertTrue(all(getattr(cfg.order_block,kind) for kind in ('bullish_formed','bearish_formed','supply_breakout','demand_breakdown')))

    def test_all_four_real_events_forming_with_volume_and_no_sma200_warmup(self):
        for kind,side in (('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')))
                runner.scan_cycle=1
                self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind,trigger=False),NOW)['signal'])
                runner.scan_cycle=2
                observation=runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind),NOW+5)
                self.assertEqual(observation['signal'],side,observation)
                self.assertIn('ob:'+kind,observation['entry_sources'])
                self.assertIn('Volume > MA 20 required',observation['volume_delta_status'])
                runner.scan_cycle=3
                self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind),NOW+10)['signal'])
                restarted=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')));restarted.scan_cycle=1
                self.assertIsNone(restarted.order_block_observation('BTCUSDT',cfg,rows(kind=kind),NOW+15)['signal'])
                with sqlite3.connect(runner.history_path) as db:
                    state=json.loads(db.execute('SELECT payload FROM order_block_checkpoints').fetchone()[0])
                self.assertLess(len(state['state']['closes']),200)

    def test_startup_gap_and_conflicting_events_do_not_open_old_or_ambiguous_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')))
            runner.scan_cycle=1
            self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(),NOW)['signal'])
            runner.scan_cycle=2
            self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(kind='bearish_formed'),NOW+400)['signal'])
            from thoth.order_block import select
            result=select(dict(events=['bullish_formed','demand_breakdown'],sma200=None,close=100,ready=True),cfg.order_block,formation_filter=False)
            self.assertTrue(result['conflict']);self.assertIsNone(result['signal'])

    def test_worker_direct_entry_stop_verification_no_gate_or_sma_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[NOW];trigger=[False];exchange=Exchange(lambda:now[0]);path=Path(tmp)/'account.sqlite'
            service=TestnetSupervisor(path,enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service);calls=[]
            def fetch(symbol,interval,**kwargs):calls.append((interval,kwargs['limit']));return rows(now[0],trigger=trigger[0])
            runner=Runner(feed,fetch=feed.fetch)
            with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')),patch('thoth.order_block_exit.observe',side_effect=AssertionError('No separate OB exit')):
                runner.cycle(paced=False);self.assertFalse(exchange.orders)
                now[0]+=5;trigger[0]=True;runner.cycle(paced=False)
                order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],'Buy')
                self.assertEqual(calls,[('240',1000),('240',60),('240',20)])
                exchange.fill(order['orderLinkId']);service.monitor()
                owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                self.assertEqual(owner['entry_timeframe'],'240');self.assertEqual(owner['exit_timeframe'],'none')
                self.assertEqual(owner['entry_sources'],['ob:bullish_formed']);self.assertEqual(service.state['entry_gates'],{})
                self.assertEqual(runner.health['status'],'ok')
                now[0]+=5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
                service._close('BTCUSDT',exchange.active['BTCUSDT'],'Fixture close')
                for order in list(exchange.orders.values()):
                    if order['reduceOnly']:exchange.fill(order['orderLinkId'])
                service.monitor();trade=service.history()['items'][0]
                self.assertEqual(trade['strategy'],STRATEGY);self.assertIn('Bullish Order Block formed',trade['entry_source_label'])
                count=len(exchange.orders);now[0]+=40
                service.apply_signals(Settings(**{k:v for k,v in cfg.model_dump().items() if k in Settings.model_fields}).model_dump(),
                    {'BTCUSDT':dict(time=now[0]-31,signal='Long',mark=100,entry_sources=['ob:bullish_formed'])})
                self.assertEqual(len(exchange.orders),count)
                self.assertIn('signal expired',service.state['events'][-1]['message'])

    def test_account_controls_are_not_modified_by_new_entry_profile(self):
        controls=dict(stop_dollars=1,take_profit_enabled=True,take_profit_dollars=20,daily_loss_pct=35,
            portfolio_loss_enabled=True,portfolio_loss_pct=25,pnl_trailing_enabled=True,pnl_trailing_activation_dollars=20,pnl_trailing_drop_dollars=1,
            coin_pnl_trailing_enabled=True,coin_pnl_trailing_activation_dollars=5,coin_pnl_trailing_drop_dollars=.5,
            daily_equity_trailing_enabled=True,daily_equity_trailing_drop_dollars=1,three_day_equity_trailing_enabled=True,
            three_day_equity_trailing_drop_dollars=2,weekly_equity_trailing_enabled=True,weekly_equity_trailing_drop_dollars=3,
            consecutive_losses=3,pause_minutes=60,max_positions=20,leverage=20,position_value=50)
        cfg=Settings(strategy=STRATEGY,**controls)
        for key,value in controls.items():self.assertEqual(getattr(cfg,key),value,key)


    def test_selected_reverse_or_close_only_action_is_respected(self):
        for action in ('reverse','close_only'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as tmp:
                now=[NOW];exchange=Exchange(lambda:now[0])
                service=TestnetSupervisor(Path(tmp)/'account.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,opposite_action=action,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
                service.configure(cfg);service.activate(service.confirmation)
                feed_cfg=Settings(**{k:v for k,v in cfg.model_dump().items() if k in Settings.model_fields}).model_dump()
                def signal(side,source):
                    now[0]+=5
                    service.apply_signals(feed_cfg,{'BTCUSDT':dict(time=now[0],mark=100,signal=side,entry_sources=['ob:'+source],entry_context={'order_block_bar':int(now[0])//14400*14400})})
                exchange.candles=lambda *args,**kwargs:rows(now[0])
                signal(None,'bullish_formed');signal('Long','bullish_formed')
                entry=next(iter(exchange.orders.values()));exchange.fill(entry['orderLinkId']);service.monitor()
                signal('Short','demand_breakdown')
                close=next(order for order in exchange.orders.values() if order['reduceOnly'])
                self.assertEqual(service.state['owned']['BTCUSDT']['signal_exit'],'Opposite Order Block signal')
                self.assertEqual('BTCUSDT' in service.state['reversals'],action=='reverse')
                exchange.fill(close['orderLinkId']);service.monitor()
                replacements=[order for order in exchange.orders.values() if not order['reduceOnly'] and order['side']=='Sell']
                self.assertEqual(len(replacements),1 if action=='reverse' else 0)
                if replacements:
                    exchange.fill(replacements[0]['orderLinkId']);service.monitor()
                    owner=service.state['owned']['BTCUSDT']
                    self.assertTrue(owner['protection_confirmed']);self.assertEqual(owner['entry_sources'],['ob:demand_breakdown'])

    def test_new_opposite_break_can_follow_consumed_break_in_same_forming_candle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')))
            start=int(NOW)//14400*14400
            history=[dict(time=start-(29-i)*14400,open=100,close=100,high=101,low=99,volume=40 if i==29 else 10) for i in range(30)]
            runner.scan_cycle=1;runner.order_block_observation('BTCUSDT',cfg,history,NOW)
            with sqlite3.connect(runner.history_path) as db:
                saved=json.loads(db.execute('SELECT payload FROM order_block_checkpoints').fetchone()[0])
                saved['state']['sell']=[dict(high=105,low=103,time=start-14400,bounced=False)]
                saved['state']['buy']=[dict(high=97,low=95,time=start-14400,bounced=False)]
                db.execute('UPDATE order_block_checkpoints SET payload=?',(json.dumps(saved),))
            runner.scan_cycle=2;history[-1]['high']=106
            first=runner.order_block_observation('BTCUSDT',cfg,history,NOW+5)
            self.assertEqual(first['signal'],'Long');self.assertEqual(first['entry_sources'],['ob:supply_breakout'])
            runner.scan_cycle=3;history[-1]['low']=94
            second=runner.order_block_observation('BTCUSDT',cfg,history,NOW+10)
            self.assertEqual(second['signal'],'Short');self.assertEqual(second['entry_sources'],['ob:demand_breakdown'])
            self.assertFalse(second['order_block_conflict'])
            runner.scan_cycle=4
            self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,history,NOW+15)['signal'])

    def test_every_family_waits_for_volume_pass_without_queuing_or_consuming_event(self):
        for kind,side in (('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')))
                runner.scan_cycle=1;runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind,trigger=False,passing=False),NOW)
                runner.scan_cycle=2
                low=runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind,passing=False),NOW+5)
                self.assertIsNone(low['signal']);self.assertEqual(low['entry_sources'],[]);self.assertEqual(low['order_block_events'],[])
                self.assertFalse(low['volume_checks']['240']['passed'])
                runner.scan_cycle=3
                high=runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind,passing=True),NOW+10)
                self.assertEqual(high['signal'],side);self.assertEqual(high['entry_sources'],['ob:'+kind])
                runner.scan_cycle=4
                self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(kind=kind,passing=True),NOW+15)['signal'])

    def test_event_must_still_hold_when_volume_passes_and_recovery_does_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')))
            runner.scan_cycle=1;runner.order_block_observation('BTCUSDT',cfg,rows(trigger=False,passing=False),NOW)
            runner.scan_cycle=2;runner.order_block_observation('BTCUSDT',cfg,rows(passing=False),NOW+5)
            runner.scan_cycle=3
            self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,rows(trigger=False,passing=True),NOW+10)['signal'])
            restarted=Runner(SimpleNamespace(path=str(Path(tmp)/'account.sqlite')));restarted.scan_cycle=1
            self.assertIsNone(restarted.order_block_observation('BTCUSDT',cfg,rows(passing=False),NOW+15)['signal'])
            restarted.scan_cycle=2
            self.assertIsNone(restarted.order_block_observation('BTCUSDT',cfg,rows(passing=True),NOW+20)['signal'])

    def test_submission_rechecks_volume_and_current_signal_bar(self):
        for scenario in ('equal','below','stale_bar'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as tmp:
                now=[NOW];exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'account.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
                service.configure(cfg);service.activate(service.confirmation)
                feed_cfg=Settings(**{k:v for k,v in cfg.model_dump().items() if k in Settings.model_fields}).model_dump()
                service.apply_signals(feed_cfg,{'BTCUSDT':dict(time=now[0],signal=None,mark=100)})
                latest=rows(now[0],passing=scenario=='stale_bar')[-20:]
                if scenario=='below':latest[-1]['volume']=1
                exchange.candles=lambda *args,**kwargs:latest
                now[0]+=5;bar=int(now[0])//14400*14400-(14400 if scenario=='stale_bar' else 0)
                service.apply_signals(feed_cfg,{'BTCUSDT':dict(time=now[0],signal='Long',mark=100,entry_sources=['ob:bullish_formed'],entry_context=dict(order_block_bar=bar))})
                self.assertFalse(exchange.orders);self.assertTrue(service.state['entries'])
                self.assertIn('not from the current 4h' if scenario=='stale_bar' else 'Volume must exceed MA 20',service.state['events'][-1]['message'])

    def test_low_volume_real_worker_does_not_queue_trade_or_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[NOW];trigger=[False];exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'account.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            service.configure(cfg);service.activate(service.confirmation);runner=Runner(FeedAdapter(service),fetch=lambda *args:rows(now[0],trigger=trigger[0],passing=False))
            with patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False);now[0]+=5;trigger[0]=True;runner.cycle(paced=False)
            self.assertFalse(exchange.orders);self.assertEqual(service.state['pending_signals'],{})
            self.assertFalse(any('signal queued' in event['message'] for event in service.state['events']))

    def test_entry_volume_requires_full_consecutive_4h_window(self):
        from thoth.order_block import entry_volume
        history=rows()
        report=entry_volume(history)
        self.assertEqual(report['volume'],40);self.assertEqual(report['average'],11.5);self.assertTrue(report['passed'])
        self.assertFalse(entry_volume(history[-19:])['passed'])
        history[-3]['time']-=14400
        with self.assertRaises(ValueError):entry_volume(history)
