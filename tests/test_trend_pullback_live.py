import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.trend_pullback import qualify,setup,ResearchSettings
from thoth.trend_pullback_live import observe,recheck,STRATEGY
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.signal_alerts import read_alerts
from thoth.database import connect
from test_testnet import Exchange
from test_trend_pullback import bars

D=1800

def source(side='Long'):
    rows=bars();rows[-1].update(open=100,low=99.9,high=100.2,close=100.01)
    if side=='Short':rows=[{**r,'open':200-r['open'],'close':200-r['close'],'low':200-r['high'],'high':200-r['low']} for r in rows]
    return rows

def level(side='Long'):return {'long_support_major' if side=='Long' else 'short_resistance_major':dict(value=100,pivot_time=0)}

def evidence(side='Long',now=402*D):
    return dict(signal=side,signal_bar=now-D,closed_at=now,source='sr_bounce:long_support_major' if side=='Long' else 'sr_rejection:short_resistance_major',boundary=100,atr14=1,sma200=99 if side=='Long' else 101,hour_close=100)

class EntryRulesTests(unittest.TestCase):
    def test_same_entry_rules_as_observer_without_research_notional_or_exit_sizing(self):
        for side in ('Long','Short'):
            with patch('thoth.trend_pullback.IndicatorState.step',return_value={'levels':level(side)}):
                rows=source(side);cfg=Settings(strategy=STRATEGY)
                live=qualify(rows,rows[-1]['close'],cfg);self.assertEqual(live['signal'],side)
                research=setup(rows,rows[-1]['close'],cfg,ResearchSettings())
                self.assertEqual((live['source'],live['distance_atr']),(research['source'],research['distance_atr']))
                self.assertNotIn('qty',live);self.assertNotIn('stop',live);self.assertNotIn('risk',live)
                self.assertIsNone(setup(rows,rows[-1]['close'],cfg,ResearchSettings(max_notional_usdt=1))['signal'])
                self.assertEqual(live['signal'],side)
                self.assertEqual(set(live['source_distances'][0]),{'source','boundary','distance_atr'})
    def test_equality_warmup_minors_wrong_trend_and_invalid_price(self):
        rows=source();cfg=Settings(strategy=STRATEGY)
        self.assertIsNone(qualify(rows[:399],100,cfg)['signal'])
        flat=[{**r,'open':.1,'high':.11,'low':.09,'close':.1} for r in rows]
        self.assertEqual(qualify(flat,.1,cfg)['reason'],'Hourly close equals SMA200')
        for lv in ({'long_support_minor':dict(value=100,pivot_time=0)}, {'short_resistance_major':dict(value=100.1,pivot_time=0)}):
            with patch('thoth.trend_pullback.IndicatorState.step',return_value={'levels':lv}):self.assertIsNone(qualify(rows,100.01,cfg)['signal'])
        for price in (float('nan'),0,float('inf')):
            with self.assertRaises(ValueError):qualify(rows,price,cfg)
    def test_profile_preserves_position_risk_and_opposite_action(self):
        cfg=TestnetSettings(strategy=STRATEGY,timeframe='1',major=False,minor=True,sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True,stop_dollars=.15,position_value=50,opposite_action='close_only')
        self.assertEqual(cfg.timeframe,'30');self.assertTrue(cfg.major);self.assertFalse(cfg.minor or cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled)
        self.assertEqual(str(cfg.stop_dollars),'0.15');self.assertEqual(float(cfg.position_value),50);self.assertEqual(cfg.opposite_action,'close_only')
    def test_submission_recheck_distance_window_equality_and_missing_evidence(self):
        for side,price in [('Long',100.05),('Short',99.95)]:
            context={'trend_pullback':evidence(side)}
            self.assertGreaterEqual(recheck(side,context,402*D+10,price)['distance_atr'],0)
            for now,p in [(402*D+121,price),(402*D+10,101 if side=='Long' else 99),(402*D+10,99 if side=='Long' else 101)]:
                with self.assertRaises(ValueError):recheck(side,context,now,p)
            with self.assertRaises(ValueError):recheck(side,{},402*D+10,price)
            context['trend_pullback']['sma200']=100
            with self.assertRaises(ValueError):recheck(side,context,402*D+10,price)

class ObservationTests(unittest.TestCase):
    def exercise(self,mode):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(SimpleNamespace(path=Path(tmp)/'live.sqlite'));cfg=Settings(strategy=STRATEGY);closed=source()
            now=[closed[-1]['time']+D-5]
            def packet():
                start=int(now[0])//D*D
                return [r for r in closed if r['time']<start]+[dict(time=start,open=100.01,close=100.01,high=100.2,low=99.9,volume=0)]
            with patch('thoth.trend_pullback.IndicatorState.step',return_value={'levels':level()}),patch('thoth.trend_pullback_live.time.time',side_effect=lambda:now[0]):
                def step(received=None):
                    runner.scan_cycle+=1
                    return observe(runner,'BTCUSDT',cfg,packet(),now[0],now[0] if received is None else received)
                self.assertIsNone(step()['signal'])
                now[0]+=10
                if mode=='restart':runner.observation_session='restart'
                if mode=='gap':now[0]+=121
                if mode=='late':now[0]=closed[-1]['time']+D+121
                result=step(now[0]-31 if mode=='stale' else None)
                if mode!='normal':self.assertIsNone(result['signal'],result)
                else:
                    self.assertEqual(result['signal'],'Long',result)
                    self.assertEqual(result['entry_sources'],['htf:30:sr_bounce:long_support_major'])
                    self.assertEqual(result['entry_context']['trend_pullback']['signal_bar'],closed[-1]['time'])
                    metrics=result['entry_context']['signal_stretch'];self.assertEqual(metrics['timeframe'],'30');self.assertEqual(metrics['closed_at'],int(now[0])//D*D);self.assertEqual(metrics['references'][0]['kind'],'bounced_support')
                now[0]+=5;self.assertIsNone(step()['signal'])
    def test_closed_only_first_timely_decision_once(self):self.exercise('normal')
    def test_restart_no_replay(self):self.exercise('restart')
    def test_missing_scan_baseline(self):self.exercise('gap')
    def test_expired_post_close_window(self):self.exercise('late')
    def test_old_price_snapshot_cannot_enter_later_same_candle(self):self.exercise('stale')
    def test_no_event_checks_are_separate_from_alerts(self):
        from thoth.signal_alerts import is_no_signal_check
        self.assertTrue(is_no_signal_check(dict(strategy=STRATEGY,signal=None,source_status='no_qualifying_signal',diagnostic=dict(reason='No trend-aligned Major bounce/rejection'))))
        self.assertFalse(is_no_signal_check(dict(strategy=STRATEGY,signal=None,entry_sources=['htf:30:sr_rejection:short_resistance_major'],diagnostic=dict(raw_sources=['sr_rejection:short_resistance_major']))))

class AccountTests(unittest.TestCase):
    def test_runner_feed_enters_with_saved_account_size_stop_and_records_observer_input(self):
        for action in ('reverse','close_only'):
            with tempfile.TemporaryDirectory() as tmp:
                closed=source();now=[closed[-1]['time']+D-5];exchange=Exchange(lambda:now[0]);exchange.prices['BTCUSDT']=100.05
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,position_value=50,stop_dollars=.15,opposite_action=action)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
                observer=SimpleNamespace(submit=lambda *args:seen.append(args));runner.trend_pullback_observer=observer;seen=[]
                def fetch(symbol,tf,limit=1000,end=None):
                    self.assertEqual(tf,'30');start=int(now[0])//D*D
                    return [r for r in closed if r['time']<start]+[dict(time=start,open=100.05,high=100.2,low=99.9,close=100.05,volume=0)]
                with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.trend_pullback.IndicatorState.step',return_value={'levels':level()}),patch('time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No legacy SMA exit')):
                    runner.cycle(paced=False);self.assertFalse(exchange.orders)
                    now[0]+=10;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1,runner.health)
                    entry=next(iter(exchange.orders.values()));self.assertEqual(entry['side'],'Buy')
                    self.assertLessEqual(float(entry['qty'])*100.05,50)
                    record=service.journal.records()[0];self.assertEqual(record['stop_dollars'],'0.15')
                    self.assertEqual(record['instrument']['thoth_exit_timeframe'],'none');self.assertIn('thoth_trend_pullback',record['instrument'])
                    exchange.fill(entry['orderLinkId']);service.monitor();self.assertTrue(service.state['owned']['BTCUSDT']['protection_confirmed'])
                    now[0]+=5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
                    self.assertGreaterEqual(len(seen),2);self.assertEqual(seen[-1][1].strategy,STRATEGY)
                    settings=feed.snapshot()['settings'];now[0]+=5
                    short=evidence('Short',int(now[0])//D*D);exchange.prices['BTCUSDT']=99.95
                    service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal='Short',entry_sources=['htf:30:sr_rejection:short_resistance_major'],entry_context=dict(primary_timeframe='30',trend_pullback=short))})
                    self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly']);self.assertEqual('BTCUSDT' in service.state['reversals'],action=='reverse')
                    closing=list(exchange.orders.values())[-1];exchange.fill(closing['orderLinkId']);service.monitor()
                    trade=service.history()['items'][0];self.assertIn('trend_pullback',trade);self.assertIn('Bounce',trade['entry_source_label'])
                    self.assertEqual(len(exchange.orders),3 if action=='reverse' else 2)
                    if action=='reverse':self.assertEqual(list(exchange.orders.values())[-1]['side'],'Sell')
                    self.assertTrue(all(a.get('entry_sources') for a in read_alerts(service.path)['items']))
    def test_final_quote_recheck_cancels_before_order_when_distance_changes(self):
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
            with patch.object(exchange,'request',side_effect=request):
                service._entry('BTCUSDT','Long',now[0],'fixture',cfg,dict(primary_timeframe='30',trend_pullback=evidence()))
            self.assertFalse(exchange.orders)
            record=service.journal.records()[0];self.assertTrue(record['result']['not_submitted']);self.assertIn('0.25 ATR',record['result']['reason'])
