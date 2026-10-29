"""Multi-minute scans of completed bars are not intrabar sampling gaps."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings
from thoth.runner import Runner
from test_closed_inverted_ob_sr import NOW,history


class ClosedHourContinuityTests(unittest.TestCase):
    def observer(self,path,strategy):
        runner=Runner(SimpleNamespace(path=str(path)))
        runner.previous_scan_seconds=431
        runner.analysis=lambda symbol,cfg,rows,now,**kwargs:([dict(time=int(now)//3600*3600-3600,signal=None,entry_sources=[])],200)
        return runner,Settings(strategy=strategy,symbols=['BTCUSDT'])

    def test_seven_minute_continuous_scan_emits_new_closed_bar_once(self):
        for strategy,side in [('order_block_sr_1h_close','Long'),('order_block_sr_1h_inverted_close','Short')]:
            with self.subTest(strategy=strategy),tempfile.TemporaryDirectory() as tmp:
                runner,cfg=self.observer(Path(tmp)/'live.sqlite',strategy)
                runner.scan_cycle=1
                self.assertIsNone(runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,history(),NOW-100)['signal'])
                runner.scan_cycle=2
                result=runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,history(),NOW+331)
                self.assertEqual(result['signal'],side,result)
                self.assertEqual(result['sampling']['gap'],431)
                self.assertTrue(result['sampling']['continuous'])
                runner.scan_cycle=3
                with patch('thoth.closed_order_block_sr.evaluate',side_effect=AssertionError('Do not replay/cache-miss the same closed bar')):
                    again=runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,history(),NOW+762)
                self.assertIsNone(again['signal'])

    def test_long_same_bar_scan_reuses_cache_and_clears_stale_baseline_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner,cfg=self.observer(Path(tmp)/'live.sqlite','order_block_sr_1h_close')
            runner.scan_cycle=1;runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,history(),NOW+1)
            runner.scan_cycle=2
            with patch('thoth.closed_order_block_sr.evaluate',side_effect=AssertionError('Cache should cover a continuous same-bar scan')):
                result=runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,history(),NOW+432)
            self.assertIsNone(result['signal']);self.assertNotIn('recovery baseline',result['volume_delta_status'])

    def test_restart_missed_cycle_or_over_hour_gap_never_replays(self):
        for scenario in ('restart','missed_cycle','hour_gap'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'live.sqlite';runner,cfg=self.observer(path,'order_block_sr_1h_close')
                runner.scan_cycle=1;runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,history(),NOW-100)
                now=NOW+331;data=history()
                if scenario=='restart':runner,cfg=self.observer(path,'order_block_sr_1h_close');runner.scan_cycle=1
                elif scenario=='missed_cycle':runner.scan_cycle=3
                else:
                    runner.scan_cycle=2;now=NOW+3601
                    data=[{**r,'time':r['time']+3600} for r in data]
                result=runner.closed_inverted_order_block_sr_observation('BTCUSDT',cfg,data,now)
                self.assertIsNone(result['signal']);self.assertIn('recovery baseline',result['volume_delta_status'])

