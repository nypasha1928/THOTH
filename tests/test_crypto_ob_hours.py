"""Synthetic execution tests; no exchange connections or live credentials."""
import json,tempfile,unittest
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from thoth.crypto_ob_hours import STRATEGY,LIST,THRESHOLDS,EXIT_THRESHOLDS,POLICY,StrengthState,validate_entry,validate_exit,entry_block_reason
from thoth.crypto_ob_strength import THRESHOLDS as OLD_THRESHOLDS,apply_exit_preset
from thoth.crypto_ob_sma200 import STRATEGY as OLD_STRATEGY
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.indicator import evaluate
from thoth.verification import strategy_fingerprint
from test_testnet import Exchange
from test_crypto_ob_strength import candles,D
from test_crypto_ob_sma200 import context as old_context
NY=ZoneInfo('America/New_York')
def stamp(hour,month=11,day=19,minute=0,second=5,fold=0):
    return datetime(2026,month,day,hour,minute,second,tzinfo=NY,fold=fold).timestamp()
def context(now,side='Long',width=8,sma=90):
    result=old_context(side,width,sma,int(now)//D*D-D)
    result['crypto_ob_strength']['strategy']=STRATEGY
    return result

class PolicyTests(unittest.TestCase):
    def test_exact_research_universe_and_old_thresholds_unchanged(self):
        self.assertEqual(len(THRESHOLDS),50)
        self.assertEqual({s:THRESHOLDS[s] for s in OLD_THRESHOLDS},OLD_THRESHOLDS)
        self.assertEqual(THRESHOLDS['BTCUSDT'],254.4)
        self.assertEqual({s for s,v in THRESHOLDS.items() if v==0},{'INJUSDT','WLDUSDT'})
        self.assertEqual(EXIT_THRESHOLDS['GRTUSDT'],.00032)
        cfg=Settings(strategy=STRATEGY,symbols=list(THRESHOLDS),timeframe='1',opposite_action='reverse',coin_breakeven_enabled=True)
        self.assertEqual((cfg.timeframe,cfg.opposite_action,cfg.stop_dollars),('60','close_only',.5))
        self.assertFalse(cfg.coin_breakeven_enabled)
        self.assertEqual(set(cfg.saved_symbol_lists[LIST]),set(THRESHOLDS))
        with self.assertRaises(ValueError):Settings(strategy=OLD_STRATEGY,symbols=['BTCUSDT'])
        with self.assertRaises(ValueError):Settings(strategy=STRATEGY,symbols=['NVDAUSDT'])
    def test_all_hours_both_dst_offsets_and_exact_boundaries(self):
        for month in (7,11):
            for hour in range(24):
                for minute,second in [(0,0),(59,59)]:
                    now=stamp(hour,month=month,minute=minute,second=second)
                    self.assertEqual(bool(entry_block_reason(now)),hour in (5,8,10,12,21,23))
                    ctx=context(now)
                    if entry_block_reason(now):
                        with self.assertRaisesRegex(ValueError,'entry-hour exclusion'):validate_entry('ETHUSDT','Long',ctx,now)
                    else:validate_entry('ETHUSDT','Long',ctx,now)
                    validate_exit('ETHUSDT','Long',ctx,now)
        for fold in (0,1):self.assertIsNone(entry_block_reason(stamp(1,month=11,day=1,fold=fold)))
    def test_exit_never_needs_hour_permission_or_sma_entry_permission(self):
        for hour in POLICY['blocked_entry_hours']:
            now=stamp(hour);ctx=context(now,'Short',7.3,90)
            validate_exit('ETHUSDT','Short',ctx,now)
            with self.assertRaises(ValueError):validate_entry('ETHUSDT','Short',ctx,now)
        now=stamp(14)
        for side,sma in [('Long',100),('Long',None),('Long',110),('Short',90)]:
            with self.assertRaises(ValueError):validate_entry('ETHUSDT',side,context(now,side=side,sma=sma),now)
        with self.assertRaises(ValueError):validate_exit('ETHUSDT','Long',old_context(),now)
    def test_preset_is_explicit_once_and_separate_from_existing_profiles(self):
        cfg=TestnetSettings(strategy=STRATEGY,symbols=['ETHUSDT'],leverage=18,daily_loss_pct=35,position_value=20,max_positions=3)
        self.assertFalse(cfg.crypto_ob_hours_preset_applied)
        saved=apply_exit_preset(cfg)
        self.assertEqual((saved.position_value,saved.max_positions,saved.stop_dollars),(50,10,.5))
        self.assertEqual((saved.coin_pnl_trailing_activation_dollars,saved.coin_pnl_trailing_drop_dollars),(2.5,.1))
        self.assertTrue(saved.coin_pnl_trailing_enabled)
        self.assertFalse(saved.pnl_trailing_enabled or saved.coin_breakeven_enabled)
        self.assertEqual((saved.leverage,saved.daily_loss_pct),(18,35))
        edited=TestnetSettings(**{**saved.model_dump(),'max_positions':4,'coin_pnl_trailing_activation_dollars':3})
        self.assertEqual(apply_exit_preset(edited).max_positions,4)
        self.assertEqual(apply_exit_preset(edited).coin_pnl_trailing_activation_dollars,3)
        self.assertEqual(apply_exit_preset(TestnetSettings(strategy=OLD_STRATEGY,symbols=['ETHUSDT'])).coin_pnl_trailing_activation_dollars,5)
    def test_completed_hour_state_checkpoint_and_identity(self):
        rows=candles(270);state=StrengthState('ETHUSDT');out=[]
        for i,row in enumerate(rows):
            if i%23==0:state=StrengthState('ETHUSDT',json.loads(json.dumps(state.checkpoint())))
            out.append(state.step(row))
        cfg=Settings(strategy=STRATEGY,symbols=['ETHUSDT'])
        self.assertEqual(out,evaluate(rows,cfg,rows[-1]['time']+D,symbol='ETHUSDT'))
        self.assertEqual(len(evaluate(rows,cfg,rows[-1]['time']+D-1,symbol='ETHUSDT')),len(rows)-1)
        self.assertTrue(all(x['crypto_ob_strength']['sma200'] is None for x in out[:199]))
        self.assertNotEqual(strategy_fingerprint(cfg),strategy_fingerprint(Settings(strategy=OLD_STRATEGY,symbols=['ETHUSDT'])))
        with self.assertRaises(ValueError):StrengthState('BTCUSDT',state.checkpoint())

class ExecutionTests(unittest.TestCase):
    def service(self,tmp,now):
        exchange=Exchange(lambda:now[0])
        service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,
            client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        service.configure(TestnetSettings(strategy=STRATEGY,symbols=['ETHUSDT'],dedicated_account=True,daily_loss_pct=90,consecutive_losses=99))
        service.activate(service.confirmation)
        cfg=Settings(**{k:v for k,v in service.state['config'].items() if k in Settings.model_fields}).model_dump()
        return service,exchange,cfg
    def test_blocked_entry_despite_general_schedule_off_and_no_replay(self):
        now=[stamp(4)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            def apply(ctx,side='Long'):
                bar=int(now[0])//D*D-D
                service.apply_signals(cfg,{'ETHUSDT':dict(time=bar,mark=100,signal=side,entry_context=ctx)})
            apply(None,None) # baseline consumed, no replay of historic signals
            now[0]=stamp(5);apply(context(now[0]))
            self.assertFalse(exchange.orders)
            self.assertFalse(service.state['pending_signals'])
            self.assertIn('entry-hour exclusion',str(service.state['events']))
            now[0]=stamp(6)
            service.apply_signals(cfg,{'ETHUSDT':dict(time=int(stamp(5))//D*D-D,mark=100,signal='Long',entry_context=context(stamp(5)))})
            self.assertFalse(exchange.orders) # blocked05 signal never catches up at06
            apply(context(now[0]));self.assertEqual(len(exchange.orders),1)
            self.assertFalse(service.state['config']['entry_daily_enabled'] or service.state['config']['entry_weekend_enabled'])
    def test_held_opposite_exit_during_blocked_hour_no_reversal(self):
        now=[stamp(4)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'fixture',TestnetSettings(**service.state['config']),context(now[0]))
            self.assertEqual(len(exchange.orders),1)
            exchange.fill(list(exchange.orders.values())[-1]['orderLinkId']);now[0]+=.1;service.monitor()
            # Establish a runner cursor for this strategy before next hour.
            service.apply_signals(cfg,{'ETHUSDT':dict(time=int(now[0])//D*D-D,mark=100,signal=None)})
            now[0]=stamp(5);ctx=context(now[0],'Short',7.3,90)
            service.apply_signals(cfg,{'ETHUSDT':dict(time=int(now[0])//D*D-D,mark=100,signal='Short',entry_context=ctx)})
            self.assertEqual(len(exchange.orders),2,service.state['events'][-5:])
            self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly'])
            self.assertFalse(service.state['reversals'])
            exchange.fill(list(exchange.orders.values())[-1]['orderLinkId']);now[0]+=.1;service.monitor()
            self.assertFalse(service.state['owned'])
            self.assertEqual(service.history()['items'][0]['crypto_ob_strength']['exit_evidence'],ctx['crypto_ob_strength'])
    def test_direct_entry_and_submission_guard_recheck(self):
        now=[stamp(5)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            settings=TestnetSettings(**service.state['config'])
            service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'blocked',settings,context(now[0]))
            self.assertFalse(exchange.orders)
            now[0]=stamp(4,minute=59,second=59);ctx=context(now[0]);submit=service.journal.submit;guards=[]
            def delayed(*args,**kwargs):
                now[0]=stamp(5)
                guards.append(kwargs['entry_guard']())
                return submit(*args,**kwargs)
            with patch.object(service.journal,'submit',side_effect=delayed):
                service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'boundary',settings,ctx)
            self.assertEqual(len(guards),1)
            self.assertTrue(guards[0]);self.assertFalse(exchange.orders)
    def test_switch_from_current_sma_profile_preserves_protected_position(self):
        now=[stamp(4)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now);service.pause()
            service.configure(TestnetSettings(**{**service.state['config'],'strategy':OLD_STRATEGY}))
            service.activate(service.confirmation)
            ctx=context(now[0]);ctx['crypto_ob_strength']['strategy']=OLD_STRATEGY
            service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'old',TestnetSettings(**service.state['config']),ctx)
            exchange.fill(list(exchange.orders.values())[-1]['orderLinkId']);now[0]+=.1;service.monitor()
            owner=deepcopy(service.state['owned']['ETHUSDT']);active=deepcopy(exchange.active)
            service.pause();service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))
            saved=service.state['owned']['ETHUSDT']
            self.assertEqual(saved['link'],owner['link']);self.assertEqual(saved['stop_dollars'],owner['stop_dollars'])
            self.assertEqual(saved['strategy'],OLD_STRATEGY)
            self.assertEqual(saved['crypto_ob_strength']['exit_policy']['strategy'],STRATEGY)
            self.assertEqual(len(exchange.orders),1);self.assertEqual(exchange.active,active)
            # A close already settling forbids this transition.
            service.state['config']['strategy']=OLD_STRATEGY
            service.state['owned']['ETHUSDT']['close_link']='pending-close';service.save()
            with self.assertRaises(ValueError):service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))

    def test_runner_reports_closed_profile_with_distinct_policy(self):
        now=[stamp(14)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            self.assertEqual(service.snapshot()['entry_filters']['crypto_ob_hours']['blocked_entry_hours'],[5,8,10,12,21,23])
            self.assertTrue(service.snapshot()['entry_filters']['sma200']['enabled'])
            self.assertFalse(service.snapshot()['entry_filters']['crypto_ob_sma200']['enabled'])
            rows=candles(270);feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
            now[0]=rows[-1]['time']+D+5
            with patch.object(exchange,'candles',return_value=rows),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                runner.cycle(paced=False)
                self.assertIn('New York',runner.health['note'])
                self.assertEqual(runner.health['symbols']['ETHUSDT']['bar'],rows[-1]['time'])
            self.assertFalse(exchange.orders) # recovery/first-observation baseline

if __name__=='__main__':unittest.main()
