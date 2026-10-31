import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from thoth.late_hour_comparison import LateHourComparison
from thoth.models import Settings
from thoth.runner import Runner
from test_late_hour_comparison import BAR,history,raw

class EarlyComparisonTests(unittest.TestCase):
    def test_early_samples_and_closed_confirmation_continue_without_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+3301];live=Path(tmp)/'live.sqlite';cfg=Settings(strategy='order_block_sr_1h_early',symbols=['BTCUSDT'],pivot_long=1,pivot_short=1)
            observer=LateHourComparison(Path(tmp)/'comparison.sqlite',live,clock=lambda:now[0])
            observer.submit('BTCUSDT',cfg,history(),now[0]);self.assertEqual(observer.inputs.qsize(),1)
            with patch('thoth.late_hour_comparison.select',return_value=raw()):observer.process(observer.inputs.get_nowait())
            row=observer.report()['items'][0];self.assertEqual(row['strategy'],cfg.strategy);self.assertEqual(row['signal'],'Long')
            now[0]=BAR+3601;rows=history()+[dict(time=BAR+3600,open=100.5,high=101,low=100,close=100.5,volume=1)]
            with patch('thoth.late_hour_comparison.select',return_value=raw()):observer.process(('BTCUSDT',cfg,rows,now[0]))
            self.assertTrue(observer.report()['items'][0]['closed']['same_direction'])
            with sqlite3.connect(live.with_suffix('.execution.sqlite')) as db:
                db.execute('CREATE TABLE execution(payload TEXT,phase TEXT,instrument TEXT,result TEXT)')
                for profile,bar in [(cfg.strategy,BAR),(cfg.strategy,BAR-3600),('order_block_sr_1h_close',BAR)]:
                    meta=dict(thoth_strategy=profile,thoth_early_window=bar,thoth_entry_diagnostics=dict(signal_bar_closed_at=BAR+3600))
                    db.execute('INSERT INTO execution VALUES(?,?,?,?)',(json.dumps(dict(symbol='BTCUSDT',side='Buy',reduceOnly=False,orderLinkId='fixture')),'protected',json.dumps(meta),'{}'))
            self.assertEqual(len(observer.report()['items'][0]['live_orders']),1)
            self.assertFalse(hasattr(observer,'client'))

    def test_early_uses_its_saved_limits_and_runner_callback_is_nonblocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy='order_block_sr_1h_early',symbols=['BTCUSDT'],pivot_long=1,pivot_short=1,early_max_opposing_wick_pct=10)
            observer=LateHourComparison(Path(tmp)/'comparison.sqlite',Path(tmp)/'live.sqlite',clock=lambda:BAR+3301)
            with patch('thoth.late_hour_comparison.select',return_value=raw()):observer.process(('BTCUSDT',cfg,history(),BAR+3301))
            row=observer.report()['items'][0];self.assertIsNone(row['signal']);self.assertEqual(row['latest']['rules']['max_opposing_wick_pct'],10)
            runner=Runner(SimpleNamespace(path=Path(tmp)/'live.sqlite'));mock=Mock();mock.submit.side_effect=RuntimeError('Observer unavailable');runner.late_hour_observer=mock
            runner.scan_cycle=1
            result=runner.early_hour_observation('BTCUSDT',cfg,history(),BAR+3200)
            self.assertIsNone(result['signal']);mock.submit.assert_called_once()
