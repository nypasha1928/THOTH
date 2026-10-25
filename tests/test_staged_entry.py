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
        confirmation=Runner(adapter,confirmation_only=True,interval=0)
        start=int(self.now[0])//60*60
        minute=[dict(time=start-(59-i)*60,open=200-i,close=199-i,high=201-i,low=198-i,volume=10) for i in range(60)]
        diagnostic=dict(entry_bar=start,sr_signal='Long',rsi_signal=None,sr_levels=['short_resistance_major'],rsi_events=[])
        first=[True]
        def source(*args):
            if first[0]:return dict(signal='Long',signal_diagnostic=diagnostic)
            return dict(signal=None)
        def fetch(symbol,tf,**kw):
            from thoth.timeframes import interval_seconds
            return minute if tf=='1' else [dict(time=int(self.now[0])//interval_seconds(tf)*interval_seconds(tf))]
        with patch.object(self.exchange,'candles',side_effect=fetch),patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',side_effect=source),patch('thoth.runner.time.time',side_effect=lambda:self.now[0]):
            runner.cycle(paced=False)
            self.assertFalse(self.exchange.orders)
            self.assertEqual(self.service.state['entry_gates']['BTCUSDT']['phase'],'armed')
            first[0]=False;self.now[0]+=5
            confirmation.cycle(paced=False)  # First minute observation after arming is a baseline.
            self.assertFalse(self.exchange.orders)
            self.now[0]+=5;confirmation.cycle(paced=False)
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
        confirmation=Runner(adapter,confirmation_only=True,interval=0)
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
            self.now[0]+=5;phase[0]=2;confirmation.cycle(paced=False)
            self.assertFalse(self.exchange.orders)  # Establish the new minute baseline first.
            self.now[0]+=5;confirmation.cycle(paced=False)
            orders=[o for o in self.exchange.orders.values() if not o['reduceOnly']]
            self.assertEqual(len(orders),1);self.assertEqual(orders[0]['side'],'Sell')
            self.assertEqual(runner.health['status'],'ok')

    def test_confirmation_can_place_protected_order_while_higher_worker_is_blocked(self):
        from threading import Thread,Event
        self.sample([primary(self.now[0]+5)])
        adapter=FeedAdapter(self.service);started=Event();release=Event();failures=[]
        def blocked_fetch(symbol,tf):
            if symbol=='ETHUSDT':
                started.set()
                if not release.wait(2):raise AssertionError('Higher fetch was not released')
                return dict(primary={},minute_not_needed=True)
            return dict(primary={},minute_on_demand=True)
        higher=Runner(adapter,fetch=blocked_fetch)
        minute=Runner(adapter,confirmation_only=True,interval=0);higher.confirmation_runner=minute
        def cycle():
            try:higher.cycle(paced=False)
            except Exception as exc:failures.append(exc)
        def fresh(symbol,tf,**kw):
            self.assertEqual(tf,'1');start=int(self.now[0])//60*60
            return [dict(time=start-(59-i)*60,open=200-i,close=199-i,high=201-i,low=198-i,volume=10) for i in range(60)]
        thread=Thread(target=cycle)
        with patch('thoth.runner.time.time',side_effect=lambda:self.now[0]),patch.object(self.exchange,'candles',side_effect=fresh):
            try:
                thread.start();self.assertTrue(started.wait(1))
                minute.cycle();self.assertFalse(self.exchange.orders)
                self.now[0]+=5;minute.cycle()
                order=next(iter(self.exchange.orders.values()));self.assertEqual(order['side'],'Sell')
                self.exchange.fill(order['orderLinkId']);self.service.monitor()
                self.assertTrue(self.service.state['owned']['BTCUSDT']['protection_confirmed'])
                self.assertTrue(thread.is_alive())  # Higher scan still has NOT returned.
                self.assertEqual(higher.health['confirmation']['status'],'ok')
                self.assertEqual(higher.health['priority_minute_checks'],2)
            finally:release.set();thread.join(2)
        self.assertFalse(failures);self.assertFalse(thread.is_alive())

    def test_higher_rollover_refresh_and_timing_gap_are_reported(self):
        adapter=FeedAdapter(self.service);runner=Runner(adapter)
        now=self.now[0];expected=int(now)//14400*14400
        packet=dict(primary={'240':[dict(time=expected-14400)]},minute_not_needed=True,refresh_primary=True)
        diagnostic=dict(signal=None,sampling=dict(continuous=True,gap=453,limit=300))
        with patch('thoth.runner.time.time',return_value=now),patch.object(adapter,'staged_primary_inputs',return_value=[dict(time=expected)]) as refresh,patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',return_value=diagnostic):
            obs=runner.staged_observation('BTCUSDT',self.cfg,packet,now-5)
        refresh.assert_called_once_with('BTCUSDT','240')
        self.assertIn('453',obs['source_errors']['240']);self.assertIn('300',obs['source_errors']['240'])
        self.assertIsNone(obs['volume_signal']);self.assertEqual(obs['primary_events'],[])

    def test_feed_fetches_both_sources_and_minute_then_skips_primary_when_armed(self):
        adapter=FeedAdapter(self.service);calls=[]
        def fetch(symbol,tf,**kw):calls.append(tf);return []
        with patch.object(self.exchange,'candles',side_effect=fetch):
            packet=adapter.fetch('BTCUSDT','1');self.assertEqual(calls,['240','D']);self.assertFalse(packet['minute_on_demand']);self.assertTrue(packet['minute_not_needed']);calls.clear()
            adapter.staged_minute_inputs('BTCUSDT');self.assertEqual(calls,['1']);calls.clear()
            self.sample([primary(self.now[0]+5)])
            adapter.fetch('BTCUSDT','1');self.assertEqual(calls,[])
            adapter.staged_minute_inputs('BTCUSDT');self.assertEqual(calls,['1'])

    def test_waiting_coin_never_requests_minute_data(self):
        adapter=FeedAdapter(self.service);runner=Runner(adapter)
        with patch.object(self.exchange,'candles',return_value=[]):packet=adapter.fetch('BTCUSDT','1')
        with patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',return_value=dict(signal=None)),patch.object(adapter,'staged_minute_inputs',side_effect=AssertionError('Unarmed coins need no minute read')):
            obs=runner.staged_observation('BTCUSDT',self.cfg,{**packet,'refresh_primary':False},self.now[0])
        self.assertEqual(obs['source_errors'],{});self.assertIsNone(obs['volume_signal'])
        self.assertIn('No minute request',obs['volume_delta_status'])
        self.assertEqual(adapter.staged_armed_symbols(),[])
        self.sample([primary(self.now[0]+5)])
        self.assertEqual(adapter.staged_armed_symbols(),['BTCUSDT'])

    def test_minute_is_fetched_after_slow_higher_analysis_with_new_timestamp(self):
        adapter=FeedAdapter(self.service);runner=Runner(adapter);calls=[]
        packet=dict(primary={'240':[]},minute_on_demand=True)
        def higher(*args):
            calls.append('higher');self.now[0]+=180
            return dict(signal=None)
        def fetch(symbol,tf,**kw):
            calls.append('minute');self.assertEqual(tf,'1');self.assertEqual(kw['limit'],60)
            start=int(self.now[0])//60*60
            return [dict(time=start-(59-i)*60,open=100,close=100,high=101,low=99,volume=10) for i in range(60)]
        before=self.now[0]
        with patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',side_effect=higher),patch.object(self.exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:self.now[0]):
            observation=runner.staged_observation('BTCUSDT',self.cfg,packet,before)
        self.assertEqual(calls,['higher','minute'])
        self.assertEqual(observation['time'],before+180)
        self.assertEqual(observation['source_errors'],{})
        self.assertIn('proxy',observation['volume_delta_status'])

    def test_minute_rollover_retries_only_previous_bar_near_boundary(self):
        adapter=FeedAdapter(self.service);runner=Runner(adapter)
        now=int(self.now[0])//60*60+2;bar=int(now)//60*60
        with patch('thoth.runner.time.time',return_value=now),patch.object(runner.stop_event,'wait',return_value=False) as pause:
            with patch.object(adapter,'staged_minute_inputs',side_effect=[[{'time':bar-60}],[{'time':bar}]]) as read:
                rows,stamp=runner.fresh_staged_minute('BTCUSDT')
                self.assertEqual(rows[-1]['time'],bar);self.assertEqual(stamp,now)
                self.assertEqual(read.call_count,2);pause.assert_called_once_with(.25)
            for actual in (bar-120,bar+60,None):
                with patch.object(adapter,'staged_minute_inputs',return_value=[] if actual is None else [{'time':actual}]) as read:
                    with self.assertRaisesRegex(ValueError,'expected'):runner.fresh_staged_minute('BTCUSDT')
                    self.assertEqual(read.call_count,1)
            with patch.object(adapter,'staged_minute_inputs',return_value=[{'time':bar-60}]) as read:
                with self.assertRaisesRegex(ValueError,'received'):runner.fresh_staged_minute('BTCUSDT')
                self.assertEqual(read.call_count,3)
        with patch('thoth.runner.time.time',return_value=bar+20),patch.object(adapter,'staged_minute_inputs',return_value=[{'time':bar-60}]) as read:
            with self.assertRaises(ValueError):runner.fresh_staged_minute('BTCUSDT')
            self.assertEqual(read.call_count,1)

    def test_higher_history_bootstrap_then_small_overlap_and_gap_catchup(self):
        adapter=FeedAdapter(self.service);runner=Runner(adapter)
        duration=14400;start=int(self.now[0])//duration*duration
        self.assertEqual(adapter.staged_history_limit('BTCUSDT','240'),1000)
        rows=[dict(time=start-(99-i)*duration,open=100,high=101,low=99,close=100,volume=10) for i in range(100)]
        runner.history('BTCUSDT','240',rows)
        self.assertEqual(adapter.staged_history_limit('BTCUSDT','240'),3)
        self.now[0]+=duration*4
        self.assertEqual(adapter.staged_history_limit('BTCUSDT','240'),7)
        self.now[0]+=duration*2000
        self.assertEqual(adapter.staged_history_limit('BTCUSDT','240'),1000)

    def test_latest_analysis_matches_full_replay_and_keeps_closed_state_unchanged(self):
        import math,sqlite3
        adapter=FeedAdapter(self.service);runner=Runner(adapter)
        duration=14400;start=int(self.now[0])//duration*duration
        cfg=Settings(strategy='sr_rsi_daily').model_copy(update={'timeframe':'240'})
        rows=[]
        for i in range(100):
            close=100+math.sin(i*.4)*5
            rows.append(dict(time=start-(99-i)*duration,open=100,high=max(100,close)+1,low=min(100,close)-1,close=close,volume=10))
        history=runner.history('BTCUSDT','240',rows)
        all_bars,_=runner.analysis('BTCUSDT',cfg,history,self.now[0])
        with sqlite3.connect(runner.history_path) as db:before=db.execute('SELECT payload FROM checkpoints').fetchall()
        recent=runner.history('BTCUSDT','240',rows[-3:])
        latest,_=runner.analysis('BTCUSDT',cfg,recent,self.now[0],latest_only=True)
        self.assertEqual(latest,[all_bars[-1]])
        with sqlite3.connect(runner.history_path) as db:self.assertEqual(db.execute('SELECT payload FROM checkpoints').fetchall(),before)
        # Finalize a changed forming candle; the incremental replay still agrees.
        rows[-1].update(close=104,high=105);self.now[0]=start+duration+20
        rows.append(dict(time=start+duration,open=104,close=103,high=105,low=102,volume=10))
        history=runner.history('BTCUSDT','240',rows[-3:])
        latest,_=runner.analysis('BTCUSDT',cfg,history,self.now[0],latest_only=True)
        from thoth.indicator import evaluate
        self.assertEqual(latest[-1],evaluate(rows,cfg,self.now[0])[-1])

    def test_runner_keeps_healthy_source_permission_when_minute_or_other_source_fails(self):
        runner=Runner(FeedAdapter(self.service))
        packet=dict(primary={'240':[], 'D':ValueError('fixture daily unavailable')},minute=[],minute_error=ValueError('fixture minute unavailable'))
        diagnostic=dict(entry_bar=1,sr_signal='Long',rsi_signal='Short',sr_levels=['short_resistance_major'],rsi_events=['ml_short_down'])
        with patch.object(runner,'history',return_value=[]),patch.object(runner,'analysis',return_value=([],100)),patch.object(runner,'daily_sr_rsi_observation',return_value=dict(signal=None,signal_diagnostic=diagnostic)):
            obs=runner.staged_observation('BTCUSDT',self.cfg,packet,self.now[0])
        self.assertEqual({e['signal'] for e in obs['primary_events']},{'Long','Short'})
        self.assertEqual(set(obs['source_errors']),{'D','1'})
        self.assertIsNone(obs['volume_signal'])


class BoundedPacketTests(unittest.TestCase):
    def test_large_watchlist_cannot_prefetch_entire_scan_ahead_of_analysis(self):
        from concurrent.futures import Future
        from threading import Event
        from thoth.runner import bounded_downloads
        submitted=[]
        class Pool:
            def submit(self,fetch,symbol):
                submitted.append(symbol);future=Future();future.set_result(fetch(symbol));return future
        results=bounded_downloads(Pool(),list(range(100)),lambda symbol:symbol,Event())
        first=next(results)
        self.assertEqual(len(submitted),4)
        self.assertEqual(first[0],first[1].result())
        # Caller performs higher analysis and fresh minute read before asking for more.
        next(results);self.assertEqual(len(submitted),5)
        results.close()


class IndependentSchedulingTests(unittest.TestCase):
    def test_start_and_stop_own_both_workers(self):
        from types import SimpleNamespace
        from threading import Event,current_thread
        seen=set();both=Event()
        cfg=Settings(automatic_paper=False)
        def snapshot():
            seen.add(current_thread().name)
            if {'thoth-market','thoth-confirmation'}<=seen:both.set()
            return dict(settings=cfg.model_dump(),status='paused',positions=[])
        with tempfile.TemporaryDirectory() as directory:
            engine=SimpleNamespace(path=Path(directory)/'fixture.sqlite',snapshot=snapshot)
            runner=Runner(engine,interval=.01)
            try:
                runner.start();self.assertTrue(both.wait(1))
                self.assertIsNotNone(runner.confirmation_runner)
                self.assertTrue(runner.confirmation_runner.confirmation_only)
            finally:runner.stop()
            self.assertFalse(runner.thread.is_alive());self.assertFalse(runner.confirmation_runner.thread.is_alive())

    def test_rotating_eleven_coins_keep_per_coin_continuity(self):
        from types import SimpleNamespace
        now=[60020.];symbols=[f'COIN{i}USDT' for i in range(11)];observed=[]
        cfg=Settings(strategy=STRATEGY,automatic_paper=True,symbols=symbols)
        def minute(symbol):
            start=int(now[0])//60*60
            return [dict(time=start-(59-i)*60,open=200-i,close=199-i,high=201-i,low=198-i,volume=10) for i in range(60)]
        with tempfile.TemporaryDirectory() as directory:
            engine=SimpleNamespace(path=Path(directory)/'fixture.sqlite',snapshot=lambda:dict(settings=cfg.model_dump()),
                staged_armed_symbols=lambda:symbols,staged_minute_inputs=minute,
                market_observation=lambda config,symbol,obs:observed.append((symbol,obs['volume_signal'])))
            worker=Runner(engine,confirmation_only=True)
            with patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                for _ in range(6):worker.cycle();now[0]+=5
            self.assertEqual({symbol for symbol,signal in observed if signal=='Short'},set(symbols))
            self.assertEqual(worker.health['status'],'ok')
            # Each batch contains at most four even with eleven armed coins.
            self.assertLessEqual(worker.health['checked_this_cycle'],4)
