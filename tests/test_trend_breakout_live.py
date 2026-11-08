import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.trend_breakout_live import qualify,evaluate_breakout,observe,recheck,STRATEGY
from thoth.trend_pullback import qualify as qualify_pullback,SUPPORTED
from thoth.verification import strategy_fingerprint
from test_testnet import Exchange
from test_trend_pullback_live import source,evidence,D


def level(side='Long'):return {'long_resistance_major' if side=='Long' else 'short_support_major':dict(value=100,pivot_time=0)}

def context(side='Long',now=402*D):
    e=evidence(side,now);e['source']='sr:long_resistance_major' if side=='Long' else 'sr:short_support_major'
    return dict(primary_timeframe='30',trend_breakout=e)

class BreakoutRulesTests(unittest.TestCase):
    def test_closed_cross_both_directions_prior_levels_and_no_research_sizing(self):
        for side in ('Long','Short'):
            rows=source(side)
            with patch('thoth.trend_breakout_live.IndicatorState.step',return_value={'levels':level(side)}) as step:
                result=qualify(rows,rows[-1]['close'],Settings(strategy=STRATEGY))
                self.assertEqual(result['signal'],side,result);self.assertEqual(step.call_count,len(rows)-1)
                self.assertTrue(result['source'].startswith('sr:'));self.assertLessEqual(result['distance_atr'],.25)
                self.assertNotIn('qty',result);self.assertNotIn('stop',result)
    def test_only_first_cross_not_equality_or_same_side_closes(self):
        lv=level()
        for previous,close,expected in [(99,100,None),(100,100,None),(100,100.1,'Long'),(100.1,100.2,None)]:
            self.assertEqual(evaluate_breakout(dict(close=close),previous,lv)['signal'],expected)
        self.assertEqual(evaluate_breakout(dict(close=99.9),100,level('Short'))['signal'],'Short')
        self.assertIsNone(evaluate_breakout(dict(close=99.8),99.9,level('Short'))['signal'])
    def test_minor_levels_and_bounce_only_setups_do_not_trigger_breakout(self):
        self.assertIsNone(evaluate_breakout(dict(close=100.1),99.9,{'long_resistance_minor':dict(value=100)})['signal'])
        cfg=Settings(strategy=STRATEGY);rows=source();bounce_levels={'long_support_major':dict(value=100,pivot_time=0)}
        with patch('thoth.trend_breakout_live.IndicatorState.step',return_value={'levels':bounce_levels}):
            self.assertIsNone(qualify(rows,rows[-1]['close'],cfg)['signal'])
            self.assertEqual(qualify_pullback(rows,rows[-1]['close'],Settings(strategy='trend_major_pullback_30m_close'))['signal'],'Long')
        with patch('thoth.trend_breakout_live.IndicatorState.step',return_value={'levels':level()}):
            self.assertEqual(qualify(rows,rows[-1]['close'],cfg)['signal'],'Long')
            self.assertIsNone(qualify_pullback(rows,rows[-1]['close'],Settings(strategy='trend_major_pullback_30m_close'))['signal'])
    def test_trend_alignment_distance_warmup_equality_and_invalid_price(self):
        cfg=Settings(strategy=STRATEGY);rows=source()
        self.assertIsNone(qualify(rows[:399],100,cfg)['signal'])
        flat=[{**r,'open':.1,'high':.11,'low':.09,'close':.1} for r in rows]
        self.assertEqual(qualify(flat,.1,cfg)['reason'],'Hourly close equals SMA200')
        with patch('thoth.trend_breakout_live.IndicatorState.step',return_value={'levels':level()}):
            for price in (99,102):self.assertEqual(qualify(rows,price,cfg)['reason'],'Entry outside allowed ATR distance')
            wrong=source('Short');wrong[-2]['close']=99.9;wrong[-1].update(open=100,high=100.2,low=99.9,close=100.01)
            self.assertEqual(qualify(wrong,100.01,cfg)['reason'],'No trend-aligned Major breakout')
        for price in (0,float('nan'),float('inf')):
            with self.assertRaises(ValueError):qualify(rows,price,cfg)
    def test_distinct_profile_and_context_cannot_consume_pullback_evidence(self):
        cfg=TestnetSettings(strategy=STRATEGY,timeframe='1',minor=True,major=False,sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True,position_value=50,stop_dollars=.15,opposite_action='close_only')
        self.assertEqual(cfg.timeframe,'30');self.assertTrue(cfg.major);self.assertFalse(cfg.minor or cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled)
        self.assertEqual(float(cfg.position_value),50);self.assertEqual(float(cfg.stop_dollars),.15);self.assertEqual(cfg.opposite_action,'close_only')
        self.assertNotEqual(strategy_fingerprint(cfg),strategy_fingerprint(Settings(strategy='trend_major_pullback_30m_close')))
        self.assertNotIn(STRATEGY,SUPPORTED) # Do not silently turn the bounce observer into breakout research.
        with self.assertRaises(ValueError):recheck('Long',dict(trend_pullback=evidence()),402*D+10,100.05)
        self.assertGreater(recheck('Long',context(),402*D+10,100.05)['distance_atr'],0)
        for now,price in [(402*D+121,100.05),(402*D+10,101),(402*D+10,99)]:
            with self.assertRaises(ValueError):recheck('Long',context(),now,price)

class BreakoutObservationTests(unittest.TestCase):
    def check(self,mode):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(SimpleNamespace(path=Path(tmp)/'live.sqlite'));cfg=Settings(strategy=STRATEGY);closed=source();now=[closed[-1]['time']+D-5]
            runner.observe_trend_pullback=lambda *a: (_ for _ in ()).throw(AssertionError('Breakout must not submit bounce research'))
            def packet():
                start=int(now[0])//D*D
                return [r for r in closed if r['time']<start]+[dict(time=start,open=100.01,close=100.01,high=100.2,low=99.9,volume=0)]
            with patch('thoth.trend_breakout_live.IndicatorState.step',return_value={'levels':level()}),patch('time.time',side_effect=lambda:now[0]):
                def step():
                    runner.scan_cycle+=1
                    return observe(runner,'BTCUSDT',cfg,packet(),now[0],now[0]-(31 if mode=='stale' and runner.scan_cycle>1 else 0))
                self.assertIsNone(step()['signal']);now[0]+=10
                if mode=='restart':runner.observation_session='restart'
                if mode=='gap':now[0]+=121
                if mode=='late':now[0]=closed[-1]['time']+D+121
                result=step()
                if mode=='normal':
                    self.assertEqual(result['signal'],'Long',result);self.assertEqual(result['entry_sources'],['htf:30:sr:long_resistance_major'])
                    self.assertIn('trend_breakout',result['entry_context']);self.assertNotIn('trend_pullback',result['entry_context'])
                    self.assertEqual(result['entry_context']['signal_stretch']['references'][0]['kind'],'broken_resistance')
                else:self.assertIsNone(result['signal'])
                now[0]+=5;self.assertIsNone(step()['signal'])
    def test_intrabar_cannot_enter_then_fresh_close_once(self):self.check('normal')
    def test_restart_baselines(self):self.check('restart')
    def test_scan_gap_baselines(self):self.check('gap')
    def test_late_window_skips(self):self.check('late')
    def test_stale_snapshot_skips(self):self.check('stale')

class BreakoutAccountTests(unittest.TestCase):
    def test_runner_feed_orders_protection_sizing_reverse_and_history(self):
        for action in ('reverse','close_only'):
            with tempfile.TemporaryDirectory() as tmp:
                closed=source();now=[closed[-1]['time']+D-5];exchange=Exchange(lambda:now[0]);exchange.prices['BTCUSDT']=100.05
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,position_value=50,stop_dollars=.15,opposite_action=action)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
                def fetch(symbol,tf,limit=1000,end=None):
                    self.assertEqual(tf,'30');start=int(now[0])//D*D
                    return [r for r in closed if r['time']<start]+[dict(time=start,open=100.05,high=100.2,low=99.9,close=100.05,volume=0)]
                with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.trend_breakout_live.IndicatorState.step',return_value={'levels':level()}),patch('time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                    runner.cycle(paced=False);self.assertFalse(exchange.orders)
                    now[0]+=10;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1,runner.health)
                    entry=next(iter(exchange.orders.values()));self.assertEqual(entry['side'],'Buy');self.assertLessEqual(float(entry['qty'])*100.05,50)
                    record=service.journal.records()[0];self.assertEqual(record['stop_dollars'],'0.15');self.assertIn('thoth_trend_breakout',record['instrument']);self.assertNotIn('thoth_trend_pullback',record['instrument'])
                    self.assertEqual(record['instrument']['thoth_exit_timeframe'],'none');exchange.fill(entry['orderLinkId']);service.monitor()
                    self.assertTrue(service.state['owned']['BTCUSDT']['protection_confirmed']);now[0]+=5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
                    settings=feed.snapshot()['settings'];now[0]+=5;exchange.prices['BTCUSDT']=99.95
                    service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal='Short',entry_sources=['htf:30:sr:short_support_major'],entry_context=context('Short',int(now[0])//D*D))})
                    closing=list(exchange.orders.values())[-1];self.assertTrue(closing['reduceOnly']);exchange.fill(closing['orderLinkId']);service.monitor()
                    trade=service.history()['items'][0];self.assertIn('trend_breakout',trade);self.assertIsNone(trade.get('trend_pullback'));self.assertIn('major resistance',trade['entry_source_label'])
                    self.assertEqual(len(exchange.orders),3 if action=='reverse' else 2)
    def test_fresh_submission_quote_cancels_overextended_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[402*D+10];exchange=Exchange(lambda:now[0]);exchange.prices['BTCUSDT']=100.05
            service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT']);service.configure(cfg);service.activate(service.confirmation)
            original=exchange.request;calls=[0]
            def request(method,path,params=None,**kwargs):
                if path=='/v5/market/tickers':
                    calls[0]+=1
                    if calls[0]>1:exchange.prices['BTCUSDT']=101
                return original(method,path,params,**kwargs)
            with patch.object(exchange,'request',side_effect=request):service._entry('BTCUSDT','Long',now[0],'fixture',cfg,context())
            self.assertFalse(exchange.orders);record=service.journal.records()[0]
            self.assertTrue(record['result']['not_submitted']);self.assertIn('Breakout submission outside',record['result']['reason'])
