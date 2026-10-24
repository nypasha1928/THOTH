"""Daily intrabar OR strategy, source independence and protected minute exits."""
import math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.daily_sr_rsi import combine,observe
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.runner import Runner
from thoth.engine import Engine
from test_indicator import fixture
import test_testnet as fixtures

class DailyCombinationTests(unittest.TestCase):
    def setUp(self):
        self.cfg=Settings(strategy='sr_rsi_daily')
        self.start=86400*100
        self.levels={'long_resistance_major':dict(value=101,pivot_time=0),'short_support_minor':dict(value=99,pivot_time=0)}
    def sample(self,tracker,price,offset,events=None,**changes):
        cfg=changes.get('cfg',self.cfg)
        preview=dict(time=self.start,rsi=50,events=events or [],conflict=False)
        return observe(tracker,[dict(time=self.start,close=price)],self.levels,preview,cfg,self.start+offset)

    def test_diagnostic_distinguishes_baseline_fresh_cross_and_no_repeat(self):
        tracker={}
        event=[dict(kind='ml_long_up',side='Long')]
        result=self.sample(tracker,100,1,event)
        self.assertIsNone(result['signal'])
        self.assertIn('recovery baseline',result['signal_diagnostic']['reason'])
        self.assertEqual(tracker['last_decision']['rsi_events'],['ml_long_up'])
        self.assertNotIn('signal_diagnostic',self.sample(tracker,100,2,event))
        result=self.sample(tracker,100,3,[dict(kind='ml_short_down',side='Short')])
        self.assertEqual(result['signal'],'Short')
        self.assertEqual(result['signal_diagnostic']['reason'],'fresh crossing observed')
        self.assertEqual(tracker['last_decision']['observed_at'],self.start+3)

    def test_model_daily_fixed_all_eight_and_no_filters_even_when_supplied(self):
        cfg=Settings(strategy='sr_rsi_daily',timeframe='5',sma_exit_enabled=False,sma_enabled=True,sma_7_50_filter_enabled=True,
            rsi={'calculation_mode':'uploaded_pine','signal_rules':'original_six','static_signals':False,'ml_signals':False,
                'volume_filter':True,'volume_for_static':True,'vwap_filter':True,'sma_filter':True,'histogram_filter':True})
        self.assertEqual(cfg.timeframe,'D');self.assertTrue(cfg.sma_exit_enabled)
        self.assertEqual(cfg.rsi.calculation_mode,'fixed');self.assertEqual(cfg.rsi.signal_rules,'eight_crossings')
        self.assertFalse(cfg.sma_enabled or cfg.sma_7_50_filter_enabled)
        self.assertTrue(cfg.rsi.static_signals and cfg.rsi.ml_signals)
        self.assertTrue(all(not getattr(cfg.rsi,key) for key in ('volume_filter','volume_for_static','vwap_filter','sma_filter','histogram_filter')))
        with self.assertRaises(ValueError):Settings(strategy='sr_rsi_daily',major=False,minor=False)

    def test_or_truth_table_matching_once_conflict_skipped(self):
        for sr in (None,'Long','Short'):
            for rsi in (None,'Long','Short'):
                result=combine(dict(signal=sr,levels={}),dict(signal=rsi))
                directions={s for s in (sr,rsi) if s}
                self.assertEqual(result['signal'],next(iter(directions)) if len(directions)==1 else None)
                self.assertEqual(result['conflict'],len(directions)>1)
        self.assertIsNone(combine(dict(signal=None,conflict=True),dict(signal='Long'))['signal'])

    def test_independent_sources_duplicate_consumption_and_conflict(self):
        tracker={};self.assertIsNone(self.sample(tracker,100,10)['signal'])
        self.assertEqual(self.sample(tracker,102,15)['signal'],'Long')
        self.assertIsNone(self.sample(tracker,102,20)['signal'])
        self.assertEqual(self.sample(tracker,102,25,[dict(kind='ml_long_down',side='Short')])['signal'],'Short')
        self.assertIsNone(self.sample(tracker,102,26,[dict(kind='ml_long_down',side='Short')])['signal'])
        other={};self.sample(other,100,10)
        both=self.sample(other,102,15,[dict(kind='ml_long_up',side='Long')])
        self.assertEqual((both['signal'],both['sr_signal'],both['rsi_signal']),('Long','Long','Long'))
        other={};self.sample(other,100,10)
        conflict=self.sample(other,102,15,[dict(kind='ml_short_down',side='Short')])
        self.assertTrue(conflict['conflict']);self.assertIsNone(conflict['signal'])
        self.assertIsNone(self.sample(other,102,20,[dict(kind='ml_short_down',side='Short')])['signal'])

    def test_all_symbols_sweep_budget_but_long_outage_does_not_replay(self):
        cfg=Settings(strategy='sr_rsi_daily',symbols=[f'COIN{i}USDT' for i in range(300)])
        tracker={};self.sample(tracker,100,10,cfg=cfg)
        self.assertEqual(self.sample(tracker,102,60,cfg=cfg)['signal'],'Long')
        other={};self.sample(other,100,10,cfg=cfg)
        self.assertIsNone(self.sample(other,102,80,cfg=cfg)['signal'])
        self.assertIsNone(self.sample(other,102,85,cfg=cfg)['signal'])

    def test_stale_preview_never_partially_consumes_sr(self):
        tracker={};self.sample(tracker,100,10);saved=repr(tracker)
        with self.assertRaises(ValueError):observe(tracker,[dict(time=self.start,close=102)],self.levels,
            dict(time=self.start-86400,rsi=50,events=[],conflict=False),self.cfg,self.start+15)
        self.assertEqual(repr(tracker),saved)
        self.assertEqual(self.sample(tracker,102,20)['signal'],'Long')

    def test_daily_profile_preserves_all_eight_rsi_crossing_events(self):
        from test_ml_rsi_draft import candles
        cfg=Settings(strategy='sr_rsi_daily',rsi={'rsi_length':2,'smooth':False})
        rows=candles([100+math.sin(i*.25)*20 for i in range(160)])
        for i,row in enumerate(rows):row['time']=i*86400
        state=IndicatorState(cfg);kinds={event['kind'] for row in rows for event in state.step(row)['events']}
        self.assertEqual(kinds,{'static30_up','static30_down','static70_up','static70_down','ml_long_up','ml_long_down','ml_short_up','ml_short_down'})

    def test_reference_batch_stream_and_restored_state_match(self):
        rows=fixture(500)
        for i,row in enumerate(rows):row.update(time=i*86400,volume=100+i)
        expected=evaluate(rows,self.cfg,now=500*86400)
        state=IndicatorState(self.cfg);actual=[]
        for i,row in enumerate(rows):
            if i%43==0:state=IndicatorState(self.cfg,state.checkpoint())
            actual.append(state.step(row))
        self.assertEqual(actual,expected)
        self.assertTrue(any(b['sr_signal'] for b in actual));self.assertTrue(any(b['rsi_signal'] for b in actual))
        self.assertEqual(actual[-1]['thresholds']['ml_long'],56.68)

    def test_measured_scan_budget_accepts_reported_gaps_but_not_restarts_or_missed_scans(self):
        for gap in (101.2814,108.484,121.455):
            for source in ('sr','rsi'):
                with self.subTest(gap=gap,source=source), tempfile.TemporaryDirectory() as d:
                    engine=Engine(Path(d)/'fixture.sqlite');runner=Runner(engine)
                    cfg=Settings(strategy='sr_rsi_daily',symbols=[f'COIN{i}USDT' for i in range(223)])
                    levels=[dict(time=self.start-86400,levels=self.levels)]
                    preview=dict(time=self.start,rsi=50,events=[],conflict=False)
                    runner.scan_cycle=1
                    with patch.object(runner,'rsi_preview',return_value=preview):
                        runner.daily_sr_rsi_observation('ETHUSDT',cfg,[dict(time=self.start,close=100)],levels,self.start+10)
                        runner.previous_scan_seconds=110;runner.scan_cycle=2
                        if source=='rsi':preview['events']=[dict(kind='ml_long_up',side='Long')]
                        price=102 if source=='sr' else 100
                        result=runner.daily_sr_rsi_observation('ETHUSDT',cfg,[dict(time=self.start,close=price)],levels,self.start+10+gap)
                        self.assertEqual(result['signal'],'Long')
                        self.assertTrue(result['signal_diagnostic']['continuous_scan'])
                        self.assertGreater(result['signal_diagnostic']['gap_limit'],gap)
                        self.assertIsNone(runner.daily_sr_rsi_observation('ETHUSDT',cfg,[dict(time=self.start,close=price)],levels,self.start+11+gap)['signal'])
                    # A different worker session must baseline even with a large budget.
                    restored=Runner(engine);restored.previous_scan_seconds=200;restored.scan_cycle=3
                    preview['events']=[dict(kind='ml_short_down',side='Short')]
                    with patch.object(restored,'rsi_preview',return_value=preview):
                        result=restored.daily_sr_rsi_observation('ETHUSDT',cfg,[dict(time=self.start,close=100)],levels,self.start+12+gap)
                        self.assertIsNone(result['signal'])
                        self.assertFalse(result['signal_diagnostic']['continuous_scan'])
                        # Missing a cycle also requires a new baseline.
                        restored.scan_cycle=5;preview['events']=[dict(kind='ml_long_down',side='Short')]
                        result=restored.daily_sr_rsi_observation('ETHUSDT',cfg,[dict(time=self.start,close=100)],levels,self.start+13+gap)
                        self.assertIsNone(result['signal'])
                        self.assertFalse(result['signal_diagnostic']['continuous_scan'])
                        restored.scan_cycle=6;restored.previous_scan_seconds=1000
                        preview['events']=[dict(kind='static70_up',side='Long')]
                        result=restored.daily_sr_rsi_observation('ETHUSDT',cfg,[dict(time=self.start,close=100)],levels,self.start+500+gap)
                        self.assertIsNone(result['signal'])
                        self.assertEqual(result['signal_diagnostic']['gap_limit'],300)

    def test_joint_observation_survives_runner_restart_and_requires_current_day(self):
        with tempfile.TemporaryDirectory() as d:
            engine=Engine(Path(d)/'fixture.sqlite');runner=Runner(engine)
            result=[dict(time=self.start-86400,levels=self.levels)]
            preview=dict(time=self.start,rsi=50,events=[],conflict=False)
            with patch.object(runner,'rsi_preview',return_value=preview):
                runner.daily_sr_rsi_observation('ETHUSDT',self.cfg,[dict(time=self.start,close=100)],result,self.start+10)
                self.assertEqual(runner.daily_sr_rsi_observation('ETHUSDT',self.cfg,[dict(time=self.start,close=102)],result,self.start+15)['signal'],'Long')
            restored=Runner(engine)
            with patch.object(restored,'rsi_preview',return_value=preview):
                self.assertIsNone(restored.daily_sr_rsi_observation('ETHUSDT',self.cfg,[dict(time=self.start,close=102)],result,self.start+20)['signal'])
                with self.assertRaises(ValueError):restored.daily_sr_rsi_observation('ETHUSDT',self.cfg,[dict(time=self.start,close=102)],[],self.start+25)


    def test_forming_sr_reference_conflict_does_not_suppress_real_rsi_source(self):
        tracker={};self.sample(tracker,100,10)
        preview=dict(time=self.start,rsi=60,events=[dict(kind='ml_long_up',side='Long')],conflict=True,rsi_conflict=False)
        result=observe(tracker,[dict(time=self.start,close=100)],self.levels,preview,self.cfg,self.start+15)
        self.assertEqual(result['signal'],'Long');self.assertIsNone(result['sr_signal'])

    def test_actual_daily_runner_generates_real_rsi_crossings_and_restart_never_replays(self):
        from types import SimpleNamespace
        from test_ml_rsi_draft import candles
        for side,price,source in [(side,price,source) for side,price in [('Long',200),('Short',10)] for source in ['sr','rsi']]:
            with self.subTest(side=side,source=source),tempfile.TemporaryDirectory() as d:
                cfg=Settings(strategy='sr_rsi_daily',automatic_paper=True,symbols=['BTCUSDT'],pivot_long=2 if source=='sr' else 100,pivot_short=1 if source=='sr' else 100,rsi={'rsi_length':100 if source=='sr' else 14,'smooth':True})
                start=86400*200;now=[start+10]
                prices=[100+math.sin(i*.25+2.25)*20 for i in range(80)]  # Smoothed RSI14/EMA4 ends inside the fixed band.
                rows=candles(prices+[prices[-1]])
                for i,row in enumerate(rows):row['time']=start+(i-80)*86400
                received={}
                engine=SimpleNamespace(path=Path(d)/'daily.sqlite',snapshot=lambda:dict(settings=cfg.model_dump(),status='running',positions=[]),market_cycle=lambda config,observations:received.update(observations))
                runner=Runner(engine,fetch=lambda *a:rows)
                with patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                    runner.cycle(paced=False);self.assertIsNone(received['BTCUSDT']['signal'])
                    rows[-1].update(open=price,close=price,high=price+1,low=price-1);now[0]+=5
                    runner.cycle(paced=False)
                    self.assertEqual(received['BTCUSDT']['signal'],side)
                    self.assertEqual(received['BTCUSDT'][source+'_signal'],side)
                    self.assertIsNone(received['BTCUSDT']['sr_signal' if source=='rsi' else 'rsi_signal'])
                    self.assertEqual(received['BTCUSDT']['entry_timeframe'],'D')
                    self.assertEqual(received['BTCUSDT']['exit_timeframe'],'1')
                    now[0]+=5;Runner(engine,fetch=lambda *a:rows).cycle(paced=False)
                    self.assertIsNone(received['BTCUSDT']['signal'])


class DailyOrderIntegration(unittest.TestCase):
    def setUp(self):
        from thoth.testnet import TestnetSupervisor
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'daily.sqlite'
        self.now=[1792108800.+10];self.exchange=fixtures.Exchange(lambda:self.now[0])
        self.create=lambda:TestnetSupervisor(self.path,enabled=True,password_configured=True,
            client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])
        self.service=self.create();self.cfg=TestnetSettings(strategy='sr_rsi_daily',dedicated_account=True,max_positions=3)
        self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.trackers={s:{} for s in self.cfg.symbols}
        self.start=int(self.now[0])//86400*86400
        self.levels={'long_resistance_major':dict(value=101,pivot_time=0),'short_support_minor':dict(value=99,pivot_time=0)}
    def tearDown(self):self.temp.cleanup()
    def dispatch(self,qualify=False):
        payload={}
        for symbol in self.cfg.symbols:
            # BTC: S/R only; ETH: RSI only; SOL: both agreeing, one entry.
            price=102 if qualify and symbol!='ETHUSDT' else 100
            events=[dict(kind='ml_long_up' if symbol=='SOLUSDT' else 'ml_short_down',side='Long' if symbol=='SOLUSDT' else 'Short')] if qualify and symbol!='BTCUSDT' else []
            preview=dict(time=self.start,rsi=50,events=events,conflict=False)
            payload[symbol]=observe(self.trackers[symbol],[dict(time=self.start,close=price)],self.levels,preview,self.cfg,self.now[0])
        base=Settings(**self.cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
        self.service.apply_signals(base,payload)
    def minute_rows(self,symbol,interval,limit=200,end=None):
        self.assertEqual(interval,'1');start=int(self.now[0])//60*60
        return [dict(time=start-(50-i)*60,close=100) for i in range(51)]

    def test_three_independent_coins_protected_then_minute_exits_after_restart(self):
        self.dispatch();self.assertFalse(self.exchange.orders)
        self.now[0]+=5;self.dispatch(True)
        # Executor intentionally waits for each fill/protect acknowledgement.
        for _ in range(3):
            pending=[o for o in self.exchange.orders.values() if not o['reduceOnly'] and o['orderStatus']=='New']
            self.assertEqual(len(pending),1)
            self.exchange.fill(pending[0]['orderLinkId']);self.now[0]+=1
            with patch.object(self.exchange,'candles',side_effect=self.minute_rows):self.service.monitor()
        orders=[o for o in self.exchange.orders.values() if not o['reduceOnly']]
        self.assertEqual({o['symbol']:o['side'] for o in orders},{'BTCUSDT':'Buy','ETHUSDT':'Sell','SOLUSDT':'Buy'})
        self.now[0]+=1
        with patch.object(self.exchange,'candles',side_effect=self.minute_rows):self.dispatch(True)
        self.assertEqual(len(self.exchange.orders),3)
        self.assertTrue(all(p['stop'] for p in self.service.snapshot()['positions']))
        self.service=self.create();self.service.activate(self.service.confirmation,recovery=True)
        self.now[0]+=6
        def crossed(symbol,interval,limit=200,end=None):
            rows=self.minute_rows(symbol,interval,limit,end)
            rows[-1]['close']=99 if self.exchange.active[symbol]['side']=='Buy' else 101
            return rows
        with patch.object(self.exchange,'candles',side_effect=crossed):self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual({o['symbol'] for o in closes},set(self.cfg.symbols))
        for close in closes:self.exchange.fill(close['orderLinkId'])
        with patch.object(self.exchange,'candles',side_effect=self.minute_rows):self.service.monitor()
        trades=self.service.history()['items'];self.assertEqual(len(trades),3)
        self.assertTrue(all((t['strategy'],t['entry_timeframe'],t['exit_timeframe'])==('sr_rsi_daily','D','1') for t in trades))

    def test_max_positions_still_limits_all_selected_signals(self):
        self.service.pause();self.cfg=self.cfg.model_copy(update={'max_positions':1});self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.dispatch();self.now[0]+=5;self.dispatch(True)
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
        order=next(iter(self.exchange.orders.values()));self.exchange.fill(order['orderLinkId'])
        with patch.object(self.exchange,'candles',side_effect=self.minute_rows):self.service.monitor()
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
