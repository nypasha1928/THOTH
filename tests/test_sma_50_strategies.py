import math
import tempfile
import unittest
from pathlib import Path
from thoth.models import Settings,TestnetSettings
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.sma_cross import combine_sr_sma
from thoth.runner import Runner
from thoth.engine import Engine
from thoth.verification import strategy_fingerprint
from test_sma_cross import candles


class SMA50Tests(unittest.TestCase):
    def test_both_confirmed_directions_and_minute_timeframe(self):
        cfg=Settings(strategy='sma_7_50',timeframe='D',sma_enabled=True,major=False,minor=False)
        self.assertEqual(cfg.timeframe,'D')
        cfg=cfg.model_copy(update={'timeframe':'1'})
        for name in ('sma_7_50','sr_sma_7_50'):
            self.assertFalse(Settings(strategy=name,sma_exit_enabled=False).sma_exit_enabled)
        rows=candles([100.]*50+[110.]*10+[80.]*10)
        output=evaluate(rows,cfg.model_copy(update={'timeframe':'1'}),now=4200)
        self.assertEqual(output[50]['signal'],'Long')
        self.assertEqual(sum(b['signal']=='Long' for b in output),1)
        self.assertEqual(sum(b['signal']=='Short' for b in output),1)
        self.assertIsNone(evaluate(rows,cfg,now=rows[50]['time']+59)[-1]['signal'])
        self.assertTrue(all(not b['levels'] for b in output))
        self.assertNotEqual(strategy_fingerprint(cfg),strategy_fingerprint(Settings(strategy='sma_cross')))

    def test_combined_truth_table_independence_agreement_and_conflict(self):
        for sr,cross,wanted in [('Long',None,'Long'),(None,'Short','Short'),('Long','Long','Long'),('Long','Short',None),(None,None,None)]:
            result=combine_sr_sma(dict(signal=sr,conflict=False,levels={}),dict(signal=cross,sma7=1,sma=2))
            self.assertEqual(result['signal'],wanted)
            self.assertEqual(result['conflict'],sr=='Long' and cross=='Short')
        result=combine_sr_sma(dict(signal=None,conflict=True,levels={}),dict(signal='Long',sma7=1,sma=2))
        self.assertIsNone(result['signal']);self.assertTrue(result['conflict'])

    def test_combined_batch_stream_checkpoint_and_runner_resume(self):
        rows=candles([100+8*math.sin(i/11)+2*math.sin(i/3) for i in range(700)])
        for strategy in ('sma_7_50','sr_sma_7_50'):
            cfg=Settings(strategy=strategy,pivot_long=7,pivot_short=3)
            expected=evaluate(rows,cfg,now=42000)
            state=IndicatorState(cfg);actual=[]
            for i,row in enumerate(rows):
                if i%97==0:state=IndicatorState(cfg,state.checkpoint())
                actual.append(state.step(row))
            self.assertEqual(actual,expected)
            self.assertLessEqual(len(state.cross.closes),50)
            if strategy=='sr_sma_7_50':
                self.assertTrue(any(b['signal'] and b['sr_signal'] is None for b in actual))
                self.assertTrue(all(not s.endswith('_minor') for b in actual for s in b.get('entry_sources',[])))
            with tempfile.TemporaryDirectory() as d:
                runner=Runner(Engine(Path(d)/'test.sqlite'))
                runner.analysis('ETHUSDT',cfg,rows[:100],6000)
                restored,_=runner.analysis('ETHUSDT',cfg,rows,42000)
                self.assertEqual(restored,expected)

    def test_sr_filters_do_not_filter_sma_branch(self):
        rows=candles([100.]*50+[110.]*10+[80.]*10)
        cfg=Settings(strategy='sr_sma_7_50',sma_enabled=True,sma_length=500,sma_7_50_filter_enabled=True)
        result=evaluate(rows,cfg,now=4200)
        self.assertTrue(any(b['signal']=='Long' and b['sr_signal'] is None for b in result))
        self.assertTrue(any(b['signal']=='Short' and b['sr_signal'] is None for b in result))
        with self.assertRaises(ValueError):Settings(strategy='sr_sma_7_50',major=False,minor=False)

    def test_new_strategies_use_existing_order_protection_and_history_tags(self):
        import test_testnet as fixtures
        for strategy in ('sma_7_50','sr_sma_7_50'):
            fixture=fixtures.TestnetIntegration()
            fixture.setUp()
            try:
                fixture.cfg=TestnetSettings(strategy=strategy,dedicated_account=True)
                fixture.service.configure(fixture.cfg)
                fixture.enter()
                self.assertTrue(fixture.service.snapshot()['positions'][0]['stop'])
                fixture.service.close_all();fixture.fill_closes()
                trade=fixture.service.history()['items'][0]
                self.assertEqual(trade['strategy'],strategy)
                self.assertEqual(trade['entry_timeframe'],'1')
                self.assertEqual(trade['exit_timeframe'],'1')
            finally:fixture.tearDown()

    def test_analysis_api_accepts_both_new_strategy_names(self):
        import _environment
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from thoth.app import app
        with patch('thoth.app.live.analysis',return_value={'ok':True}):
            for name in ('sma_7_50','sr_sma_7_50'):
                response=TestClient(app).get('/api/live/analysis?strategy='+name)
                self.assertEqual(response.status_code,200,response.text)

    def test_chart_returns_independent_reference_signals_for_combined_strategy(self):
        import _environment
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from thoth.app import app
        rows=candles([100+8*math.sin(i/11) for i in range(200)])
        cfg=Settings(strategy='sr_sma_7_50',pivot_long=7,pivot_short=3).model_dump()
        with patch('thoth.app.live.snapshot',return_value={'config':cfg}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=12000):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=1')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['chart_signal_strategy'],'sr_sma_7_50')
        self.assertTrue(any(row['sma_signal'] for row in data['analysis']))
        self.assertEqual(len(data['sr_analysis']),len(data['analysis']))
        self.assertTrue(all('sma_signal' not in row for row in data['sr_analysis']))

    def test_selected_intervals_keep_confirmed_close_and_stream_parity(self):
        from thoth.timeframes import INTERVALS
        for strategy in ('sma_7_50','sr_sma_7_50'):
            for interval,duration in INTERVALS.items():
                cfg=Settings(strategy=strategy,timeframe=interval)
                rows=candles([100.]*50+[110.]*10+[80.]*10)
                for i,row in enumerate(rows):row['time']=i*duration
                before=evaluate(rows[:51],cfg,now=51*duration-1)
                self.assertFalse(any(b['signal'] for b in before))
                result=evaluate(rows,cfg,now=len(rows)*duration)
                self.assertEqual(result[50]['signal'],'Long')
                self.assertTrue(any(b['signal']=='Short' for b in result))
                state=IndicatorState(cfg)
                self.assertEqual([state.step(row) for row in rows],result)
