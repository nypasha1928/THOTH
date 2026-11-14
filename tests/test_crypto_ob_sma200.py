import hashlib,json,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from thoth.crypto_ob_sma200 import STRATEGY,StrengthState,THRESHOLDS,EXIT_THRESHOLDS,validate_entry,validate_exit
from thoth.crypto_ob_strength import apply_exit_preset
from thoth.models import Settings,TestnetSettings
from thoth.indicator import evaluate
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from test_testnet import Exchange
from test_crypto_ob_strength import candles,D,BASE


def context(side='Long',width=8,sma=90,stamp=BASE):
    formation=dict(side=side,block_high=100+width,block_low=100,block_width=width)
    return {'crypto_ob_strength':dict(strategy=STRATEGY,symbol='ETHUSDT',timeframe='60',
        signal_bar=stamp,closed_at=stamp+D,entry_minimum=7.45,exit_minimum=EXIT_THRESHOLDS['ETHUSDT'],
        exit_ratio='32/33',close=100,sma200=sma,
        entry_formation={**formation,'minimum_strength':7.45} if width>=7.45 else None,
        exit_formation={**formation,'minimum_strength':EXIT_THRESHOLDS['ETHUSDT']})}

class PolicyTests(unittest.TestCase):
    def test_all_fifteen_and_exact_grt_example(self):
        self.assertEqual(set(THRESHOLDS),set(EXIT_THRESHOLDS));self.assertEqual(len(EXIT_THRESHOLDS),15)
        self.assertEqual(EXIT_THRESHOLDS['GRTUSDT'],.00032)
        self.assertTrue(all(0<EXIT_THRESHOLDS[s]<v for s,v in THRESHOLDS.items()))
        cfg=TestnetSettings(strategy=STRATEGY,symbols=list(THRESHOLDS),timeframe='1',opposite_action='reverse',stop_dollars=1)
        self.assertEqual((cfg.timeframe,cfg.opposite_action,cfg.stop_dollars),('60','close_only',.5))
        self.assertFalse(cfg.sma_exit_enabled or cfg.sma_7_50_filter_enabled or cfg.order_block.supply_breakout)
        with self.assertRaises(ValueError):Settings(strategy=STRATEGY,symbols=['BTCUSDT'])
    def test_independent_preset_and_editable_trailing(self):
        cfg=TestnetSettings(strategy=STRATEGY,symbols=['ETHUSDT'],crypto_ob_exit_preset_applied=True)
        cfg=apply_exit_preset(cfg)
        self.assertTrue(cfg.crypto_ob_sma200_preset_applied and cfg.coin_pnl_trailing_enabled)
        self.assertEqual((cfg.coin_pnl_trailing_activation_dollars,cfg.coin_pnl_trailing_drop_dollars),(5,.1))
        self.assertFalse(cfg.pnl_trailing_enabled)
        cfg=TestnetSettings(**{**cfg.model_dump(),'coin_pnl_trailing_activation_dollars':4})
        self.assertEqual(apply_exit_preset(cfg).coin_pnl_trailing_activation_dollars,4)
    def test_entry_sma_permission_both_sides_and_equality_missing(self):
        now=BASE+D+5
        for side,sma in [('Long',90),('Short',110)]:validate_entry('ETHUSDT',side,context(side,sma=sma),now)
        for side,sma in [('Long',110),('Short',90),('Long',100),('Short',100),('Long',None),('Long',float('nan'))]:
            with self.assertRaises(ValueError):validate_entry('ETHUSDT',side,context(side,sma=sma),now)
    def test_weaker_exit_bypasses_sma_but_never_enters(self):
        for sma in [90,110,100,None]:
            ctx=context('Short',7.3,sma=sma)
            validate_exit('ETHUSDT','Short',ctx,BASE+D+5)
            with self.assertRaises(ValueError):validate_entry('ETHUSDT','Short',ctx,BASE+D+5)
    def test_stale_wrong_side_symbol_threshold_or_boundaries_rejected(self):
        good=context();now=BASE+D+5
        for mutate in [lambda d:d.update(symbol='GRTUSDT'),lambda d:d.update(exit_ratio='0.97'),
                       lambda d:d.update(signal_bar=BASE-D),lambda d:d.update(exit_minimum=0),
                       lambda d:d['exit_formation'].update(block_high=float('nan')),
                       lambda d:d['exit_formation'].update(block_high=100.1),
                       lambda d:d['exit_formation'].update(side='Short')]:
            bad=deepcopy(good);mutate(bad['crypto_ob_strength'])
            with self.assertRaises(ValueError):validate_exit('ETHUSDT','Long',bad,now)
        with self.assertRaises(ValueError):validate_entry('ETHUSDT','Long',good,now+D)
    def test_native_closed_checkpoint_prefix_and_sma_warmup(self):
        rows=candles(270);state=StrengthState('ETHUSDT');outputs=[]
        for i,row in enumerate(rows):
            if i%23==0:state=StrengthState('ETHUSDT',json.loads(json.dumps(state.checkpoint())))
            outputs.append(state.step(row))
        cfg=Settings(strategy=STRATEGY,symbols=['ETHUSDT'])
        self.assertEqual(outputs,evaluate(rows,cfg,rows[-1]['time']+D,symbol='ETHUSDT'))
        self.assertEqual(len(evaluate(rows,cfg,rows[-1]['time']+D-1,symbol='ETHUSDT')),len(rows)-1)
        self.assertTrue(all(x['crypto_ob_strength']['sma200'] is None for x in outputs[:199]))
        self.assertAlmostEqual(outputs[199]['crypto_ob_strength']['sma200'],sum(r['close'] for r in rows[:200])/200)
        passed=0
        for out in outputs:
            if out['signal']:
                validate_exit('ETHUSDT',out['signal'],{'crypto_ob_strength':out['crypto_ob_strength']},out['time']+D+1)
                try:validate_entry('ETHUSDT',out['signal'],{'crypto_ob_strength':out['crypto_ob_strength']},out['time']+D+1);passed+=1
                except ValueError:pass
        self.assertGreater(passed,0)
        with self.assertRaises(ValueError):StrengthState('GRTUSDT',state.checkpoint())

class ExecutionTests(unittest.TestCase):
    def setup_service(self,tmp,now):
        exchange=Exchange(lambda:now[0])
        service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,
            client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        service.configure(TestnetSettings(strategy=STRATEGY,symbols=['ETHUSDT'],dedicated_account=True,daily_loss_pct=90,consecutive_losses=99))
        service.activate(service.confirmation)
        cfg=Settings(**{k:v for k,v in service.state['config'].items() if k in Settings.model_fields}).model_dump()
        return service,exchange,cfg
    def test_weaker_opposite_closes_no_reverse_durable_evidence_and_no_replay(self):
        now=[BASE+D+5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.setup_service(tmp,now)
            def apply(stamp,side,ctx):
                service.apply_signals(cfg,{'ETHUSDT':dict(time=stamp,mark=100,signal=side,entry_sources=['ob:bullish_formed' if side=='Long' else 'ob:bearish_formed'],entry_context=ctx)})
            def fill():
                order=list(exchange.orders.values())[-1];exchange.fill(order['orderLinkId']);now[0]+=.1;service.monitor()
            apply(BASE-D,None,None);apply(BASE,'Long',context())
            self.assertEqual(len(exchange.orders),1,service.state['events'][-5:]);fill()
            now[0]=BASE+2*D+5
            weak=context('Short',7.3,sma=90,stamp=BASE+D)
            apply(BASE+D,'Short',weak)
            self.assertEqual(len(exchange.orders),2);self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly'])
            self.assertFalse(service.state['reversals']);fill()
            self.assertFalse(service.state['owned']);self.assertEqual(len(exchange.orders),2)
            trade=service.history()['items'][0]
            self.assertIn('3.03%',trade['reason'])
            self.assertEqual(trade['crypto_ob_strength']['exit_evidence'],weak['crypto_ob_strength'])
            apply(BASE+D,'Short',weak);self.assertEqual(len(exchange.orders),2)
            now[0]+=D
            apply(BASE+2*D,'Short',context('Short',7.3,110,BASE+2*D))
            self.assertEqual(len(exchange.orders),2) # weak formation cannot open even with SMA permission
            now[0]+=D
            apply(BASE+3*D,'Short',context('Short',8,110,BASE+3*D))
            self.assertEqual(len(exchange.orders),3) # genuinely later strong permitted entry
    def test_switch_with_protected_positions_preserves_stops_trailing_and_entry_history(self):
        from thoth.crypto_ob_strength import STRATEGY as OLD,StrengthState as OldState
        now=[BASE+D+5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.setup_service(tmp,now)
            service.pause()
            service.configure(TestnetSettings(**{**service.state['config'],'strategy':OLD}))
            service.activate(service.confirmation)
            state=OldState('ETHUSDT');event=next(x for x in [state.step(row) for row in candles()] if x['signal']=='Long')
            now[0]=event['time']+D+5
            current=TestnetSettings(**service.state['config'])
            service._entry('ETHUSDT','Long',event['time'],'fixture',current,{'crypto_ob_strength':event['crypto_ob_strength']})
            order=list(exchange.orders.values())[-1];exchange.fill(order['orderLinkId']);now[0]+=.1;service.monitor()
            service.state['owned']['ETHUSDT']['stop_dollars']='0.75'
            service.state['owned']['ETHUSDT']['coin_pnl_trailing']={'active':True,'peak':'5.4','current':'5.4','threshold':'5.3','triggered':False}
            service.save();service.pause()
            before=deepcopy(service.state['owned']['ETHUSDT']);position=deepcopy(exchange.active['ETHUSDT']);order_count=len(exchange.orders)
            service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))
            owner=service.state['owned']['ETHUSDT']
            self.assertEqual(owner['link'],before['link']);self.assertEqual(owner['stop_dollars'],'0.75')
            self.assertEqual(owner['strategy'],OLD);self.assertEqual(owner['coin_pnl_trailing'],before['coin_pnl_trailing'])
            self.assertEqual(owner['crypto_ob_strength']['exit_policy']['strategy'],STRATEGY)
            self.assertEqual(len(exchange.orders),order_count);self.assertEqual(exchange.active['ETHUSDT'],position)
            cfg=Settings(**{k:v for k,v in service.state['config'].items() if k in Settings.model_fields}).model_dump()
            def apply(stamp,signal,ctx):service.apply_signals(cfg,{'ETHUSDT':dict(time=stamp,mark=100,signal=signal,entry_context=ctx)})
            apply(event['time'],None,None) # new strategy baseline
            now[0]=event['time']+2*D+5
            apply(event['time']+D,'Short',context('Short',7.3,90,event['time']+D))
            self.assertEqual(len(exchange.orders),order_count+1)
            self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly'])
            self.assertFalse(service.state['reversals'])
    def test_switch_rejects_unprotected_or_pending_entry_and_unrelated_strategy(self):
        from thoth.crypto_ob_strength import STRATEGY as OLD
        now=[BASE+D+5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.setup_service(tmp,now);service.pause()
            service.configure(TestnetSettings(**{**service.state['config'],'strategy':OLD}))
            service.state['owned']['ETHUSDT']=dict(link='fixture',strategy=OLD,protection_confirmed=False,close_link=None)
            service.save()
            with self.assertRaises(ValueError):service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))
            with self.assertRaises(ValueError):service.configure(TestnetSettings(**{**service.state['config'],'strategy':'sma_7_50'}))
    def test_pending_entry_must_settle_before_in_place_switch(self):
        from thoth.crypto_ob_strength import STRATEGY as OLD,StrengthState as OldState
        now=[BASE+D+5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.setup_service(tmp,now);service.pause()
            service.configure(TestnetSettings(**{**service.state['config'],'strategy':OLD}))
            service.activate(service.confirmation)
            state=OldState('ETHUSDT');event=next(x for x in [state.step(row) for row in candles()] if x['signal']=='Long')
            now[0]=event['time']+D+5
            service._entry('ETHUSDT','Long',event['time'],'fixture',TestnetSettings(**service.state['config']),{'crypto_ob_strength':event['crypto_ob_strength']})
            self.assertTrue(exchange.orders);self.assertFalse(service.state['owned'])
            service.pause()
            with self.assertRaises(ValueError):service.configure(TestnetSettings(**{**service.state['config'],'strategy':STRATEGY}))
            self.assertEqual(service.state['config']['strategy'],OLD)
    def test_direct_entry_revalidation_and_missing_sma_blocks(self):
        now=[BASE+D+5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.setup_service(tmp,now)
            current=TestnetSettings(**service.state['config'])
            for ctx in [context('Long',8,110),context('Long',8,None),context('Long',7.3,90)]:
                service._entry('ETHUSDT','Long',BASE,'fixture',current,ctx)
            self.assertFalse(exchange.orders)
    def test_runner_uses_closed_hour_and_sma_filter_instead_of_legacy_profile(self):
        rows=candles(270);state=StrengthState('ETHUSDT');outputs=[state.step(row) for row in rows]
        first=next(i for i,r in enumerate(outputs) if i>=200 and r['signal'] and r['crypto_ob_strength']['entry_formation'] and
            ((r['close']>r['sma']) if r['signal']=='Long' else (r['close']<r['sma'])))
        now=[rows[first]['time']+D-5]
        with tempfile.TemporaryDirectory() as tmp:
            service,exchange,cfg=self.setup_service(tmp,now);feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
            def fetch(symbol,interval,limit=1000,end=None):
                return [r for r in rows if r['time']<=int(now[0])//D*D][-limit:]
            with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                runner.cycle(paced=False);self.assertFalse(exchange.orders)
                now[0]=rows[first]['time']+D+5;runner.cycle(paced=False)
                self.assertEqual(len(exchange.orders),1,(runner.health,service.state['events'][-5:]))
                record=service.journal.records()[0]
                self.assertEqual(record['instrument']['thoth_crypto_ob_strength']['strategy'],STRATEGY)
                self.assertIn('sma200',record['instrument']['thoth_crypto_ob_strength'])

if __name__=='__main__':unittest.main()
