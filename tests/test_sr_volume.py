import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.sr_volume import volume_pass,STRATEGY
from thoth.bybit_testnet import TestnetClient,BybitError
from thoth.timeframes import interval_seconds
from test_testnet import Exchange
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter

class SRVolumeTests(unittest.TestCase):
    def test_volume_strict_average_includes_forming(self):
        for value,expected in [(100,False),(99,False),(101,True)]:
            result=volume_pass([{'volume':100}]*19+[{'volume':value}],20)
            self.assertEqual(result['passed'],expected)
            self.assertAlmostEqual(result['average'],(1900+value)/20)
        self.assertFalse(volume_pass([{'volume':200}],20)['passed'])
        with self.assertRaises(ValueError):volume_pass([{'volume':float('nan')}]*20,20)

    def test_profile_only_volume_no_sma_exit_and_both_level_families(self):
        cfg=Settings(strategy=STRATEGY,sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True)
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled)
        self.assertTrue(cfg.major and not cfg.minor)
        self.assertEqual(cfg.sr_volume_period,20)
        with self.assertRaises(ValueError):Settings(strategy=STRATEGY,major=False,minor=False)

    def test_volume_qualifies_existing_current_bar_breakout_without_order_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=type('Engine',(),{'path':str(Path(tmp)/'live.sqlite')})()
            runner=Runner(engine);cfg=Settings(strategy=STRATEGY)
            now=[1793000000.];price=[100];vol=[100]
            def packet():
                return {'primary':{tf:[dict(time=int(now[0])//interval_seconds(tf)*interval_seconds(tf)-(19-i)*interval_seconds(tf),close=price[0],volume=vol[0] if i==19 else 100) for i in range(20)] for tf in ('240','D')}}
            def analysis(symbol,c,history,t,**kwargs):
                start=int(t)//interval_seconds(c.timeframe)*interval_seconds(c.timeframe)
                return [dict(time=start-interval_seconds(c.timeframe),levels={'long_resistance_major':dict(value=101,pivot_time=0),'short_support_major':dict(value=99,pivot_time=0)})],300
            with patch.object(runner,'history',side_effect=lambda s,t,r:r),patch.object(runner,'analysis',side_effect=analysis),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch.object(runner,'rsi_preview',side_effect=AssertionError('No RSI')):
                def step():
                    runner.scan_cycle+=1;result=runner.sr_volume_observation('BTCUSDT',cfg,packet(),now[0]);now[0]+=5;return result
                self.assertIsNone(step()['signal'])
                price[0]=102
                blocked=step();self.assertIsNone(blocked['signal']);self.assertNotIn('signal_diagnostic',blocked)
                vol[0]=200;self.assertEqual(step()['signal'],'Long')
                self.assertIsNone(step()['signal']) # One qualified entry per level/candle.
                price[0]=98
                short=step();self.assertEqual(short['signal'],'Short')
                self.assertEqual(set(short['entry_sources']),{'htf:240:sr:short_support_major','htf:D:sr:short_support_major'})
                self.assertTrue(all(r['passed'] for r in short['volume_checks'].values()))
                now[0]=(int(now[0])//86400+1)*86400+20
                price[0]=100;step();price[0]=102
                long=step();self.assertEqual(long['signal'],'Long')
                self.assertEqual(long['entry_context']['primary_timeframe'],'240')
                runner.observation_session='restarted';price[0]=98
                self.assertIsNone(step()['signal'])

    def test_unqualified_setup_cancels_on_pullback_rollover_gap_and_changed_level(self):
        from thoth.sr_volume import observe
        cfg=Settings(strategy=STRATEGY);base=1793000000//14400*14400+100
        original={'long_resistance_major':dict(value=101,pivot_time=0)}
        for invalidation in ('pullback','rollover','gap','level'):
            tracker={};levels=dict(original);now=base
            def sample(price,volume=100):
                rows=[dict(time=int(now)//14400*14400-(19-i)*14400,close=price,volume=volume if i==19 else 100) for i in range(20)]
                return observe(tracker,rows,levels,cfg,now,30)
            sample(100);now+=5
            self.assertIsNone(sample(102)['signal'])
            if invalidation=='pullback':
                now+=5;sample(100);now+=5
                self.assertIsNone(sample(100,200)['signal'])
            elif invalidation=='rollover':
                now=int(now)//14400*14400+14400+1
                self.assertIsNone(sample(102,200)['signal'])
            elif invalidation=='gap':
                now+=60;self.assertIsNone(sample(102,200)['signal'])
            else:
                levels={'long_resistance_major':dict(value=100.5,pivot_time=1)}
                now+=5;self.assertIsNone(sample(102,200)['signal'])

    def test_protected_entry_no_sma_exit_opposite_action_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[1793000000.];exchange=Exchange(lambda:now[0])
            service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT'])
            service.configure(cfg);service.activate(service.confirmation)
            feed=FeedAdapter(service);settings=feed.snapshot()['settings']
            with patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                for side in (None,'Long'):
                    now[0]+=5;service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal=side,entry_sources=['htf:D:sr:long_resistance_major'],entry_context=dict(primary_timeframe='D'))})
                order=next(iter(exchange.orders.values()));exchange.fill(order['orderLinkId']);service.monitor()
                owner=service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
                self.assertEqual(owner['entry_timeframe'],'D');self.assertEqual(owner['exit_timeframe'],'none')
                now[0]+=5;service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal='Short')})
                self.assertEqual(service.state['reversals']['BTCUSDT']['side'],'Short')
                self.assertTrue(any(o['reduceOnly'] for o in exchange.orders.values()))

    def test_four_hour_cap_persists_after_close_and_restart_shared_daily_4h(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[1793000000.];exchange=Exchange(lambda:now[0]);path=Path(tmp)/'live.sqlite'
            def service():return TestnetSupervisor(path,enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            account=service();cfg=TestnetSettings(strategy=STRATEGY,dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT','ETHUSDT'])
            account.configure(cfg);account.activate(account.confirmation)
            account._entry('BTCUSDT','Long',now[0],'fixture-first',cfg,dict(primary_timeframe='D'))
            self.assertEqual(len(exchange.orders),1)
            account._entry('BTCUSDT','Short',now[0]+1,'fixture-second',cfg,dict(primary_timeframe='240'))
            self.assertEqual(len(exchange.orders),1)
            self.assertTrue(account._sr_volume_window_used('BTCUSDT',cfg))
            self.assertFalse(account._sr_volume_window_used('ETHUSDT',cfg))
            reopened=service();self.assertTrue(reopened._sr_volume_window_used('BTCUSDT',cfg))
            # Previously deployed trades without new reservation metadata also count.
            reopened.state['sr_volume_entry_windows']={}
            with patch.object(reopened,'journal',None):
                reopened.state['owned']['BTCUSDT']=dict(strategy=STRATEGY,opened_ms=int(now[0]*1000))
                self.assertTrue(reopened._sr_volume_window_used('BTCUSDT',cfg))
                reopened.state['owned'].clear()
                trade=dict(strategy=STRATEGY,opened_at=now[0],closed_at=now[0]+1)
                with reopened.db() as db:db.execute('INSERT INTO trade_history VALUES(?,?,?,?)',('fixture','BTCUSDT',now[0]+1,json.dumps(trade)))
                self.assertTrue(reopened._sr_volume_window_used('BTCUSDT',cfg))
            now[0]=(int(now[0])//14400+1)*14400+1
            self.assertFalse(reopened._sr_volume_window_used('BTCUSDT',cfg))
            self.assertFalse(reopened._sr_volume_window_used('BTCUSDT',cfg.model_copy(update={'strategy':'sr_breakout'})))

class ClockCorrectionTests(unittest.TestCase):
    def test_private_read_corrects_signed_clock_and_retries_once(self):
        calls=[]
        def transport(req,timeout):
            calls.append(req)
            return io.BytesIO(json.dumps(dict(retCode=10002,retMsg='time window',time=120000) if len(calls)==1 else dict(retCode=0,result={'list':[]})).encode())
        client=TestnetClient('fixture','fixture',transport=transport)
        with patch('thoth.bybit_testnet.BYBIT_PACER'),patch('thoth.bybit_testnet.time.time',return_value=100):
            self.assertEqual(client.request('GET','/v5/account/transaction-log',{},private=True),{'list':[]})
        self.assertEqual([r.get_header('X-bapi-timestamp') for r in calls],['100000','120000'])
        self.assertNotEqual(calls[0].get_header('X-bapi-sign'),calls[1].get_header('X-bapi-sign'))

    def test_no_retry_writes_missing_server_time_or_repeated_clock_error(self):
        for method,private,server,expected in [('POST',True,120000,1),('GET',True,None,1),('GET',False,120000,1),('GET',True,120000,2)]:
            calls=[]
            def transport(req,timeout):
                calls.append(req);body=dict(retCode=10002,retMsg='time window')
                if server is not None:body['time']=server
                return io.BytesIO(json.dumps(body).encode())
            client=TestnetClient('fixture','fixture',transport=transport)
            with patch('thoth.bybit_testnet.BYBIT_PACER'),patch('thoth.bybit_testnet.time.time',return_value=100):
                with self.assertRaises(BybitError):client.request(method,'/v5/account/transaction-log',{},private=private)
            self.assertEqual(len(calls),expected)
