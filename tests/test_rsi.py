"""Corrected RSI numerical/state tests. No exchange access."""
import math,random,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,RSISettings
from thoth.rsi import RSIState,clusters,MovingAverage
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.runner import Runner
from thoth.engine import Engine
from tools.ml_rsi_draft import evaluate_default,DraftConfig
from test_ml_rsi_draft import candles


def naive_clusters(values,iterations):
    values=sorted(values)
    def percentile(p):
        index=(len(values)-1)*p;i=int(index);j=min(i+1,len(values)-1)
        return values[i]+(values[j]-values[i])*(index-i)
    centers=[percentile(p) for p in (.25,.5,.75)]
    for _ in range(iterations+1):
        groups=[[],[],[]]
        for value in values:groups[min(range(3),key=lambda i:abs(value-centers[i]))].append(value)
        updated=sorted(sum(group)/len(group) if group else centers[i] for i,group in enumerate(groups))
        if all(abs(a-b)<=1e-10 for a,b in zip(updated,centers)):return updated
        centers=updated
    return centers


class RSITests(unittest.TestCase):
    def test_uploaded_source_preserves_empty_threshold_slots_and_static_filters(self):
        # Even valid cluster means are appended after three na slots by the
        # uploaded code. Verify the port's outcome without repairing that source.
        original=Path('reference/ml_rsi_900_uploaded.pine').read_text()
        self.assertIn('new_centroids = array.new_float(3)',original)
        self.assertIn('array.push(new_centroids, array.avg(cluster1))',original)
        self.assertIn('long_S = array.get(centroids, 2)',original)
        original_slots=[None]*3+[25.,50.,75.]
        rows=candles([100+math.sin(i*.13)*10 for i in range(200)])
        uploaded=RSIState(RSISettings(calculation_mode='uploaded_pine',volume_filter=False,vwap_filter=False,sma_filter=False))
        reference=evaluate_default(rows,DraftConfig(volume_filter=False,vwap_filter=False,sma_filter=False))
        seen=[]
        with patch('thoth.rsi.clusters',side_effect=AssertionError('Must not substitute corrected ML')):
            for row,expected in zip(rows,reference):
                actual=uploaded.step(row,3600)
                self.assertEqual(actual['rsi'],expected['rsi'])
                self.assertEqual(actual['events'],expected['events'])
                self.assertEqual(actual['thresholds']['ml_short'],original_slots[0])
                self.assertEqual(actual['thresholds']['ml_long'],original_slots[2])
                self.assertFalse(actual['ml_ready'])
                self.assertIsNone(actual['cluster_centers'])
                self.assertEqual(actual['ml_unavailable_reason'],'uploaded_pine_empty_threshold_slots')
                seen.extend(actual['events'])
        self.assertTrue(seen)
        self.assertTrue(all(e['kind'].startswith('static') for e in seen))
        filtered=RSIState(RSISettings(calculation_mode='uploaded_pine'))
        for row,expected in zip(rows,evaluate_default(rows,DraftConfig())):
            self.assertEqual(filtered.step(row,3600)['events'],expected['events'])

    def test_uploaded_static_signals_are_not_blocked_by_unavailable_ml(self):
        cfg=Settings(strategy='rsi_hourly_intrabar',rsi={'calculation_mode':'uploaded_pine'})
        with tempfile.TemporaryDirectory() as d:
            runner=Runner(Engine(Path(d)/'offline.sqlite'))
            preview=dict(time=360000,rsi=50,ml_ready=False,events=[],conflict=False)
            history=[dict(time=360000,close=100)]
            with patch.object(runner,'rsi_preview',side_effect=lambda *a:preview):
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,history,360010)['signal'])
                preview['events']=[dict(kind='static30_up',side='Long')]
                self.assertEqual(runner.rsi_observation('ETHUSDT',cfg,history,360015)['signal'],'Long')
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,history,360020)['signal'])
        self.assertEqual(RSISettings().calculation_mode,'corrected')
        with self.assertRaises(ValueError):RSISettings(calculation_mode='unknown')

    def test_optimized_clustering_matches_independent_nearest_center_reference(self):
        rng=random.Random(29)
        for values in ([50]*30,[0]*9+[10],list(range(100)),[rng.randrange(5)*25 for _ in range(100)],[rng.random()*100 for _ in range(500)]):
            expected=naive_clusters(values,1000);actual=clusters(values,1000)
            self.assertTrue(actual['converged'])
            self.assertAlmostEqual(actual['short'],expected[0]);self.assertAlmostEqual(actual['long'],expected[2])

    def test_default_static_signals_match_offline_default_rsi_and_filters(self):
        rows=candles([100+math.sin(i*.13)*10 for i in range(200)])
        expected=evaluate_default(rows,DraftConfig())
        state=RSIState(RSISettings(ml_signals=False))
        for row,wanted in zip(rows,expected):
            actual=state.step(row,3600)
            self.assertEqual(actual['rsi'],wanted['rsi'])
            self.assertEqual(actual['events'],wanted['events'])
            self.assertAlmostEqual(actual['vwap'],wanted['vwap'])

    def test_all_smoothing_types_checkpoint_and_forming_preview_are_causal(self):
        rows=candles([100+math.sin(i*.13)*10 for i in range(180)])
        for kind in ('SMA','Hull','Ema','Wma','Dema','RMA','LINREG','TEMA','ALMA','T3'):
            cfg=RSISettings(ma_type=kind,max_data=50)
            first=RSIState(cfg);actual=[first.step(row,3600) for row in rows[:100]]
            saved=first.checkpoint();preview=RSIState(cfg,saved).step(rows[100],3600)
            self.assertEqual(first.checkpoint(),saved)
            restored=RSIState(cfg,saved)
            actual.extend(restored.step(row,3600) for row in rows[100:])
            full=RSIState(cfg);expected=[full.step(row,3600) for row in rows]
            self.assertEqual(actual,expected,kind);self.assertEqual(preview,expected[100])
            self.assertTrue(math.isfinite(expected[-1]['rsi']))
            self.assertLessEqual(len(restored.s['rsi_values']),51)

    def test_stream_replay_and_runner_resume_match_batch(self):
        cfg=Settings(strategy='rsi_hourly_intrabar',rsi={'max_data':50,'volume_filter':False,'vwap_filter':False,'sma_filter':False})
        rows=candles([100+math.sin(i*.23)*10 for i in range(220)])
        expected=evaluate(rows,cfg,now=220*3600)
        stream=IndicatorState(cfg);self.assertEqual([stream.step(row) for row in rows],expected)
        with tempfile.TemporaryDirectory() as d:
            runner=Runner(Engine(Path(d)/'offline.sqlite'))
            first,_=runner.analysis('ETHUSDT',cfg,rows[:100],100*3600)
            actual,count=runner.analysis('ETHUSDT',cfg,rows,220*3600)
            self.assertEqual(first,expected[:100]);self.assertEqual(actual,expected);self.assertEqual(count,220)
            saved=runner.rsi_preview('ETHUSDT',cfg,rows+[dict(rows[-1],time=220*3600,close=102)],220*3600+10)
            self.assertEqual(saved['time'],220*3600)
            self.assertEqual(runner.analysis('ETHUSDT',cfg,rows,220*3600)[0],expected)

    def test_macd_sign_filters_and_source_choices_are_independent_of_sr(self):
        rows=candles([100+math.sin(i*.2)*10 for i in range(500)])
        for name in ('close','open','high','low','hlc3','ohlc4'):
            cfg=RSISettings(source=name,vwap_source=name,max_data=50,volume_filter=False,vwap_filter=False,sma_filter=False,histogram_filter=True)
            state=RSIState(cfg);output=[state.step(row,3600) for row in rows]
            for bar in output:
                for event in bar['events']:self.assertGreater(bar['histogram'] if event['side']=='Long' else -bar['histogram'],0)

    def test_sr_entry_filters_and_level_flags_do_not_change_rsi_signals(self):
        rows=candles([100+math.sin(i*.2)*10 for i in range(200)])
        base=Settings(strategy='rsi_hourly_intrabar',rsi={'max_data':50,'volume_filter':False})
        other=base.model_copy(update={'major':False,'minor':False,'sma_enabled':True,'sma_length':2,'sma_7_50_filter_enabled':True})
        self.assertEqual(evaluate(rows,base,now=200*3600),evaluate(rows,other,now=200*3600))

    def test_duplicate_intrabar_events_and_first_observation_never_replay(self):
        cfg=Settings(strategy='rsi_hourly_intrabar',rsi={'max_data':50})
        with tempfile.TemporaryDirectory() as d:
            engine=Engine(Path(d)/'offline.sqlite');runner=Runner(engine)
            preview=dict(time=360000,rsi=50,ml_ready=True,events=[],conflict=False)
            history=[dict(time=360000,close=100)]
            with patch.object(runner,'rsi_preview',side_effect=lambda *a:preview):
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,history,360010)['signal'])
                preview['events']=[dict(kind='ml_short_up',side='Long'),dict(kind='static30_up',side='Long')]
                self.assertEqual(runner.rsi_observation('ETHUSDT',cfg,history,360015)['signal'],'Long')
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,history,360020)['signal'])
            restored=Runner(engine)
            with patch.object(restored,'rsi_preview',return_value=preview):self.assertIsNone(restored.rsi_observation('ETHUSDT',cfg,history,360025)['signal'])
            preview['events']=[dict(kind='ml_long_down',side='Short')]
            with patch.object(restored,'rsi_preview',return_value=preview):self.assertIsNone(restored.rsi_observation('ETHUSDT',cfg,history,360100)['signal'])

    def test_incomplete_ml_window_or_conflict_cannot_enter(self):
        cfg=Settings(strategy='rsi_hourly_intrabar')
        with tempfile.TemporaryDirectory() as d:
            runner=Runner(Engine(Path(d)/'offline.sqlite'))
            preview=dict(time=360000,rsi=50,ml_ready=False,events=[],conflict=False)
            with patch.object(runner,'rsi_preview',return_value=preview):
                with self.assertRaises(ValueError):runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],360010)
                preview['ml_ready']=True
                runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],360010)
                preview['events']=[dict(kind='ml_long_down',side='Short'),dict(kind='ml_short_up',side='Long')];preview['conflict']=True
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],360015)['signal'])

    def test_standalone_chart_calculation_rejects_invalid_prices_and_timestamps(self):
        for change in ({'close':float('nan')},{'high':50},{'volume':-1},{'time':1}):
            with self.assertRaises(ValueError):RSIState(RSISettings()).step({**candles([100])[0],**change},3600)

    def test_new_strategy_requires_one_signal_family_and_minute_exit(self):
        cfg=Settings(strategy='rsi_hourly_intrabar',timeframe='D',sma_exit_enabled=False,major=False,minor=False)
        self.assertEqual(cfg.timeframe,'60');self.assertTrue(cfg.sma_exit_enabled)
        with self.assertRaises(ValueError):Settings(strategy='rsi_hourly_intrabar',rsi={'static_signals':False,'ml_signals':False})
        with self.assertRaises(ValueError):RSISettings(ma_type='invented')
