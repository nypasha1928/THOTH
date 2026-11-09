import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.major_pullback_intrabar import STRATEGY,observe,qualify,recheck,confirmation,chart_references
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange

D=1800;BASE=1794000000//D*D

def candle(t,p=100,low=99,high=101):return dict(time=t,open=p,close=p,low=low,high=high,volume=0)
def levels(side='Long',minor=False):return {('long_support_' if side=='Long' else 'short_resistance_')+('minor' if minor else 'major'):dict(value=100,pivot_time=BASE-10*D)}
def packet(now,half=0,side='Long'):
    target=BASE+half*900;current=int(now)//900*900;form30=int(now)//D*D
    special={target:{**candle(target),'open':100,'high':100.15,'low':99.9,'close':100.1},target+900:{**candle(target+900),'open':100.1,'high':100.2,'low':100,'close':100.12}}
    lower=[special.get(t,candle(t)) for t in (current-1800,current-900,current)]
    rows=[candle(BASE-(60-i)*D) for i in range(60)]
    source={**candle(BASE),'high':100.2,'low':99.9,'close':100.1 if form30>BASE else 100.12}
    rows.append(source)
    if form30>BASE:rows.append({**candle(form30),'close':100.12})
    if side=='Short':
        def invert(r):return {**r,'open':200-r['open'],'close':200-r['close'],'high':200-r['low'],'low':200-r['high']}
        rows=list(map(invert,rows));lower=list(map(invert,lower))
    return dict(primary={'30':rows},confirmation_15=lower,sample_received_at=now)

def context(now,side='Long'):
    return dict(primary_timeframe='30',major_pullback_intrabar=dict(signal=side,source='sr_bounce:long_support_major' if side=='Long' else 'sr_rejection:short_resistance_major',
        confirmation_closed_at=int(now)//900*900,observed_at=now,boundary=100,previous_30m_close=100,atr14=2))

class RuleTests(unittest.TestCase):
    def test_confirmation_uses_completed_actual_15m_not_forming_preview(self):
        for half in (0,1):
            now=BASE+(half+1)*900+5;p=packet(now,half)
            closed,previous,setup=confirmation(p['confirmation_15'],now)
            self.assertEqual(closed['time'],BASE+half*900);self.assertEqual(setup['time'],BASE);self.assertEqual(setup['close'],100.1)
            self.assertNotEqual(closed['close'],p['confirmation_15'][-1]['close'])
    def test_matching_major_confirmation_both_sides_no_sma_with_short_history(self):
        for side in ('Long','Short'):
            now=BASE+905;p=packet(now,side=side);last,prev,setup=confirmation(p['confirmation_15'],now);lv=levels(side)
            result=qualify(last,prev,setup,candle(BASE-D),lv,lv,Settings(strategy=STRATEGY),p['confirmation_15'][-1]['close'],2,now,[])
            self.assertEqual(result['signal'],side,result);self.assertFalse(result['sma_filter']);self.assertLessEqual(result['distance_atr'],.25)
            self.assertNotIn('sma200',result)
            self.assertGreater(recheck(side,dict(major_pullback_intrabar=result),now,p['confirmation_15'][-1]['close'])['distance_atr'],0)
    def test_changed_levels_minor_sources_and_touchless_confirmation_do_not_enter(self):
        now=BASE+905;p=packet(now);last,prev,setup=confirmation(p['confirmation_15'],now);cfg=Settings(strategy=STRATEGY)
        for mode in ('minor','changed','no_touch'):
            lv=levels(minor=mode=='minor');current=lv if mode!='changed' else {'long_support_major':dict(value=99.99,pivot_time=1)}
            tested={**last,'low':100.05} if mode=='no_touch' else last
            result=qualify(tested,prev,setup,candle(BASE-D),lv,current,cfg,100.12,2,now,[])
            self.assertIsNone(result['signal'],mode)
    def test_dedupe_and_adverse_distance_and_quote_invalidation(self):
        now=BASE+905;p=packet(now);last,prev,setup=confirmation(p['confirmation_15'],now);lv=levels();cfg=Settings(strategy=STRATEGY);fired=[]
        result=qualify(last,prev,setup,candle(BASE-D),lv,lv,cfg,101,2,now,fired)
        self.assertIsNone(result['signal']);self.assertIn('0.25 ATR',result['reason'])
        self.assertIsNone(qualify(last,prev,setup,candle(BASE-D),lv,lv,cfg,100.12,2,now+5,fired)['signal'])
        for price in (99.9,101):
            with self.assertRaises(ValueError):recheck('Long',context(now),now,price)
        with self.assertRaises(ValueError):recheck('Long',context(now),now+31,100.12)
        with self.assertRaises(ValueError):recheck('Long',context(now),BASE+1801,100.12)
    def test_chart_15m_uses_30m_majors_and_labels_forming_confirmation(self):
        now=BASE+905;p=packet(now);lv={**levels(),'short_support_minor':dict(value=100,pivot_time=0)}
        refs=chart_references(p['confirmation_15'],[dict(time=BASE-D,levels=lv)],Settings(strategy=STRATEGY),now)
        self.assertEqual(refs[-2]['entry_sources'],['sr_bounce:long_support_major'])
        self.assertFalse(refs[-2]['provisional']);self.assertTrue(refs[-1]['provisional'])
        self.assertNotIn('short_support_minor',refs[-2]['levels'])
    def test_long_and_short_term_major_families_can_confirm(self):
        now=BASE+905
        for side in ('Long','Short'):
            p=packet(now,side=side);last,prev,setup=confirmation(p['confirmation_15'],now)
            for family in ('long','short'):
                key=family+('_support_major' if side=='Long' else '_resistance_major');lv={key:dict(value=100,pivot_time=0)}
                result=qualify(last,prev,setup,candle(BASE-D),lv,lv,Settings(strategy=STRATEGY),p['confirmation_15'][-1]['close'],2,now,[])
                self.assertEqual(result['signal'],side)

    def test_profile_preserves_risk_no_hourly_filter_or_window(self):
        cfg=TestnetSettings(strategy=STRATEGY,timeframe='1',major=False,minor=True,sma_enabled=True,sma_7_50_filter_enabled=True,sma_exit_enabled=True,position_value=50,stop_dollars=.15,opposite_action='close_only')
        self.assertEqual(cfg.timeframe,'30');self.assertTrue(cfg.major);self.assertFalse(cfg.minor or cfg.sma_enabled or cfg.sma_7_50_filter_enabled or cfg.sma_exit_enabled)
        self.assertEqual(float(cfg.position_value),50);self.assertEqual(float(cfg.stop_dollars),.15);self.assertEqual(cfg.opposite_action,'close_only')

class ObservationTests(unittest.TestCase):
    def check(self,half,mode='normal',side='Long'):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(SimpleNamespace(path=Path(tmp)/'live.sqlite'));cfg=Settings(strategy=STRATEGY);now=[BASE+(half+1)*900-5];lv=levels(side)
            def analysis(symbol,c,history,t,**kw):return [dict(time=r['time'],levels=lv) for r in history if r['time']+D<=t],60
            with patch.object(runner,'analysis',side_effect=analysis),patch('time.time',side_effect=lambda:now[0]):
                def step():
                    runner.scan_cycle+=1;p=packet(now[0],half,side)
                    if mode=='stale' and runner.scan_cycle>1:p['sample_received_at']-=31
                    return observe(runner,'BTCUSDT',cfg,p,now[0])
                self.assertIsNone(step()['signal']);now[0]+=1;self.assertIsNone(step()['signal'])
                now[0]+=9
                if mode=='restart':runner.observation_session='restart'
                if mode=='gap':now[0]+=301
                result=step()
                if mode=='normal':
                    self.assertEqual(result['signal'],side,result)
                    e=result['entry_context']['major_pullback_intrabar'];self.assertEqual(e['confirmation_bar'],BASE+half*900);self.assertFalse(e['sma_filter'])
                    self.assertEqual(e['source_bar'],int(now[0])//D*D);self.assertTrue(result['entry_context']['signal_stretch']['provisional'])
                else:self.assertIsNone(result['signal'],result)
                now[0]+=5;self.assertIsNone(step()['signal'])
    def test_confirmed_15m_can_wait_for_a_later_30m_intrabar_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(SimpleNamespace(path=Path(tmp)/'live.sqlite'));cfg=Settings(strategy=STRATEGY);now=[BASE+895];price=[99.98]
            def analysis(symbol,c,history,t,**kw):return [dict(time=r['time'],levels=levels()) for r in history if r['time']+D<=t],60
            with patch.object(runner,'analysis',side_effect=analysis),patch('time.time',side_effect=lambda:now[0]):
                def step():
                    runner.scan_cycle+=1;p=packet(now[0]);p['confirmation_15'][-1].update(close=price[0],low=min(99.9,price[0]),high=max(100.2,price[0]))
                    return observe(runner,'BTCUSDT',cfg,p,now[0])
                self.assertIsNone(step()['signal']);now[0]+=10
                self.assertIsNone(step()['signal']) # Closed 15m bounce exists; current 30m price has not recovered.
                now[0]+=5;price[0]=100.12
                result=step();self.assertEqual(result['signal'],'Long',result)
                self.assertEqual(result['entry_context']['major_pullback_intrabar']['confirmation_closed_at'],BASE+900)
                now[0]+=5;self.assertIsNone(step()['signal'])

    def test_enter_before_30m_close_after_first_15m_confirms(self):self.check(0)
    def test_boundary_confirmation_is_allowed_without_fixed_minute_restriction(self):self.check(1)
    def test_short_can_enter_without_hourly_bias(self):self.check(0,side='Short')
    def test_restart_does_not_replay(self):self.check(0,'restart')
    def test_gap_does_not_replay(self):self.check(0,'gap')
    def test_stale_data_skips(self):self.check(0,'stale')

class AccountTests(unittest.TestCase):
    def test_full_runner_feed_native_confirmation_orders_and_protection(self):
        for action in ('reverse','close_only'):
            with tempfile.TemporaryDirectory() as tmp:
                now=[BASE+895];exchange=Exchange(lambda:now[0]);exchange.prices['BTCUSDT']=100.12
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,position_value=50,stop_dollars=.15,opposite_action=action)
                service.configure(cfg);service.activate(service.confirmation);feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
                def fetch(symbol,tf,limit=1000,end=None):
                    self.assertIn(tf,('30','15'));p=packet(now[0]);return p['primary']['30'] if tf=='30' else p['confirmation_15']
                def analysis(symbol,c,history,t,**kw):return [dict(time=r['time'],levels=levels()) for r in history if r['time']+D<=t],60
                with patch.object(exchange,'candles',side_effect=fetch),patch.object(runner,'analysis',side_effect=analysis),patch('time.time',side_effect=lambda:now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('No SMA exit')):
                    runner.cycle(paced=False);self.assertFalse(exchange.orders);now[0]+=10;runner.cycle(paced=False)
                    self.assertEqual(len(exchange.orders),1,runner.health);entry=next(iter(exchange.orders.values()));self.assertEqual(entry['side'],'Buy')
                    self.assertLessEqual(float(entry['qty'])*100.12,50);record=service.journal.records()[0]
                    self.assertEqual(record['stop_dollars'],'0.15');self.assertEqual(record['instrument']['thoth_exit_timeframe'],'none')
                    self.assertIn('thoth_major_pullback_intrabar',record['instrument']);exchange.fill(entry['orderLinkId']);service.monitor()
                    self.assertTrue(service.state['owned']['BTCUSDT']['protection_confirmed']);now[0]+=5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
                    now[0]+=5;exchange.prices['BTCUSDT']=99.88;settings=feed.snapshot()['settings']
                    service.apply_signals({**settings,'automatic_paper':False},{'BTCUSDT':dict(time=now[0],signal='Short',entry_sources=['htf:30:sr_rejection:short_resistance_major'],entry_context=context(now[0],'Short'))})
                    closing=list(exchange.orders.values())[-1];self.assertTrue(closing['reduceOnly']);exchange.fill(closing['orderLinkId']);service.monitor()
                    trade=service.history()['items'][0];self.assertIn('major_pullback_intrabar',trade);self.assertEqual(len(exchange.orders),3 if action=='reverse' else 2)
    def test_submission_guard_rejects_lost_pullback_and_expired_confirmation(self):
        for mode in ('price','time'):
            with tempfile.TemporaryDirectory() as tmp:
                now=[BASE+905];exchange=Exchange(lambda:now[0]);exchange.prices['BTCUSDT']=100.12
                service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                cfg=TestnetSettings(strategy=STRATEGY,dedicated_account=True,daily_loss_pct=90,symbols=['BTCUSDT']);service.configure(cfg);service.activate(service.confirmation)
                original=exchange.request;calls=[0];ctx=context(now[0])
                def request(method,path,params=None,**kw):
                    if path=='/v5/market/tickers':
                        calls[0]+=1
                        if calls[0]>1:
                            if mode=='price':exchange.prices['BTCUSDT']=99.9
                            else:now[0]+=31
                    return original(method,path,params,**kw)
                with patch.object(exchange,'request',side_effect=request):service._entry('BTCUSDT','Long',now[0],'fixture',cfg,ctx)
                self.assertFalse(exchange.orders);self.assertTrue(service.journal.records()[0]['result']['not_submitted'])
