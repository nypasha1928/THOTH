"""Eight crossing rules and raw chart observations versus eligible entries."""
import math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,RSISettings
from thoth.rsi import signal_events,RSIState
from thoth.runner import Runner
from thoth.engine import Engine
from test_ml_rsi_draft import candles

KINDS={'static30_up':'Long','static30_down':'Short','static70_up':'Long','static70_down':'Short',
       'ml_long_up':'Long','ml_long_down':'Short','ml_short_up':'Long','ml_short_down':'Short'}

class EightCrossingTests(unittest.TestCase):
    def test_all_eight_directions_strict_cross_and_previous_equality(self):
        for name,threshold in [('static30',30),('static70',70),('ml_long',60),('ml_short',40)]:
            for suffix,old,current,side in [('up',threshold,threshold+1,'Long'),('down',threshold,threshold-1,'Short')]:
                self.assertEqual(signal_events(old,current,{name:threshold},{name:threshold}),[dict(kind=name+'_'+suffix,side=side)])
            self.assertEqual(signal_events(threshold-1,threshold,{name:threshold},{name:threshold}),[])
            self.assertEqual(signal_events(threshold+1,threshold,{name:threshold},{name:threshold}),[])
            self.assertEqual(signal_events(threshold+1,threshold+2,{name:threshold},{name:threshold}),[])
            self.assertEqual(signal_events(threshold-1,threshold-2,{name:threshold},{name:threshold}),[])
        self.assertEqual(signal_events(None,40,{},dict(static30=30)),[])
        self.assertEqual(signal_events(40,50,dict(ml_long=None),dict(ml_long=None)),[])
        # Adaptive series compare each RSI value with that candle's threshold.
        self.assertEqual(signal_events(50,51,dict(ml_long=52),dict(ml_long=50)),[dict(kind='ml_long_up',side='Long')])

    def test_real_rsi_generates_all_eight_and_filters_never_erase_crossing_evidence(self):
        rows=candles([100+math.sin(i*.25)*20 for i in range(300)])
        cfg=RSISettings(calculation_mode='corrected',signal_rules='eight_crossings',rsi_length=2,smooth=False,max_data=50,
            volume_filter=False,vwap_filter=False,sma_filter=False)
        state=RSIState(cfg);bars=[state.step(row,3600) for row in rows]
        self.assertEqual({e['kind'] for b in bars for e in b['crossings']},set(KINDS))
        self.assertEqual({e['kind'] for b in bars for e in b['events']},set(KINDS))
        blocked=RSIState(cfg.model_copy(update={'volume_filter':True,'volume_for_static':True,'volume_multiplier':5}))
        blocked_bars=[blocked.step(row,3600) for row in rows]
        self.assertFalse(any(b['events'] for b in blocked_bars))
        self.assertEqual([b['crossings'] for b in blocked_bars],[b['crossings'] for b in bars])
        legacy=RSIState(cfg.model_copy(update={'signal_rules':'original_six'}))
        original=[legacy.step(row,3600) for row in rows]
        self.assertEqual({e['kind'] for b in original for e in b['events']},set(KINDS)-{'static30_down','static70_up'})
        self.assertEqual([b['rsi'] for b in original],[b['rsi'] for b in bars])
        self.assertEqual([b['thresholds'] for b in original],[b['thresholds'] for b in bars])
        self.assertEqual([b['crossings'] for b in original],[b['crossings'] for b in bars])

    def test_eight_crossings_survive_checkpoint_and_family_entry_switches(self):
        cfg=RSISettings(calculation_mode='corrected',signal_rules='eight_crossings',rsi_length=2,smooth=False,max_data=50,
            volume_filter=False,vwap_filter=False,sma_filter=False,static_signals=False)
        state=RSIState(cfg);restored=RSIState(cfg);seen=set()
        for row in candles([100+math.sin(i*.25)*20 for i in range(150)]):
            bar=state.step(row,3600);actual=restored.step(row,3600)
            self.assertEqual(actual,bar);restored=RSIState(cfg,restored.checkpoint())
            self.assertFalse(any(e['kind'].startswith('static') for e in bar['events']))
            seen.update(e['kind'] for e in bar['crossings'])
        self.assertEqual(seen,set(KINDS))

    def test_runner_only_dispatches_eligible_events_not_raw_crossings(self):
        cfg=Settings(strategy='rsi_hourly_intrabar',rsi={'signal_rules':'eight_crossings'})
        with tempfile.TemporaryDirectory() as d:
            runner=Runner(Engine(Path(d)/'fixture.sqlite'))
            preview=dict(time=360000,rsi=50,ml_ready=True,events=[],crossings=[],conflict=False)
            with patch.object(runner,'rsi_preview',side_effect=lambda *args:preview):
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],360001)['signal'])
                for index,(kind,side) in enumerate(KINDS.items()):
                    preview.update(events=[],crossings=[dict(kind=kind,side=side)])
                    stamp=360005+index*5
                    self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],stamp)['signal'])
                    preview['events']=preview['crossings']
                    self.assertEqual(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],stamp+1)['signal'],side)
                    self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],stamp+2)['signal'])
        self.assertEqual(RSISettings().signal_rules,'original_six')
        with self.assertRaises(ValueError):RSISettings(signal_rules='invalid')

    def test_chart_api_has_all_crossings_when_entry_filters_block_every_trade(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        rows=candles([100+math.sin(i*.25)*20 for i in range(150)])
        cfg=Settings(strategy='sr_breakout',rsi={'calculation_mode':'corrected','signal_rules':'eight_crossings','rsi_length':2,'smooth':False,'max_data':50,
            'volume_filter':True,'volume_for_static':True,'volume_multiplier':5,'vwap_filter':False,'sma_filter':False}).model_dump()
        with patch('thoth.app.live.snapshot',return_value={'config':cfg}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=150*3600):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=60&include_rsi=true')
        self.assertEqual(response.status_code,200,response.text)
        bars=response.json()['rsi_analysis']
        self.assertEqual({e['kind'] for b in bars for e in b['crossings']},set(KINDS))
        self.assertFalse(any(b['events'] for b in bars))
        self.assertEqual(cfg['strategy'],'sr_breakout')
