"""Executable early-hour strategy: synthetic candles/exchange only."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.early_hour import STRATEGY,window,rules,context,recheck
from thoth.late_hour_comparison import SourceState,decision
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.signal_stretch import with_fill
from test_late_hour_comparison import BAR,history,raw
from test_testnet import Exchange
from test_closed_inverted_ob_sr import history as formation_history,NOW

class EarlyHourTests(unittest.TestCase):
    def cfg(self,**kw):return Settings(strategy=STRATEGY,pivot_long=1,pivot_short=1,**kw)
    def test_profile_keeps_risk_and_action_but_fixes_hour_and_major_sources(self):
        c=self.cfg(timeframe='D',sma_enabled=True,sma_exit_enabled=True,major=False,minor=True,opposite_action='close_only',stop_dollars=.4)
        self.assertEqual(c.timeframe,'60');self.assertTrue(c.major);self.assertFalse(c.minor or c.sma_enabled or c.sma_exit_enabled or c.order_block.filter_breaks)
        self.assertEqual(c.opposite_action,'close_only');self.assertEqual(c.stop_dollars,.4)
        self.assertEqual(rules(c)['maximum_distance_atr'],.5)
        with self.assertRaises(ValueError):self.cfg(early_min_distance_atr=1,early_max_distance_atr=.5)
        for t,expected in [(3299,False),(3300,True),(3599.9,True),(3600,False)]:self.assertEqual(window(BAR+t),expected)

    def test_checkpoint_recheck_matches_full_history_and_rejects_revision_expiry_settings(self):
        c=self.cfg();rows=history();at=BAR+3301;s=SourceState(c);s.advance(rows,at);ctx=context(s,rows,c,at)
        with patch('thoth.late_hour_comparison.select',return_value=raw()):
            full=decision(rows,c,at,at,s,rules(c));small=recheck(rows[-2:],c,'Long',ctx,at)
            self.assertEqual(full['signal'],small['signal']);self.assertEqual(full['metrics'],small['metrics'])
            revised=copy.deepcopy(rows[-2:]);revised[0]['close']+=.01
            for data,cfg,stamp in [(revised,c,at),(rows[-2:],c,BAR+3600),(rows[-2:],self.cfg(early_max_distance_atr=.6),at)]:
                with self.assertRaises(ValueError):recheck(data,cfg,'Long',ctx,stamp)
            with self.assertRaises(ValueError):recheck(rows[-2:],c,'Short',ctx,at)

    def test_all_four_sources_and_custom_limits(self):
        c=self.cfg()
        for kind,side in [('bullish_formed','Long'),('supply_breakout','Long'),('bearish_formed','Short'),('demand_breakdown','Short')]:
            rows=history(close=100.5 if side=='Long' else 99.5);at=BAR+3301;s=SourceState(c);s.advance(rows,at)
            with patch('thoth.late_hour_comparison.select',return_value=raw(kind,side)):
                result=recheck(rows[-2:],c,side,context(s,rows,c,at),at)
                self.assertEqual(result['passed_sources'],['ob:'+kind]);self.assertTrue(result['metrics']['provisional'])
        strict=self.cfg(early_max_opposing_wick_pct=10);s=SourceState(strict);s.advance(history(),BAR+3301)
        with patch('thoth.late_hour_comparison.select',return_value=raw()):
            with self.assertRaises(ValueError):recheck(history()[-2:],strict,'Long',context(s,history(),strict,BAR+3301),BAR+3301)

    def test_major_breakouts_only_conflicts_and_faded_recheck(self):
        c=self.cfg();rows=history();at=BAR+3301;s=SourceState(c);s.advance(rows,at)
        s.sr_report['levels']={'long_resistance_major':dict(value=100,pivot_time=BAR-3600),'short_resistance_minor':dict(value=100,pivot_time=BAR-3600)}
        quiet=dict(ready=True,signal=None,conflict=False,events=[],entry_sources=[])
        with patch('thoth.late_hour_comparison.select',return_value=quiet):
            ctx=context(s,rows,c,at);result=recheck(rows[-2:],c,'Long',ctx,at)
            self.assertEqual(result['passed_sources'],['sr:long_resistance_major'])
            faded=copy.deepcopy(rows[-2:]);faded[-1]['close']=100
            with self.assertRaises(ValueError):recheck(faded,c,'Long',ctx,at)
        with patch('thoth.late_hour_comparison.select',return_value=raw('bearish_formed','Short')):
            with self.assertRaises(ValueError):recheck(rows[-2:],c,'Long',ctx,at)

    def test_runner_continuity_once_per_hour_restart_and_outside_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+3299];exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'live.sqlite',client_factory=lambda *a,**k:exchange,clock=lambda:now[0])
            runner=Runner(FeedAdapter(service));c=self.cfg()
            runner.scan_cycle=1
            with patch('thoth.late_hour_comparison.select',return_value=raw()):
                self.assertIsNone(runner.early_hour_observation('BTCUSDT',c,history(),now[0])['signal'])
                runner.scan_cycle+=1;now[0]+=2
                self.assertEqual(runner.early_hour_observation('BTCUSDT',c,history(),now[0])['signal'],'Long')
                runner.scan_cycle+=1;now[0]+=1
                self.assertIsNone(runner.early_hour_observation('BTCUSDT',c,history(),now[0])['signal'])
                runner.observation_session='restart';runner.scan_cycle+=1;now[0]+=1
                self.assertIsNone(runner.early_hour_observation('BTCUSDT',c,history(),now[0])['signal'])

    def test_real_formation_pipeline_submit_protect_durable_cap_and_no_bar_close_wait(self):
        for bearish in (False,True):
            with self.subTest(bearish=bearish),tempfile.TemporaryDirectory() as tmp:
                now=[NOW-301];exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,early_min_distance_atr=0,early_max_distance_atr=20,early_max_opposing_wick_pct=100)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch);data=formation_history(bearish)
                def fetch(symbol,interval,limit=1000):return data[-limit:]
                with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No separate SMA exit')):
                    runner.cycle(paced=False);self.assertFalse(exchange.orders)
                    now[0]=NOW-299;runner.cycle(paced=False)
                    self.assertTrue(exchange.orders,(runner.health,service.state['events'][-4:]))
                    order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],'Sell' if bearish else 'Buy')
                    exchange.fill(order['orderLinkId']);service.monitor();owner=service.state['owned']['BTCUSDT']
                    self.assertTrue(owner['protection_confirmed']);self.assertEqual(owner['exit_timeframe'],'none')
                    self.assertTrue(owner['signal_stretch']['provisional']);self.assertNotIn('close_to_fill_observed_seconds',owner['signal_stretch'])
                    self.assertTrue(service._early_hour_used('BTCUSDT',cfg))
                    service.state.pop('early_entry_windows',None)
                    self.assertTrue(service._early_hour_used('BTCUSDT',cfg)) # Durable journal, not memory only.
                    now[0]+=2;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
                    now[0]=NOW;service._entry('OTHERUSDT','Long',now[0],'expired',cfg,{})
                    self.assertEqual(len(exchange.orders),1)

    def test_provisional_fill_labels_use_snapshot_time(self):
        m=dict(provisional=True,side='Long',ohlc=dict(close=100),references=[],closed_at=3600,observed_at=3300)
        result=with_fill(m,dict(fill_price=101,fill_observed_at=3305))
        self.assertEqual(result['snapshot_to_fill_observed_seconds'],5);self.assertNotIn('close_to_fill_observed_seconds',result)

    def test_shared_reversal_settles_before_reentry_and_respects_window(self):
        for action,late in [('reverse',False),('close_only',False),('reverse',True)]:
            with self.subTest(action=action,late=late),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+3301];exchange=Exchange(lambda:now[0])
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],pivot_long=1,pivot_short=1,dedicated_account=True,daily_loss_pct=90,opposite_action=action)
                cfg=TestnetSettings(**cfg.model_dump())
                service.configure(cfg);service.activate(service.confirmation)
                rows=history();s=SourceState(cfg);s.advance(rows,now[0]);ctx=context(s,rows,cfg,now[0])
                with patch.object(exchange,'candles',return_value=rows[-2:]),patch('thoth.late_hour_comparison.select',return_value=raw()):
                    service.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=now[0]-1,signal=None,mark=100)})
                    service.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=now[0],signal='Long',mark=100,entry_context=ctx)})
                self.assertTrue(exchange.orders,(service.state['events'][-8:],service.state['cursors'],{k:(v,service.state['config'].get(k)) for k,v in cfg.model_dump().items() if v!=service.state['config'].get(k)}))
                first=next(iter(exchange.orders));exchange.fill(first);service.monitor()
                now[0]=BAR+3600+(3590 if late else 3301)
                rows=[{**r,'time':r['time']+3600} for r in history(close=99.5)]
                s=SourceState(cfg);s.advance(rows,now[0]);ctx=context(s,rows,cfg,now[0]);stamp=now[0]
                with patch.object(exchange,'candles',return_value=rows[-2:]),patch('thoth.late_hour_comparison.select',return_value=raw('bearish_formed','Short')):
                    service.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=stamp,signal='Short',mark=100,entry_context=ctx)})
                    closes=[o for o in exchange.orders.values() if o['reduceOnly']]
                    self.assertEqual(len(closes),1);self.assertEqual(len(exchange.orders),2)
                    if late:now[0]=BAR+7200
                    exchange.fill(closes[0]['orderLinkId']);service.monitor()
                    now[0]+=1
                    service.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=now[0],signal=None,mark=100)})
                    entries=[o for o in exchange.orders.values() if not o['reduceOnly']]
                    self.assertEqual(len(entries),2 if action=='reverse' and not late else 1,service.state['events'][-8:])
                    if len(entries)==2:self.assertEqual(entries[-1]['side'],'Sell')

    def test_final_recheck_blocks_fading_signal_or_preflight_crossing_hour(self):
        for late in (False,True):
            with self.subTest(late=late),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+3598];exchange=Exchange(lambda:now[0])
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],pivot_long=1,pivot_short=1,dedicated_account=True,daily_loss_pct=90)
                service.configure(cfg);service.activate(service.confirmation)
                rows=history();state=SourceState(cfg);state.advance(rows,now[0]);ctx=context(state,rows,cfg,now[0])
                def fresh(*a,**kw):
                    if late:now[0]=BAR+3600
                    return history(close=100 if not late else 100.5)[-2:]
                with patch.object(exchange,'candles',side_effect=fresh),patch('thoth.late_hour_comparison.select',return_value=raw()):
                    service._entry('BTCUSDT','Long',BAR+3598,'fixture',cfg,ctx)
                self.assertFalse(exchange.orders)
