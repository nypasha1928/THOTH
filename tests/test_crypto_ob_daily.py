"""Daily entry confirmation with independent closed-hour position management."""
import json, tempfile, unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from thoth.crypto_ob_daily import (
    STRATEGY, POLICY, StrengthState, daily_confirmation, validate_entry, validate_exit,
    THRESHOLDS, EXIT_THRESHOLDS, ENTRY_THRESHOLDS, LIST,
)
from thoth.crypto_ob_hours import STRATEGY as HOURS
from thoth.crypto_ob_strength import apply_exit_preset
from thoth.models import Settings, TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.indicator import evaluate
from thoth.verification import strategy_fingerprint
from test_crypto_ob_hours import stamp, context as hour_context
from test_crypto_ob_strength import candles, D
from test_testnet import Exchange

def daily_rows(now, side='Long'):
    boundary=int(now)//86400*86400
    values=list(range(100,150)) if side=='Long' else list(range(150,100,-1)) if side=='Short' else [100]*50
    return [dict(time=boundary-(50-i)*86400,open=v,high=v+1,low=v-1,close=v,volume=1) for i,v in enumerate(values)]

def context(now,side='Long',width=8,daily_side=None):
    ctx=hour_context(now,side,width,sma=99999 if side=='Long' else .01)
    ctx['crypto_ob_strength'].update(strategy=STRATEGY,daily_confirmation=daily_confirmation(daily_rows(now,daily_side or side),now))
    return ctx

class PolicyTests(unittest.TestCase):
    def test_settings_and_independent_preset_preserve_other_profiles_and_edits(self):
        cfg=TestnetSettings(strategy=STRATEGY,symbols=['ETHUSDT'],timeframe='1',leverage=18,daily_loss_pct=35,
            crypto_ob_hours_preset_applied=True,position_value=20,max_positions=3,coin_breakeven_enabled=True)
        self.assertEqual((cfg.timeframe,cfg.stop_dollars,cfg.opposite_action),('60',.5,'close_only'))
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled or cfg.coin_breakeven_enabled)
        saved=apply_exit_preset(cfg)
        self.assertTrue(saved.crypto_ob_daily_preset_applied)
        self.assertEqual((saved.position_value,saved.max_positions,saved.leverage,saved.daily_loss_pct),(50,10,18,35))
        self.assertEqual((saved.coin_pnl_trailing_activation_dollars,saved.coin_pnl_trailing_drop_dollars),(2.5,.1))
        self.assertTrue(saved.coin_pnl_trailing_enabled)
        self.assertFalse(saved.pnl_trailing_enabled)
        edited=TestnetSettings(**{**saved.model_dump(),'max_positions':4,'coin_pnl_trailing_activation_dollars':3})
        self.assertEqual(apply_exit_preset(edited),edited)
        self.assertEqual(len(saved.saved_symbol_lists[LIST]),48)
        self.assertEqual(POLICY['entry_thresholds'],ENTRY_THRESHOLDS)
        self.assertNotIn('sma_period',POLICY)
        self.assertNotEqual(strategy_fingerprint(cfg),strategy_fingerprint(Settings(strategy=HOURS,symbols=['ETHUSDT'])))
    def test_last_closed_daily_not_forming_and_direction_persists_without_new_cross(self):
        now=stamp(14)
        rows=daily_rows(now)
        expected=daily_confirmation(rows,now)
        boundary=int(now)//86400*86400
        rows.append(dict(time=boundary,close=.00001))
        self.assertEqual(daily_confirmation(rows,now),expected)
        self.assertEqual(daily_confirmation(rows,now+3600),expected)
        self.assertEqual(expected['sma7'],sum(range(143,150))/7)
        self.assertEqual(expected['direction'],'Long')
        for side in ('Long','Short'):
            # Deliberately opposite hourly price/SMA200: it must not filter entry.
            validate_entry('ETHUSDT',side,context(now,side),now)
            with self.assertRaisesRegex(ValueError,'Daily SMA7/50 entry permission'):
                validate_entry('ETHUSDT',side,context(now,side,daily_side='Short' if side=='Long' else 'Long'),now)
    def test_daily_gaps_missing_equality_nan_stale_and_tampered_evidence(self):
        now=stamp(14);rows=daily_rows(now)
        for bad in (rows[:-1], rows[:20]+rows[21:], rows[:-1]+[dict(time=rows[-1]['time'],close=float('nan'))]):
            with self.assertRaises(ValueError):daily_confirmation(bad,now)
        for change in ({'daily_confirmation':None},{'daily_confirmation':daily_confirmation(daily_rows(now,'Equal'),now)}):
            ctx=context(now);ctx['crypto_ob_strength'].update(change)
            with self.assertRaises(ValueError):validate_entry('ETHUSDT','Long',ctx,now)
            validate_exit('ETHUSDT','Long',ctx,now)
        ctx=context(now);ctx['crypto_ob_strength']['daily_confirmation']['sma7']+=1
        with self.assertRaises(ValueError):validate_entry('ETHUSDT','Long',ctx,now)
        ctx=context(now);ctx['crypto_ob_strength']['daily_confirmation']['closed_at']-=86400
        with self.assertRaises(ValueError):validate_entry('ETHUSDT','Long',ctx,now)
        ctx=context(now);ctx['crypto_ob_strength']['signal_bar']-=D
        with self.assertRaises(ValueError):validate_exit('ETHUSDT','Long',ctx,now)
    def test_all_blocked_hours_and_relaxed_exits_independent_of_daily(self):
        for hour in POLICY['blocked_entry_hours']:
            now=stamp(hour);ctx=context(now,'Short',7.3)
            ctx['crypto_ob_strength'].pop('daily_confirmation')
            validate_exit('ETHUSDT','Short',ctx,now)
            with self.assertRaises(ValueError):validate_entry('ETHUSDT','Short',ctx,now)
        for symbol in ('INJUSDT','WLDUSDT'):
            now=stamp(14);ctx=context(now)
            d=ctx['crypto_ob_strength'];d.update(symbol=symbol,entry_minimum=0,exit_minimum=0)
            d['entry_formation']['minimum_strength']=0;d['exit_formation']['minimum_strength']=0
            validate_exit(symbol,'Long',ctx,now)
            with self.assertRaisesRegex(ValueError,'Zero OB'):validate_entry(symbol,'Long',ctx,now)
    def test_hourly_state_checkpoint_native_formations_close_only_and_no_sma200(self):
        rows=candles(270);state=StrengthState('ETHUSDT');out=[]
        for i,row in enumerate(rows):
            if i%23==0:state=StrengthState('ETHUSDT',json.loads(json.dumps(state.checkpoint())))
            out.append(state.step(row))
        cfg=Settings(strategy=STRATEGY,symbols=['ETHUSDT'])
        self.assertEqual(out,evaluate(rows,cfg,rows[-1]['time']+D,symbol='ETHUSDT'))
        self.assertEqual(len(evaluate(rows,cfg,rows[-1]['time']+D-1,symbol='ETHUSDT')),269)
        self.assertTrue(all('sma200' not in x['crypto_ob_strength'] for x in out))
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
    def test_opposite_exit_when_daily_missing_and_hour_blocked_no_reverse(self):
        now=[stamp(4)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'fixture',TestnetSettings(**service.state['config']),context(now[0]))
            self.assertEqual(len(exchange.orders),1)
            exchange.fill(list(exchange.orders.values())[-1]['orderLinkId']);now[0]+=.1;service.monitor()
            service.apply_signals(cfg,{'ETHUSDT':dict(time=int(now[0])//D*D-D,mark=100,signal=None)})
            now[0]=stamp(5);ctx=context(now[0],'Short',7.3)
            ctx['crypto_ob_strength']['daily_confirmation']={'error':'network unavailable'}
            service.apply_signals(cfg,{'ETHUSDT':dict(time=int(now[0])//D*D-D,mark=100,signal='Short',entry_context=ctx)})
            self.assertEqual(len(exchange.orders),2,service.state['events'][-5:])
            self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly'])
            self.assertFalse(service.state['reversals'])
            exchange.fill(list(exchange.orders.values())[-1]['orderLinkId']);now[0]+=.1;service.monitor()
            self.assertFalse(service.state['owned'])
            self.assertEqual(service.history()['items'][0]['crypto_ob_strength']['exit_evidence'],ctx['crypto_ob_strength'])
    def test_submission_guard_rejects_daily_boundary_rollover(self):
        # Both New York hours are allowed; only the completed Daily changed.
        now=[stamp(18,minute=59,second=59)] # 23:59:59 UTC
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            ctx=context(now[0]);submit=service.journal.submit;guards=[]
            def delayed(*args,**kwargs):
                now[0]+=1
                # Refresh hourly formation only to isolate stale Daily protection.
                ctx['crypto_ob_strength'].update(signal_bar=int(now[0])//D*D-D,closed_at=int(now[0])//D*D)
                guards.append(kwargs['entry_guard']())
                return submit(*args,**kwargs)
            with patch.object(service.journal,'submit',side_effect=delayed):
                service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'fixture',TestnetSettings(**service.state['config']),ctx)
            self.assertIn('Daily SMA7/50',guards[0]);self.assertFalse(exchange.orders)
    def test_switch_retains_original_owner_hardstop_and_protected_orders(self):
        now=[stamp(4)]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now);service.pause()
            service.configure(TestnetSettings(**{**service.state['config'],'strategy':HOURS}))
            service.activate(service.confirmation)
            service._entry('ETHUSDT','Long',int(now[0])//D*D-D,'old',TestnetSettings(**service.state['config']),hour_context(now[0]))
            exchange.fill(list(exchange.orders.values())[-1]['orderLinkId']);now[0]+=.1;service.monitor()
            owner=deepcopy(service.state['owned']['ETHUSDT']);active=deepcopy(exchange.active)
            service.pause();service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))
            saved=service.state['owned']['ETHUSDT']
            self.assertEqual((saved['link'],saved['strategy'],saved['stop_dollars']),(owner['link'],HOURS,owner['stop_dollars']))
            self.assertEqual(saved['crypto_ob_strength']['exit_policy']['strategy'],STRATEGY)
            self.assertEqual(exchange.active,active);self.assertEqual(len(exchange.orders),1)
            service.state['config']['strategy']=HOURS;service.state['owned']['ETHUSDT']['close_link']='pending';service.save()
            with self.assertRaises(ValueError):service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))
    def test_runner_fetches_completed_daily_caches_success_retries_failure_and_keeps_exits(self):
        rows=candles(270);now=[rows[-1]['time']+D+5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.service(tmp,now)
            feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
            snap=service.snapshot()['entry_filters']
            self.assertTrue(snap['crypto_ob_daily']['enabled']);self.assertFalse(snap['sma200']['enabled'])
            packets=[]
            def cycle():
                with patch.object(exchange,'candles',return_value=rows),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]),patch.object(feed,'market_cycle',side_effect=lambda cfg,obs:packets.append(obs)):
                    runner.cycle(paced=False)
            with patch.object(feed,'daily_confirmation_inputs',side_effect=RuntimeError('fixture unavailable')) as daily:
                cycle();cycle();self.assertEqual(daily.call_count,2)
            self.assertIn('ETHUSDT',packets[-1])
            self.assertIn('error',packets[-1]['ETHUSDT']['entry_context']['crypto_ob_strength']['daily_confirmation'])
            self.assertEqual(runner.health['status'],'degraded')
            with patch.object(feed,'daily_confirmation_inputs',return_value=daily_rows(now[0])) as daily:
                cycle();cycle();self.assertEqual(daily.call_count,1)
            self.assertEqual(runner.health['status'],'ok')
            details=packets[-1]['ETHUSDT']['entry_context']['crypto_ob_strength']
            self.assertEqual(details['daily_confirmation']['direction'],'Long')
            self.assertEqual(details['strategy'],STRATEGY)
            self.assertIn('Daily SMA7/50',runner.health['note'])
            self.assertFalse(exchange.orders)

if __name__=='__main__':unittest.main()
