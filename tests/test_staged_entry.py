"""Two-stage permission, fresh unlock, and real supervisor simulation (no network)."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.staged_entry import new_gate,apply,result,STRATEGY
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange


def primary(stamp=10,side='Long',frame='240',key='a',window=0):
    return dict(id=key,signal=side,time=stamp,timeframe=frame,sources=['htf:'+frame+':rsi:ml_long_up'],window_start=window)

class GateTests(unittest.TestCase):
    def sample(self,g,now,events=(),side=None,enabled=True):
        return apply(g,list(events),dict(time=now,signal=side,entry_sources=['volume_delta:bearish_strong']),now,enabled=enabled,position_open=False)

    def test_permission_does_not_enter_and_confirmation_chooses_either_side(self):
        for side in ('Long','Short'):
            g=new_gate();out,_=self.sample(g,10,[primary()],side)
            self.assertIsNone(out);self.assertEqual(g['direction'],'Any')
            out,_=self.sample(g,11,side=side)
            self.assertEqual(out['signal'],side)
            self.assertEqual(out['entry_context']['primary_timeframe'],'240')
            result(g,-1,12,3,60)
            self.sample(g,13,[primary(13,'Short','D','b')])
            self.assertEqual(g['losses'],1);self.assertEqual(g['arm_id'],1)
            result(g,1,14,3,60)
            self.assertEqual((g['losses'],g['phase']),(0,'armed'))

    def test_opposing_higher_signals_still_give_directionless_permission(self):
        g=new_gate();self.sample(g,10,[primary(),primary(10,'Short','D','b')])
        self.assertEqual(g['phase'],'armed');self.assertEqual(g['reference_direction'],'Both')
        self.assertEqual(g['primary_timeframe'],'240+D')

    def test_lock_persists_restart_and_requires_fresh_after_cooldown(self):
        g=new_gate();self.sample(g,10,[primary()])
        for n in range(3):result(g,-.1,20+n,3,60)
        g=json.loads(json.dumps(g));until=g['cooldown_until']
        self.assertIsNone(self.sample(g,until-1,[primary(until-1,key='during')],'Short')[0])
        self.assertIsNone(self.sample(g,until+1,side='Short')[0])
        self.assertEqual(g['phase'],'wait_fresh')
        self.sample(g,until+2,[primary(until+2,key='ambiguous',window=until-1)])
        self.assertEqual(g['phase'],'wait_fresh')
        self.assertIsNone(self.sample(g,until+3,[primary(until+3,key='fresh',window=until+1)],'Long')[0])
        self.assertEqual((g['phase'],g['losses']),('armed',0))
        self.assertEqual(self.sample(g,until+4,side='Short')[0]['signal'],'Short')

    def test_paused_primary_is_consumed_not_replayed(self):
        g=new_gate();self.sample(g,10,[primary()],enabled=False)
        self.sample(g,11,[primary()]);self.assertEqual(g['phase'],'waiting')

    def test_pause_still_allows_existing_position_opposite_exit(self):
        g=new_gate();self.sample(g,10,[primary()])
        out,_=apply(g,[],dict(time=11,signal='Short'),11,enabled=False,position_open=True)
        self.assertEqual(out['signal'],'Short')
        self.assertIsNone(self.sample(g,12,side='Short',enabled=False)[0])

    def test_mandatory_profile_constraints(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',sma_exit_enabled=True,loss_streak_scope='account',
            volume_delta={'sma_filter':False},rsi={'volume_filter':True})
        self.assertEqual(cfg.timeframe,'1');self.assertFalse(cfg.sma_exit_enabled)
        self.assertTrue(cfg.volume_delta.sma_filter);self.assertFalse(cfg.rsi.volume_filter)
        self.assertEqual(cfg.loss_streak_scope,'per_coin')
        self.assertEqual((cfg.consecutive_losses,cfg.pause_minutes),(3,60))

class StagedAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1792152020.]
        self.exchange=Exchange(lambda:self.now[0]);self.path=Path(self.temp.name)/'live.sqlite'
        self.service=TestnetSupervisor(self.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])
        self.cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT','ETHUSDT'],dedicated_account=True,daily_loss_pct=90)
        self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.feed_cfg=Settings(**{k:v for k,v in self.cfg.model_dump().items() if k in Settings.model_fields}).model_dump()

    def tearDown(self):self.temp.cleanup()

    def sample(self,events=(),side=None,symbol='BTCUSDT'):
        self.now[0]+=5
        self.service.apply_signals(self.feed_cfg,{symbol:dict(time=self.now[0],mark=100,primary_events=list(events),volume_signal=side,volume_sources=['volume_delta:bearish_strong'])})

    def open_position(self):
        self.sample(side='Short')
        order=next(o for o in reversed(list(self.exchange.orders.values())) if not o['reduceOnly'])
        self.assertEqual(order['side'],'Sell')
        self.exchange.fill(order['orderLinkId']);self.service.monitor()
        self.assertTrue(self.service.state['owned']['BTCUSDT']['protection_confirmed'])
        self.assertEqual(self.service.state['owned']['BTCUSDT']['exit_timeframe'],'none')
        return order

    def close_loss(self):
        self.exchange.prices['BTCUSDT']+=.1
        self.service._close('BTCUSDT',self.exchange.active['BTCUSDT'],'Fixture stop')
        for o in list(self.exchange.orders.values()):
            if o['reduceOnly'] and o['orderStatus']=='New':self.exchange.fill(o['orderLinkId'])
        self.service.monitor()

    def test_three_realized_losses_lock_only_coin_and_history_preserves_sources(self):
        with patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
            self.sample([primary(self.now[0]+5)])
            self.assertFalse(self.exchange.orders)
            for i in range(3):self.open_position();self.close_loss()
            g=self.service.state['entry_gates']['BTCUSDT']
            self.assertEqual((g['phase'],g['losses']),('cooldown',3))
            self.assertTrue(self.service.state['entries'])
            self.sample([primary(self.now[0]+5,key='eth')],symbol='ETHUSDT')
            self.assertEqual(self.service.state['entry_gates']['ETHUSDT']['phase'],'armed')
            until=g['cooldown_until'];self.now[0]=until+1
            before=len(self.exchange.orders);self.sample(side='Short')
            self.assertEqual(len(self.exchange.orders),before)
            self.assertEqual(g['phase'],'wait_fresh')
            self.sample([primary(self.now[0]+5,key='unlock',window=until+1)],side='Short')
            self.assertEqual(len(self.exchange.orders),before)
            self.open_position()
        trade=self.service.history()['items'][0]
        self.assertIn('4-hour RSI',trade['entry_source_label'])
        self.assertIn('Volume Delta',trade['entry_source_label'])
        self.assertEqual(trade['entry_timeframe'],'240')
        self.assertEqual(self.service.load()['entry_gates']['BTCUSDT']['phase'],'armed')

    def test_third_losing_reversal_cannot_open_replacement(self):
        self.sample([primary(self.now[0]+5)])
        for i in range(2):self.open_position();self.close_loss()
        self.open_position();self.exchange.prices['BTCUSDT']+=.1
        self.sample(side='Long')
        self.assertIn('BTCUSDT',self.service.state['reversals'])
        for order in list(self.exchange.orders.values()):
            if order['reduceOnly'] and order['orderStatus']=='New':self.exchange.fill(order['orderLinkId'])
        self.service.monitor()
        self.assertEqual(self.service.state['entry_gates']['BTCUSDT']['phase'],'cooldown')
        self.assertNotIn('BTCUSDT',self.service.state['reversals'])
        self.assertFalse(any(not o['reduceOnly'] and o['side']=='Buy' for o in self.exchange.orders.values()))

    def test_runner_real_minute_delta_requires_permission_then_can_short(self):
        adapter=FeedAdapter(self.service);runner=Runner(adapter,fetch=adapter.fetch)
        start=int(self.now[0])//60*60
        minute=[dict(time=start-(59-i)*60,open=200-i,close=199-i,high=201-i,low=198-i,volume=10) for i in range(60)]
        diagnostic=dict(entry_bar=start,sr_signal='Long',rsi_signal=None,sr_levels=['short_resistance_major'],rsi_events=[])
        first=[True]
        def source(*args):
            if first[0]:return dict(signal='Long',signal_diagnostic=diagnostic)
            return dict(signal=None)
        def fetch(symbol,tf,**kw):return minute if tf=='1' else [dict(time=0)]
        with patch.object(self.exchange,'candles',side_effect=fetch),patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',side_effect=source),patch('thoth.runner.time.time',side_effect=lambda:self.now[0]):
            runner.cycle(paced=False)
            self.assertFalse(self.exchange.orders)
            self.assertEqual(self.service.state['entry_gates']['BTCUSDT']['phase'],'armed')
            first[0]=False;self.now[0]+=5
            runner.cycle(paced=False)
            entries=[o for o in self.exchange.orders.values() if not o['reduceOnly']]
            self.assertEqual(len(entries),1)  # One submitted order; others wait for protection.
            self.assertEqual(entries[0]['side'],'Sell')
            self.assertEqual(runner.health['status'],'ok')

    def test_real_higher_rsi_cross_arms_then_real_minute_short_enters(self):
        import math
        cfg=self.cfg.model_copy(update={'rsi':self.cfg.rsi.model_copy(update={'smooth':False,'rsi_length':2})})
        self.service.pause();self.service.configure(cfg);self.cfg=cfg
        self.feed_cfg=Settings(**{k:v for k,v in cfg.model_dump().items() if k in Settings.model_fields}).model_dump()
        self.service.activate(self.service.confirmation)
        adapter=FeedAdapter(self.service);runner=Runner(adapter,fetch=adapter.fetch)
        phase=[0]
        def fetch(symbol,tf,**kw):
            from thoth.timeframes import interval_seconds
            duration=interval_seconds(tf);start=int(self.now[0])//duration*duration
            rows=[]
            for i in range(100):
                close=100+math.sin(i*.6)*5 if tf!='1' else 100
                rows.append(dict(time=start-(99-i)*duration,open=100,close=close,high=max(100,close)+1,low=min(100,close)-1,volume=10))
            close=(90 if phase[0]==2 else 100) if tf=='1' else (80 if phase[0]==0 else 200)
            rows[-1].update(close=close,high=max(100,close)+1,low=min(100,close)-1)
            return rows
        with patch.object(self.exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:self.now[0]):
            runner.cycle(paced=False);self.assertFalse(self.exchange.orders)
            self.assertEqual(self.service.state['entry_gates']['BTCUSDT']['phase'],'waiting')
            self.now[0]+=5;phase[0]=1;runner.cycle(paced=False)
            self.assertFalse(self.exchange.orders)
            self.assertEqual(self.service.state['entry_gates']['BTCUSDT']['phase'],'armed')
            self.now[0]+=5;phase[0]=2;runner.cycle(paced=False)
            orders=[o for o in self.exchange.orders.values() if not o['reduceOnly']]
            self.assertEqual(len(orders),1);self.assertEqual(orders[0]['side'],'Sell')
            self.assertEqual(runner.health['status'],'ok')

    def test_feed_fetches_both_sources_and_minute_then_skips_primary_when_armed(self):
        adapter=FeedAdapter(self.service);calls=[]
        def fetch(symbol,tf,**kw):calls.append(tf);return []
        with patch.object(self.exchange,'candles',side_effect=fetch):
            adapter.fetch('BTCUSDT','1');self.assertEqual(calls,['240','D','1']);calls.clear()
            self.sample([primary(self.now[0]+5)])
            adapter.fetch('BTCUSDT','1');self.assertEqual(calls,['1'])

    def test_runner_keeps_healthy_source_permission_when_minute_or_other_source_fails(self):
        runner=Runner(FeedAdapter(self.service))
        packet=dict(primary={'240':[], 'D':ValueError('fixture daily unavailable')},minute=[],minute_error=ValueError('fixture minute unavailable'))
        diagnostic=dict(entry_bar=1,sr_signal='Long',rsi_signal='Short',sr_levels=['short_resistance_major'],rsi_events=['ml_short_down'])
        with patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',return_value=dict(signal=None,signal_diagnostic=diagnostic)):
            obs=runner.staged_observation('BTCUSDT',self.cfg,packet,self.now[0])
        self.assertEqual({e['signal'] for e in obs['primary_events']},{'Long','Short'})
        self.assertEqual(set(obs['source_errors']),{'D','1'})
        self.assertIsNone(obs['volume_signal'])
