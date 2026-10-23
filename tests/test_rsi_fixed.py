"""Fixed RSI thresholds have no adaptive dependency and remain strategy eligible."""
import math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import RSISettings,Settings,TestnetSettings
from thoth.rsi import RSIState
from thoth.runner import Runner
from thoth.engine import Engine
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_ml_rsi_draft import candles

class FixedRSITests(unittest.TestCase):
    def test_constants_all_crossings_and_checkpoint_without_clustering(self):
        cfg=RSISettings(signal_rules='eight_crossings',rsi_length=2,smooth=False)
        state=RSIState(cfg);restored=RSIState(cfg);seen=set()
        with patch('thoth.rsi.clusters',side_effect=AssertionError('Fixed must not cluster')):
            for row in candles([100+math.sin(i*.25)*20 for i in range(160)]):
                bar=state.step(row,3600)
                self.assertEqual(restored.step(row,3600),bar);restored=RSIState(cfg,restored.checkpoint())
                self.assertEqual((bar['thresholds']['ml_long'],bar['thresholds']['ml_short']),(56.68,42.38))
                self.assertIsNone(bar['cluster_centers']);self.assertIsNone(bar['rsi_cluster'])
                self.assertFalse(bar['ml_ready']);seen.update(e['kind'] for e in bar['events'])
        self.assertEqual(seen,{'static30_up','static30_down','static70_up','static70_down','ml_long_up','ml_long_down','ml_short_up','ml_short_down'})

    def test_bounds_and_order_validated(self):
        for values in ({'fixed_long':42,'fixed_short':42},{'fixed_long':40,'fixed_short':50},{'fixed_long':100},{'fixed_short':0},{'fixed_long':float('nan')},{'fixed_short':float('inf')}):
            with self.assertRaises(ValueError):RSISettings(**values)
        self.assertEqual(RSISettings(fixed_long=60,fixed_short=40).fixed_long,60)

    def test_fixed_entries_do_not_require_ml_warmup(self):
        with tempfile.TemporaryDirectory() as d:
            runner=Runner(Engine(Path(d)/'feed.sqlite'))
            cfg=Settings(strategy='rsi_hourly_intrabar')
            preview=dict(time=360000,rsi=50,ml_ready=False,events=[],conflict=False)
            with patch.object(runner,'rsi_preview',side_effect=lambda *a:preview):
                self.assertIsNone(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],360001)['signal'])
                preview['events']=[dict(kind='ml_short_up',side='Long')]
                self.assertEqual(runner.rsi_observation('ETHUSDT',cfg,[dict(close=100)],360005)['signal'],'Long')

    def test_fixed_fetch_does_not_backfill_ml_window(self):
        with tempfile.TemporaryDirectory() as d:
            from types import SimpleNamespace
            rows=candles([100+i%5 for i in range(30)])
            client=SimpleNamespace(candles=lambda *a:rows)
            supervisor=SimpleNamespace(path=Path(d)/'live.sqlite',client=client,load=lambda:{'config':TestnetSettings(strategy='rsi_hourly_intrabar').model_dump()})
            self.assertEqual(FeedAdapter(supervisor).fetch('ETHUSDT','60'),rows)

    def test_existing_settings_switch_once_then_preserve_edits_and_other_modes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fixed.sqlite';create=lambda:TestnetSupervisor(path,clock=lambda:1792152000.)
            service=create();cfg=TestnetSettings(strategy='rsi_hourly_intrabar',timeframe='5',rsi={'calculation_mode':'uploaded_pine','signal_rules':'eight_crossings'})
            service.configure(cfg);old=service.load();old.pop('rsi_fixed_thresholds_v1');service.save(old)
            service=create();state=service.load();rsi=state['config']['rsi']
            self.assertEqual((rsi['calculation_mode'],rsi['fixed_long'],rsi['fixed_short']),('fixed',56.68,42.38))
            self.assertEqual(state['rsi_fixed_previous_calculation']['settings'],old['config']['rsi'])
            self.assertEqual(state['risk'],old['risk']);self.assertEqual(state['config']['timeframe'],'5')
            self.assertEqual(rsi['signal_rules'],'eight_crossings');self.assertTrue(rsi['smooth']);self.assertFalse(state['entries'])
            service.configure(TestnetSettings(**{**state['config'],'rsi':{**rsi,'fixed_long':59,'fixed_short':41}}))
            self.assertEqual(create().load()['config']['rsi']['fixed_long'],59)
            service.configure(cfg);self.assertEqual(create().load()['config']['rsi']['calculation_mode'],'uploaded_pine')

    def test_chart_api_returns_exact_fixed_levels_and_correct_heading_status(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        rows=candles([100+math.sin(i*.25)*20 for i in range(100)])
        config=Settings().model_dump()
        with patch('thoth.app.live.snapshot',return_value={'config':config}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=100*3600):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=60&include_rsi=true')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertIn('Fixed RSI Long/Short thresholds',data['rsi_status'])
        for row in data['rsi_analysis']:
            self.assertEqual(row['calculation_mode'],'fixed')
            self.assertEqual((row['thresholds']['ml_long'],row['thresholds']['ml_short']),(56.68,42.38))
