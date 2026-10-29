"""Independent 4h entry branches, common volume gate, no production calls."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.order_block_sr import combine,STRATEGY
from test_testnet import Exchange
from test_order_block import formation_rows

NOW=1793000000.


def quiet(now=NOW,price=100,passing=True):
    start=int(now)//14400*14400
    return [dict(time=start-(59-i)*14400,open=100,close=price if i==59 else 100,
        high=max(101,price+1) if i==59 else 101,low=min(99,price-1) if i==59 else 99,
        volume=40 if i==59 and passing else 10) for i in range(60)]


def ob_rows(now=NOW,trigger=True):
    source=formation_rows();start=int(now)//14400*14400
    if not trigger:source[-1].update(close=99,high=100)
    return [{**row,'time':start-(len(source)-i-1)*14400,'volume':40 if i==len(source)-1 else 10} for i,row in enumerate(source)]


def levels_analysis(key,value):
    def analysis(symbol,cfg,history,now,**kwargs):
        return [dict(time=int(now)//14400*14400-14400,levels={key:dict(value=value,pivot_time=0)})],len(history)-1
    return analysis


class CombinedOrderBlockSRTests(unittest.TestCase):
    def test_profile_requires_both_sr_families_4h_volume20_and_retains_risk_controls(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',major=False,minor=False,sr_volume_period=50,
            sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True,
            daily_loss_pct=35,portfolio_loss_enabled=True,portfolio_loss_pct=25,
            pnl_trailing_enabled=True,pnl_trailing_activation_dollars=20,pnl_trailing_drop_dollars=1,
            daily_equity_trailing_enabled=True,daily_equity_trailing_drop_dollars=2,
            opposite_action='close_only',stop_dollars=1)
        self.assertEqual(cfg.timeframe,'240');self.assertTrue(cfg.major and not cfg.minor)
        self.assertEqual(cfg.sr_volume_period,20);self.assertEqual(cfg.opposite_action,'close_only')
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled or cfg.order_block.filter_breaks)
        self.assertEqual(cfg.daily_loss_pct,35);self.assertEqual(cfg.portfolio_loss_pct,25);self.assertTrue(cfg.portfolio_loss_enabled)
        self.assertEqual(cfg.pnl_trailing_activation_dollars,20);self.assertEqual(cfg.pnl_trailing_drop_dollars,1)
        self.assertTrue(cfg.daily_equity_trailing_enabled);self.assertEqual(cfg.daily_equity_trailing_drop_dollars,2)

    def test_merge_independent_agreeing_and_opposing_sources_with_volume_required(self):
        def branch(side,source,ob=False,passed=True):
            report=dict(passed=passed,reason='volume passed MA' if passed else 'volume did not exceed MA')
            return dict(signal=side,entry_sources=[source],**({'volume_checks':{'240':report}} if ob else {'volume_check':report}))
        long_ob=branch('Long','ob:bullish_formed',True);long_sr=branch('Long','sr:long_resistance_major')
        self.assertEqual(combine(long_ob,{},NOW,100)['signal'],'Long')
        self.assertEqual(combine({},long_sr,NOW,100)['signal'],'Long')
        agreeing=combine(long_ob,long_sr,NOW,100)
        self.assertEqual(agreeing['entry_sources'],['ob:bullish_formed','sr:long_resistance_major'])
        self.assertEqual(agreeing['signal'],'Long')
        opposing=combine(long_ob,branch('Short','sr:short_support_major'),NOW,100)
        self.assertIsNone(opposing['signal']);self.assertEqual(opposing['entry_sources'],[])
        self.assertIn('conflicting',opposing['signal_diagnostic']['reason'])
        self.assertIsNone(combine(branch('Long','ob:bullish_formed',True,False),{},NOW,100)['signal'])

    def test_every_major_minor_sr_family_qualifies_only_after_volume_no_ob_required(self):
        for term in ('long','short'):
            for family in ('major',):
                for kind,side,value,price in (('resistance','Long',101,102),('support','Short',99,98)):
                    key=term+'_'+kind+'_'+family
                    with self.subTest(key=key),tempfile.TemporaryDirectory() as tmp:
                        cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')))
                        with patch.object(runner,'analysis',side_effect=levels_analysis(key,value)):
                            runner.scan_cycle=1
                            self.assertIsNone(runner.combined_order_block_sr_observation('BTCUSDT',cfg,quiet(passing=False),NOW)['signal'])
                            runner.scan_cycle=2
                            low=runner.combined_order_block_sr_observation('BTCUSDT',cfg,quiet(price=price,passing=False),NOW+5)
                            self.assertIsNone(low['signal']);self.assertEqual(low['entry_sources'],[])
                            runner.scan_cycle=3
                            high=runner.combined_order_block_sr_observation('BTCUSDT',cfg,quiet(price=price),NOW+10)
                            self.assertEqual(high['signal'],side,high);self.assertEqual(high['entry_sources'],['sr:'+key])
                            runner.scan_cycle=4
                            self.assertIsNone(runner.combined_order_block_sr_observation('BTCUSDT',cfg,quiet(price=price),NOW+15)['signal'])
                            restarted=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')));restarted.scan_cycle=1
                            with patch.object(restarted,'analysis',side_effect=levels_analysis(key,value)):
                                self.assertIsNone(restarted.combined_order_block_sr_observation('BTCUSDT',cfg,quiet(price=price),NOW+20)['signal'])

    def test_worker_either_branch_opens_protected_position_with_only_4h_reads(self):
        for branch in ('ob','sr'):
            with self.subTest(branch=branch),tempfile.TemporaryDirectory() as tmp:
                now=[NOW];trigger=[False];exchange=Exchange(lambda:now[0]);calls=[]
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service)
                def fetch(symbol,interval,limit=1000):
                    calls.append((interval,limit))
                    data=ob_rows(now[0],trigger[0]) if branch=='ob' else quiet(now[0],102 if trigger[0] else 100)
                    return data[-limit:]
                runner=Runner(feed,fetch=feed.fetch)
                with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                    if branch=='sr':runner.analysis=levels_analysis('short_resistance_major',101)
                    runner.cycle(paced=False);self.assertFalse(exchange.orders)
                    now[0]+=5;trigger[0]=True;runner.cycle(paced=False)
                    order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],'Buy')
                    self.assertEqual(calls,[('240',1000),('240',60),('240',20)])
                    exchange.fill(order['orderLinkId']);service.monitor()
                    owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                    self.assertEqual(owner['entry_timeframe'],'240');self.assertEqual(owner['exit_timeframe'],'none')
                    self.assertIn('ob:bullish_formed' if branch=='ob' else 'sr:short_resistance_major',owner['entry_sources'])
                    self.assertEqual(service.state['entry_gates'],{})
                    service._close('BTCUSDT',exchange.active['BTCUSDT'],'Fixture close')
                    close=next(order for order in exchange.orders.values() if order['reduceOnly']);exchange.fill(close['orderLinkId']);service.monitor()
                    trade=service.history()['items'][0];self.assertEqual(trade['strategy'],STRATEGY)
                    self.assertIn('Order Block' if branch=='ob' else 'major',trade['entry_source_label'])

    def test_combined_profile_cannot_submit_if_volume_recheck_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[NOW];trigger=[False];exchange=Exchange(lambda:now[0])
            service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service)
            def fetch(symbol,interval,limit=1000):return quiet(now[0],102 if trigger[0] else 100,passing=limit!=20)[-limit:]
            runner=Runner(feed,fetch=feed.fetch);runner.analysis=levels_analysis('long_resistance_major',101)
            with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False);now[0]+=5;trigger[0]=True;runner.cycle(paced=False)
            self.assertFalse(exchange.orders);self.assertTrue(service.state['entries'])
            self.assertIn('Volume must exceed MA 20',service.state['events'][-1]['message'])
