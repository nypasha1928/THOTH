"""Closed-hour permission and fresh unfiltered minute entries/exits; no network."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.staged_entry import HOUR_GATE,new_gate,result
from thoth.closed_order_block_sr import evaluate
from test_closed_inverted_ob_sr import NOW,history,sr_result
from test_order_block_intrabar import rows as ob_rows
from test_testnet import Exchange


class HourGateTests(unittest.TestCase):
    def test_profile_forces_minute_close_only_no_filters_keeps_money_controls(self):
        cfg=Settings(strategy=HOUR_GATE,timeframe='D',opposite_action='reverse',sma_enabled=True,
            sma_7_50_filter_enabled=True,sma_exit_enabled=True,major=False,minor=False,
            stop_dollars=.4,leverage=20,consecutive_losses=2,weekly_equity_trailing_enabled=True,
            coin_pnl_trailing_enabled=True,coin_pnl_trailing_activation_dollars=5)
        self.assertEqual((cfg.timeframe,cfg.opposite_action,cfg.loss_streak_scope),('1','close_only','per_coin'))
        self.assertTrue(cfg.major and not cfg.minor and cfg.weekly_equity_trailing_enabled and cfg.coin_pnl_trailing_enabled)
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled or cfg.order_block.filter_breaks)
        self.assertEqual((cfg.stop_dollars,cfg.leverage,cfg.consecutive_losses),(.4,20,2))

    def test_all_four_minute_ob_types_are_live_without_volume_and_not_replayed(self):
        now=NOW+10
        for kind,side in [('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=HOUR_GATE,symbols=['BTCUSDT']);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                def data(trigger):
                    source=ob_rows(kind=kind,trigger=trigger,passing=False)
                    start=int(now)//60*60
                    return [{**row,'time':start-(len(source)-i-1)*60,'volume':0} for i,row in enumerate(source)]
                runner.confirmation_symbol_cycles['BTCUSDT']=1
                with patch('thoth.order_block.entry_volume',side_effect=AssertionError('No volume filter')):
                    self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,data(False),now)['signal'])
                    runner.confirmation_symbol_cycles['BTCUSDT']=2
                    obs=runner.order_block_observation('BTCUSDT',cfg,data(True),now+5)
                    self.assertEqual(obs['signal'],side,obs);self.assertIn('ob:'+kind,obs['entry_sources'])
                    self.assertEqual(obs['volume_checks'],{})
                    runner.confirmation_symbol_cycles['BTCUSDT']=3
                    self.assertIsNone(runner.order_block_observation('BTCUSDT',cfg,data(True),now+10)['signal'])
                    restarted=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                    restarted.confirmation_symbol_cycles['BTCUSDT']=1
                    self.assertIsNone(restarted.order_block_observation('BTCUSDT',cfg,data(True),now+15)['signal'])

    def test_fresh_minute_sr_major_minor_both_sides_with_zero_volume(self):
        for family in ('major',):
            for side in ('Long','Short'):
                with self.subTest(family=family,side=side),tempfile.TemporaryDirectory() as tmp:
                    cfg=Settings(strategy=HOUR_GATE,symbols=['BTCUSDT'])
                    runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                    now=NOW+10;start=int(now)//60*60
                    key=('long_support_' if side=='Long' else 'short_resistance_')+family
                    levels={key:dict(value=99 if side=='Long' else 101,pivot_time=0)}
                    runner.analysis=lambda *a,**k:([dict(time=start-60,levels=levels)],200)
                    data=[dict(time=start-(59-i)*60,open=100,close=100,high=100,low=100,volume=0) for i in range(60)]
                    runner.confirmation_symbol_cycles['BTCUSDT']=1
                    self.assertIsNone(runner.order_block_sr_branch('BTCUSDT',cfg,data,now)['signal'])
                    data[-1]={**data[-1],'close':102 if side=='Long' else 98,'low':99 if side=='Long' else 98,'high':102 if side=='Long' else 101}
                    runner.confirmation_symbol_cycles['BTCUSDT']=2
                    out=runner.order_block_sr_branch('BTCUSDT',cfg,data,now+5)
                    self.assertEqual(out['signal'],side);self.assertEqual(out['entry_sources'],[('sr_bounce:' if side=='Long' else 'sr_rejection:')+key])
                    runner.confirmation_symbol_cycles['BTCUSDT']=3
                    self.assertIsNone(runner.order_block_sr_branch('BTCUSDT',cfg,data,now+10)['signal'])

    def test_changed_permission_epoch_forces_new_minute_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            epoch=[1];cfg=Settings(strategy=HOUR_GATE)
            engine=SimpleNamespace(path=str(Path(tmp)/'live.sqlite'),staged_permission_epoch=lambda symbol:epoch[0])
            runner=Runner(engine,confirmation_only=True);runner.confirmation_symbol_cycles['BTCUSDT']=4
            runner.confirmation_epochs['BTCUSDT']=1
            epoch[0]=2
            with patch.object(runner,'fresh_staged_minute',return_value=([],NOW)),patch.object(runner,'combined_order_block_sr_observation',return_value=dict(signal=None)),patch.object(runner,'closed_minute_exit_observation',return_value={}):
                runner.hour_gate_observation('BTCUSDT',cfg,dict(minute_on_demand=True),NOW)
            self.assertEqual(runner.confirmation_symbol_cycles['BTCUSDT'],6)
            self.assertEqual(runner.confirmation_epochs['BTCUSDT'],2)

    def test_hour_conflicting_directions_select_coin_but_do_not_pick_entry_side(self):
        cfg=Settings(strategy=HOUR_GATE)
        out=evaluate(history(),cfg,NOW+1,sr_result('Short'))
        self.assertIsNotNone(out['signal'])
        self.assertTrue(any(s.startswith('ob:') for s in out['entry_sources']))
        self.assertTrue(any(s.startswith('sr:') for s in out['entry_sources']))

    def test_real_workers_hour_arms_minute_chooses_opposite_side_and_exit_closes_only(self):
        for pause_exit in (False,True):
            with self.subTest(pause_exit=pause_exit),tempfile.TemporaryDirectory() as tmp:
                now=[NOW-5];price=[100.];minute_bars={};exchange=Exchange(lambda:now[0]);calls=[]
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,
                    client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=HOUR_GATE,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
                service.configure(cfg);service.activate(service.confirmation)
                feed=FeedAdapter(service);main=Runner(feed,fetch=feed.fetch);minute=Runner(feed,confirmation_only=True)
                def fetch(symbol,interval,limit=1000):
                    calls.append((interval,limit))
                    if interval=='60':return history()[-limit:]
                    self.assertEqual(interval,'1');start=int(now[0])//60*60
                    current=minute_bars.setdefault(start,dict(time=start,open=100,close=100,high=100,low=100,volume=0))
                    current.update(close=price[0],high=max(current['high'],price[0]),low=min(current['low'],price[0]))
                    return [dict(minute_bars.get(start-(199-i)*60,dict(time=start-(199-i)*60,open=100,close=100,high=100,low=100,volume=0))) for i in range(200)][-limit:]
                def analysis(symbol,source,rows,stamp,**kwargs):
                    duration=3600 if source.timeframe=='60' else 60
                    levels={'long_resistance_major':dict(value=101,pivot_time=0),'short_support_major':dict(value=97,pivot_time=0)}
                    return [dict(time=int(stamp)//duration*duration-duration,signal=None,entry_sources=[],levels=levels)],200
                main.analysis=analysis;minute.analysis=analysis
                with patch.object(minute,'order_block_observation',return_value=dict(signal=None,entry_sources=[],order_block_conflict=False)),patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                    main.cycle(paced=False);self.assertFalse(exchange.orders)
                    now[0]=NOW+1;main.cycle(paced=False)
                    self.assertEqual(service.state['entry_gates']['BTCUSDT']['phase'],'armed');self.assertFalse(exchange.orders)
                    self.assertEqual({i for i,_ in calls},{'60'})
                    now[0]+=1;minute.cycle();self.assertFalse(exchange.orders) # minute baseline
                    now[0]+=5;price[0]=102;minute.cycle();self.assertFalse(exchange.orders) # breakout is NOT an S/R entry
                    now[0]+=5;price[0]=99;minute.cycle() # resistance rejection, below previous close
                    order=next(iter(exchange.orders.values()));self.assertEqual(order['side'],'Sell')
                    exchange.fill(order['orderLinkId']);service.monitor()
                    owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                    self.assertEqual(owner['exit_timeframe'],'1')
                    self.assertTrue(any(s.startswith('htf:60:') for s in owner['entry_sources']))
                    self.assertIn('htf:1:sr_rejection:long_resistance_major',owner['entry_sources'])
                    if pause_exit:
                        service.pause();service.state['entry_gates']['BTCUSDT'].update(phase='cooldown',cooldown_until=now[0]+60);service.save()
                    now[0]+=5;price[0]=96;minute.cycle()
                    now[0]+=5;price[0]=101;minute.cycle() # native support bounce is forming, not a closed exit
                    self.assertFalse([o for o in exchange.orders.values() if o['reduceOnly']])
                    now[0]=int(now[0])//60*60+61;minute.cycle()
                    closes=[o for o in exchange.orders.values() if o['reduceOnly']]
                    self.assertEqual(len(closes),1,service.state['events'][-6:]);self.assertFalse(service.state['reversals'])
                    exchange.fill(closes[0]['orderLinkId']);service.monitor()
                    self.assertEqual(len([o for o in exchange.orders.values() if not o['reduceOnly']]),1)
                    trade=service.history()['items'][0]
                    self.assertEqual(trade['reason'],'Opposite CLOSED 1m Order Block / S/R signal')
                    self.assertIn('1-hour',trade['entry_source_label']);self.assertIn('1-minute',trade['entry_source_label'])
                    self.assertFalse(any(n==20 for _,n in calls))

    def test_loss_lock_waits_for_new_hour_after_cooldown(self):
        from thoth.staged_entry import apply
        gate=new_gate();gate.update(phase='armed',armed_at=1,arm_id=1,sources=['htf:60:ob:bullish_formed'])
        result(gate,-.1,10,2,60);self.assertTrue(result(gate,-.1,20,2,60))
        until=gate['cooldown_until']
        out,_=apply(gate,[],dict(time=until+1,signal='Long'),until+1,enabled=True,position_open=False,primary_window='CLOSED 1h')
        self.assertIsNone(out);self.assertEqual(gate['phase'],'wait_fresh')
        event=dict(id='newhour',signal='Short',time=until+2,timeframe='60',sources=['htf:60:sr:short_support_major'],window_start=until+1)
        out,_=apply(gate,[event],dict(time=until+2,signal='Long'),until+2,enabled=True,position_open=False,primary_window='CLOSED 1h')
        self.assertIsNone(out);self.assertEqual((gate['phase'],gate['losses']),('armed',0))
