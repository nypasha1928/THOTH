import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings, TestnetSettings
from thoth.sr_unfiltered import STRATEGY, observe
from thoth.runner import Runner
from thoth.timeframes import interval_seconds, INTRABAR_STRATEGIES
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange

BASE=1793000000//14400*14400+100

def rows(now,tf,price,previous=100,low=None,high=None):
    d=interval_seconds(tf);start=int(now)//d*d
    return [dict(time=start-d,open=previous,close=previous,low=previous,high=previous,volume=0),
            dict(time=start,open=100,close=price,low=price if low is None else low,high=price if high is None else high,volume=0)]

def levels(key,value):return {key:dict(value=value,pivot_time=0)}

class NativeSRTests(unittest.TestCase):
    def test_profile_no_filters_and_risk_preserved(self):
        cfg=TestnetSettings(strategy=STRATEGY,timeframe='1',major=False,minor=True,sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True,opposite_action='close_only',stop_dollars=.15,coin_pnl_trailing_activation_dollars=.15)
        self.assertEqual(cfg.timeframe,'240');self.assertTrue(cfg.major);self.assertFalse(cfg.minor)
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled)
        self.assertEqual(cfg.opposite_action,'close_only');self.assertEqual(str(cfg.stop_dollars),'0.15')
        self.assertIn(STRATEGY,INTRABAR_STRATEGIES)

    def test_breakout_zero_volume_first_cross_and_dedupe(self):
        cfg=Settings(strategy=STRATEGY);tracker={};lv=levels('long_resistance_major',101)
        def step(t,p):return observe(tracker,rows(t,'240',p),lv,cfg,t,30)
        self.assertIsNone(step(BASE,100)['signal'])
        self.assertIsNone(step(BASE+5,101)['signal'])
        r=step(BASE+10,102);self.assertEqual(r['signal'],'Long');self.assertEqual(r['entry_sources'],['sr:long_resistance_major'])
        step(BASE+15,100);self.assertIsNone(step(BASE+20,102)['signal'])

    def test_native_support_bounce_and_resistance_rejection_no_breakout(self):
        cfg=Settings(strategy=STRATEGY)
        for key,initial,current,previous,side,source in [('short_support_major',100,102,100,'Long','sr_bounce:'),('long_resistance_major',102,100,102,'Short','sr_rejection:')]:
            tracker={};lv=levels(key,101)
            observe(tracker,rows(BASE,'240',initial,previous,100,102),lv,cfg,BASE,30)
            result=observe(tracker,rows(BASE+5,'240',current,previous,100,102),lv,cfg,BASE+5,30)
            self.assertEqual(result['signal'],side);self.assertEqual(result['entry_sources'],[source+key])
            self.assertIsNone(observe(tracker,rows(BASE+10,'240',current,previous,100,102),lv,cfg,BASE+10,30)['signal'])

    def test_startup_gaps_new_levels_do_not_replay_and_minor_never_fires(self):
        cfg=Settings(strategy=STRATEGY)
        for mode in ('gap','level','minor','startup'):
            tracker={};lv=levels('long_resistance_minor' if mode=='minor' else 'long_resistance_major',101)
            observe(tracker,rows(BASE,'240',100),lv,cfg,BASE,30)
            if mode=='level':lv=levels('long_resistance_major',101.5)
            if mode=='startup':tracker={}
            t=BASE+(60 if mode=='gap' else 5)
            self.assertIsNone(observe(tracker,rows(t,'240',102),lv,cfg,t,30)['signal'])

    def test_current_and_previous_candles_required(self):
        with self.assertRaises(ValueError):observe({},rows(BASE,'240',100)[-1:],{},Settings(strategy=STRATEGY),BASE,30)

class RunnerSRTests(unittest.TestCase):
    def run_case(self,mode):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(type('Engine',(),{'path':str(Path(tmp)/'live.sqlite')})());cfg=Settings(strategy=STRATEGY)
            now=[float(BASE)];prices={'240':100,'D':100};lv={'240':levels('long_resistance_major',101),'D':levels('long_resistance_major',101)}
            if mode=='conflict':lv['D']=levels('short_support_major',99)
            if mode=='bounce':lv['D']=levels('short_support_major',101)
            def analysis(symbol,c,history,t,**kw):
                d=interval_seconds(c.timeframe);return [dict(time=int(t)//d*d-d,levels=lv[c.timeframe])],300
            with patch.object(runner,'history',side_effect=lambda s,t,r:r),patch.object(runner,'analysis',side_effect=analysis),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch.object(runner,'rsi_preview',side_effect=AssertionError('No RSI')):
                def step():
                    runner.scan_cycle+=1
                    packet=dict(primary={tf:rows(now[0],tf,prices[tf],low=100 if mode=='bounce' else None) for tf in prices},sample_received_at=now[0]-(31 if mode=='stale' else 0))
                    if mode=='partial':packet['primary']['240']=ValueError('missing 4h')
                    result=runner.sr_unfiltered_observation('BTCUSDT',cfg,packet,now[0]);now[0]+=5;return result
                self.assertIsNone(step()['signal']);prices.update({'240':102,'D':98 if mode=='conflict' else 102})
                result=step()
                if mode in ('conflict','stale'):
                    self.assertIsNone(result['signal'])
                    if mode=='conflict':self.assertTrue(result['signal_diagnostic']['conflict'])
                    else:self.assertEqual(set(result['source_errors']),{'240','D'})
                else:
                    self.assertEqual(result['signal'],'Long',result)
                    expected={'htf:D:sr_bounce:short_support_major'} if mode=='bounce' else {'htf:D:sr:long_resistance_major'}
                    if mode!='partial':expected.add('htf:240:sr:long_resistance_major')
                    self.assertEqual(set(result['entry_sources']),expected)
                    self.assertEqual(result['entry_context']['primary_timeframe'],'D' if mode=='partial' else '240')
                    self.assertFalse(result['volume_checks'])
                    self.assertIsNone(step()['signal'])
                    runner.observation_session='restart';prices.update({'240':98,'D':98});self.assertIsNone(step()['signal'])
    def test_independent_timeframes_provenance_and_restart(self):self.run_case('normal')
    def test_cross_frame_conflict_skips(self):self.run_case('conflict')
    def test_daily_bounce_with_4h_breakout(self):self.run_case('bounce')
    def test_one_frame_unavailable_other_can_trigger(self):self.run_case('partial')
    def test_stale_snapshots_never_trade(self):self.run_case('stale')

class ExecutionSRTests(unittest.TestCase):
    def test_full_runner_feed_dispatches_both_frames_and_submits_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[float(BASE)];price=[100];exchange=Exchange(lambda:now[0])
            service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            service.configure(TestnetSettings(strategy=STRATEGY,dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT']));service.activate(service.confirmation)
            feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
            def analysis(symbol,c,history,t,**kw):
                d=interval_seconds(c.timeframe);return [dict(time=int(t)//d*d-d,levels=levels('long_resistance_major',101))],300
            with patch.object(feed,'staged_primary_inputs',side_effect=lambda symbol,tf:rows(now[0],tf,price[0])) as downloads,patch.object(runner,'history',side_effect=lambda s,t,r:r),patch.object(runner,'analysis',side_effect=analysis),patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False);self.assertFalse(exchange.orders)
                now[0]+=5;price[0]=102;runner.cycle(paced=False)
                self.assertEqual(len(exchange.orders),1,runner.health)
                self.assertEqual({call.args[1] for call in downloads.call_args_list},{'240','D'})
                record=service.journal.records()[0]
                self.assertEqual(set(record['instrument']['thoth_entry_sources']),{'htf:240:sr:long_resistance_major','htf:D:sr:long_resistance_major'})
                self.assertIn('frames',record['instrument']['thoth_sr_unfiltered'])

    def test_protected_entry_no_minute_exit_selected_opposite_action_and_retained_evidence(self):
        for action in ('reverse','close_only'):
            with tempfile.TemporaryDirectory() as tmp:
                now=[float(BASE)];exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT'],opposite_action=action)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service);settings=feed.snapshot()['settings']
                with patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                    for side in (None,'Long'):
                        now[0]+=5;service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal=side,entry_sources=['htf:D:sr_bounce:short_support_major'],entry_context=dict(primary_timeframe='D',sr_unfiltered={'frames':{'D':{'observed_at':now[0]}}}))})
                    order=next(iter(exchange.orders.values()));exchange.fill(order['orderLinkId']);service.monitor()
                    owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed']);self.assertEqual(owner['exit_timeframe'],'none');self.assertIn('D',owner['sr_unfiltered']['frames'])
                    now[0]+=5;service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal='Short',entry_sources=['htf:240:sr:long_support_major'])})
                    self.assertEqual('BTCUSDT' in service.state['reversals'],action=='reverse')
                    closing=list(exchange.orders.values())[-1];self.assertTrue(closing['reduceOnly']);exchange.fill(closing['orderLinkId']);service.monitor()
                    trade=service.history()['items'][0];self.assertIn('D',trade['sr_unfiltered']['frames']);self.assertIn('Bounce',trade['entry_source_label'])
