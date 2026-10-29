"""Normal direction shares only the completed-candle engine with inverted mode."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.closed_order_block_sr import evaluate,STRATEGY,NORMAL_STRATEGY
from thoth.timeframes import SHORT_LIVED_SIGNAL_STRATEGIES
from thoth.verification import strategy_fingerprint
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange
from test_closed_inverted_ob_sr import NOW,history,sr_result


class ClosedNormalTests(unittest.TestCase):
    def test_fixed_hourly_unfiltered_profile_retains_risk_and_opposite_action(self):
        cfg=Settings(strategy=NORMAL_STRATEGY,timeframe='D',sma_enabled=True,sma_exit_enabled=True,sma_7_50_filter_enabled=True,
            opposite_action='close_only',major=False,minor=False,stop_dollars=.4,daily_loss_pct=35,pnl_trailing_enabled=True,weekly_equity_trailing_enabled=True)
        self.assertEqual(cfg.timeframe,'60');self.assertTrue(cfg.major and cfg.minor)
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled or cfg.order_block.filter_breaks)
        self.assertEqual(cfg.opposite_action,'close_only');self.assertEqual(cfg.stop_dollars,.4)
        self.assertTrue(cfg.pnl_trailing_enabled and cfg.weekly_equity_trailing_enabled)
        self.assertEqual(cfg.daily_loss_pct,35)
        self.assertIn(NORMAL_STRATEGY,SHORT_LIVED_SIGNAL_STRATEGIES)
        self.assertNotEqual(strategy_fingerprint(cfg),strategy_fingerprint(Settings(strategy=STRATEGY)))

    def test_all_four_ob_types_and_sr_major_minor_keep_source_direction(self):
        normal=Settings(strategy=NORMAL_STRATEGY);inverted=Settings(strategy=STRATEGY)
        for kind,side in [('bullish_formed','Long'),('supply_breakout','Long'),('bearish_formed','Short'),('demand_breakdown','Short')]:
            with patch('thoth.order_block.reference',return_value=[dict(ready=True,signal=side,entry_sources=['ob:'+kind],conflict=False)]):
                result=evaluate(history(),normal,NOW,sr_result())
                self.assertEqual(result['signal'],side);self.assertEqual(result['source_signal'],side)
                self.assertNotEqual(evaluate(history(),inverted,NOW,sr_result())['signal'],side)
                self.assertEqual(result['volume_checks'],{})
        quiet=[{**r,'open':100,'close':100,'high':101,'low':99} for r in history()]
        for family in ('major','minor'):
            for side in ('Long','Short'):
                key='sr:long_resistance_'+family if side=='Long' else 'sr:short_support_'+family
                self.assertEqual(evaluate(quiet,normal,NOW,{**sr_result(side),'entry_sources':[key]})['signal'],side)
        self.assertIsNone(evaluate(history(),normal,NOW,sr_result('Short'))['signal'])

    def test_worker_waits_for_close_then_opens_normal_side_with_protection(self):
        for branch in ('ob','sr'):
            for bearish in (False,True):
                with self.subTest(branch=branch,bearish=bearish),tempfile.TemporaryDirectory() as tmp:
                    now=[NOW-5];exchange=Exchange(lambda:now[0]);calls=[]
                    service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                    cfg=TestnetSettings(strategy=NORMAL_STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
                    service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service)
                    data=history(bearish)
                    if branch=='sr':data=[{**r,'open':100,'close':100,'high':101,'low':99} for r in data]
                    def fetch(symbol,interval,limit=1000):calls.append((interval,limit));return data[-limit:]
                    runner=Runner(feed,fetch=feed.fetch)
                    side='Short' if bearish else 'Long'
                    def analysis(symbol,source,rows,stamp,**kwargs):
                        result=sr_result(side if branch=='sr' and stamp>=NOW else None)
                        return [{**result,'time':int(stamp)//3600*3600-3600}],200
                    runner.analysis=analysis
                    with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                        runner.cycle(paced=False);self.assertFalse(exchange.orders)
                        now[0]=NOW+1;runner.cycle(paced=False)
                        self.assertTrue(runner.health['symbols']['BTCUSDT']['sampling']['continuous'])
                        self.assertEqual(runner.health['symbols']['BTCUSDT']['sampling']['limit'],3600.)
                        self.assertTrue(exchange.orders,(runner.health,service.state['events'][-3:]))
                        order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],'Sell' if bearish else 'Buy')
                        self.assertTrue(all(interval=='60' for interval,_ in calls))
                        self.assertFalse(any(limit in (20,21) for _,limit in calls))
                        exchange.fill(order['orderLinkId']);service.monitor()
                        owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                        self.assertEqual(owner['strategy'],NORMAL_STRATEGY);self.assertEqual(owner['exit_timeframe'],'none')
                        stop=float(exchange.active['BTCUSDT']['stopLoss']);self.assertTrue(stop>100 if bearish else stop<100)
                        now[0]+=5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
                        service._close('BTCUSDT',exchange.active['BTCUSDT'],'Fixture close')
                        close=next(o for o in exchange.orders.values() if o['reduceOnly']);exchange.fill(close['orderLinkId']);service.monitor()
                        trade=service.history()['items'][0];self.assertEqual(trade['strategy'],NORMAL_STRATEGY)
                        self.assertEqual(trade['side'],side)
