"""Closed-bar direction inversion, rollback, volume independence and execution guards."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.closed_order_block_sr import evaluate,STRATEGY
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange
from test_order_block import formation_rows,mirror

NOW=1793001600.  # Exact hour boundary.


def history(bear=False):
    data=[mirror(r) for r in formation_rows()] if bear else formation_rows()
    start=int(NOW)//3600*3600
    return [{**r,'time':start-(len(data)-i)*3600,'volume':0} for i,r in enumerate(data)]


def sr_result(side=None):
    return dict(time=NOW-3600,signal=side,conflict=False,entry_sources=['sr:long_resistance_major'] if side=='Long' else ['sr:short_support_major'] if side else [])


class ClosedInvertedTests(unittest.TestCase):
    def test_model_is_fixed_hourly_unfiltered_and_preserves_risk(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True,
            major=False,minor=False,opposite_action='close_only',stop_dollars=.4,daily_loss_pct=35,coin_pnl_trailing_enabled=True,
            order_block={'filter_breaks':True,'bullish_formed':False})
        self.assertEqual(cfg.timeframe,'60');self.assertTrue(cfg.major and not cfg.minor)
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled or cfg.order_block.filter_breaks)
        self.assertTrue(cfg.order_block.bullish_formed and cfg.order_block.bearish_formed and cfg.order_block.supply_breakout and cfg.order_block.demand_breakdown)
        self.assertEqual(cfg.opposite_action,'close_only');self.assertEqual(cfg.stop_dollars,.4);self.assertEqual(cfg.daily_loss_pct,35)
        self.assertTrue(cfg.coin_pnl_trailing_enabled)

    def test_ob_sides_invert_at_zero_volume_and_conflicts_skip(self):
        cfg=Settings(strategy=STRATEGY)
        for bear,source,actual in [(False,'Long','Short'),(True,'Short','Long')]:
            data=history(bear);result=evaluate(data,cfg,NOW,sr_result())
            self.assertEqual(result['signal'],actual,result);self.assertEqual(result['source_signal'],source)
            self.assertEqual(result['volume_checks'],{})
            self.assertEqual(result['entry_context']['closed_signal_bar'],NOW-3600)
            self.assertIsNone(evaluate(data,cfg,NOW,sr_result(actual))['signal'])
        with self.assertRaises(ValueError):evaluate(history()[:-1],cfg,NOW,sr_result())

    def test_all_four_ob_event_types_invert_and_component_conflicts_skip(self):
        cfg=Settings(strategy=STRATEGY)
        for kind,source,actual in [('bullish_formed','Long','Short'),('supply_breakout','Long','Short'),('bearish_formed','Short','Long'),('demand_breakdown','Short','Long')]:
            ob=dict(ready=True,signal=source,entry_sources=['ob:'+kind],conflict=False)
            with patch('thoth.order_block.reference',return_value=[ob]):
                result=evaluate(history(),cfg,NOW,sr_result())
                self.assertEqual(result['signal'],actual);self.assertEqual(result['entry_sources'],['ob:'+kind])
                self.assertIsNone(evaluate(history(),cfg,NOW,{**sr_result(),'conflict':True})['signal'])

    def test_sr_major_minor_both_directions_invert_without_ob_requirement(self):
        cfg=Settings(strategy=STRATEGY)
        quiet=[{**r,'open':100,'close':100,'high':101,'low':99} for r in history()]
        for family in ('major',):
            for source,actual in [('Long','Short'),('Short','Long')]:
                key='sr:long_resistance_'+family if source=='Long' else 'sr:short_support_'+family
                result=evaluate(quiet,cfg,NOW,{**sr_result(source),'entry_sources':[key]})
                self.assertEqual(result['signal'],actual);self.assertEqual(result['entry_sources'],[key])

    def test_ob_never_uses_forming_candle_and_no_repeated_or_restart_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')));cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT'])
            data=history();before=NOW-5
            runner.analysis=lambda symbol,source,rows,now,**kwargs:([dict(time=int(now)//3600*3600-3600,signal=None,entry_sources=[])],len(rows)-1)
            runner.scan_cycle=1;self.assertIsNone(runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,data,before)['signal'])
            runner.scan_cycle=2;signal=runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,data,NOW+1)
            self.assertEqual(signal['signal'],'Short',signal)
            runner.scan_cycle=3;self.assertIsNone(runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,data,NOW+5)['signal'])
            restarted=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')));restarted.analysis=runner.analysis;restarted.scan_cycle=1
            self.assertIsNone(restarted.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,data,NOW+6)['signal'])

    def test_worker_closed_fill_stops_follow_actual_side_and_reads_do_not_recheck_volume(self):
        for bear,side in [(False,'Sell'),(True,'Buy')]:
            with self.subTest(bear=bear),tempfile.TemporaryDirectory() as tmp:
                now=[NOW-5];exchange=Exchange(lambda:now[0]);calls=[]
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service)
                def fetch(symbol,interval,limit=1000):calls.append((interval,limit));return history(bear)[-limit:]
                runner=Runner(feed,fetch=feed.fetch)
                runner.analysis=lambda symbol,source,rows,now,**kwargs:([dict(time=int(now)//3600*3600-3600,signal=None,entry_sources=[])],len(history())-1)
                with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                    runner.cycle(paced=False);self.assertFalse(exchange.orders)
                    now[0]=NOW+1;runner.cycle(paced=False)
                    self.assertTrue(exchange.orders,(runner.health,service.state['events'][-3:]))
                    order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],side)
                    self.assertTrue(all(interval=='60' for interval,_ in calls));self.assertFalse(any(limit in (20,21) for _,limit in calls))
                    exchange.fill(order['orderLinkId']);service.monitor()
                    owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                    self.assertEqual(owner['entry_timeframe'],'60');self.assertEqual(owner['exit_timeframe'],'none')
                    stop=float(exchange.active['BTCUSDT']['stopLoss']);self.assertTrue(stop>100 if side=='Sell' else stop<100)
                    now[0]+=5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)

    def test_stale_closed_context_is_blocked_without_network_or_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            exchange=Exchange(lambda:NOW)
            service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:NOW)
            cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            service.configure(cfg);service.activate(service.confirmation)
            service._entry('BTCUSDT','Short',NOW,'old',cfg,dict(closed_signal_bar=NOW-7200))
            self.assertFalse(exchange.orders);self.assertIn('latest CLOSED',service.state['events'][-1]['message'])
