"""Repair the reported minute-in-hour cache without manufacturing hourly bars."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from thoth.history_backfill import quarantine_hourly,recover
from thoth.models import Settings
from thoth.runner import Runner
from thoth.testnet_feed import FeedAdapter
from test_history_backfill import candle,STEP

class MixedHourlyTests(unittest.TestCase):
    def fixture(self,tmp):
        path=Path(tmp)/'live.sqlite';path.write_bytes(b'Execution and risk sentinel, unchanged')
        runner=Runner(SimpleNamespace(path=path));base=1793210400
        rows=[candle(i) for i in range(base//STEP-150,base//STEP+51)]
        runner.history('ETHUSDT','60',rows)
        minutes=[dict(time=base+i*60,open=100,high=101,low=99,close=100.25,volume=1) for i in range(23,1023)]
        with sqlite3.connect(runner.history_path) as db:
            db.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?)',[('ETHUSDT','60',r['time'],json.dumps(r)) for r in minutes])
        return path,runner,rows,minutes

    def feed(self,path,rows,empty=False,strategy='order_block_sr_1h_early'):
        calls=[]
        def fetch(symbol,interval,limit=1000,end=None):
            calls.append(dict(limit=limit,end=end))
            if end is None:return rows[-3:]
            if empty:return []
            last=end//1000//STEP
            return [candle(i) for i in range(last-limit+1,last+1)]
        cfg=Settings(strategy=strategy,symbols=['ETHUSDT'])
        feed=FeedAdapter(SimpleNamespace(path=path,clock=lambda:rows[-1]['time']+10,
            client=SimpleNamespace(candles=fetch),load=lambda:dict(config=cfg.model_dump())))
        return feed,calls

    def test_exact_minute_spacing_case_repairs_real_hour_bars_and_archives_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,rows,minutes=self.fixture(tmp)
            with self.assertRaisesRegex(ValueError,'gap'):runner.history('ETHUSDT','60',rows[-3:])
            before=path.read_bytes();feed,calls=self.feed(path,rows)
            result=feed.fetch('ETHUSDT','60');status=feed.history_recovery_status('ETHUSDT')
            self.assertGreaterEqual(status['quarantined_bars'],1000)
            self.assertGreater(status['recovered_bars'],0);self.assertFalse(status['active'])
            self.assertEqual(len(calls),2)
            repaired=runner.history('ETHUSDT','60',result)
            self.assertEqual(repaired,rows) # Includes hourly-aligned minute overwrites.
            self.assertEqual(path.read_bytes(),before)
            with sqlite3.connect(runner.history_path) as db:
                saved=[json.loads(p) for p, in db.execute('SELECT payload FROM candle_quarantine')]
            self.assertTrue(all(r in saved for r in minutes))
            self.assertEqual(quarantine_hourly(runner.history_path,'ETHUSDT',rows[-1]['time'])['quarantined_bars'],0)
            calls.clear();feed.fetch('ETHUSDT','60');self.assertEqual(len(calls),1)

    def test_unavailable_real_replacement_keeps_entries_blocked_and_retries_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,rows,_=self.fixture(tmp);feed,calls=self.feed(path,rows,empty=True)
            result=feed.fetch('ETHUSDT','60');status=feed.history_recovery_status('ETHUSDT')
            self.assertTrue(status['active']);self.assertIn('unavailable',status['reason'])
            with self.assertRaisesRegex(ValueError,'gap'):runner.history('ETHUSDT','60',result)
            runner=Runner(SimpleNamespace(path=path));feed,calls=self.feed(path,rows)
            self.assertEqual(runner.history('ETHUSDT','60',feed.fetch('ETHUSDT','60')),rows)

    def test_wrong_interval_download_never_writes_or_heals_hourly_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,runner,rows,minutes=self.fixture(tmp)
            with sqlite3.connect(runner.history_path) as db:
                before=db.execute('SELECT COUNT(*) FROM candles').fetchone()[0]
            for incoming in (minutes,[minutes[0]],[rows[-1],rows[-1]]):
                with self.assertRaisesRegex(ValueError,'Hourly'):runner.history('ETHUSDT','60',incoming)
                with self.assertRaisesRegex(ValueError,'Hourly'):recover(runner.history_path,'ETHUSDT','60',incoming,Mock(),rows[-1]['time'])
            with sqlite3.connect(runner.history_path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],before)

    def test_repair_invalidates_indicator_cache_but_preserves_hourly_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,rows,_=self.fixture(tmp)
            runner.early_hour_states={('ETHUSDT','fixture'):object(),('SOLUSDT','fixture'):object()}
            with sqlite3.connect(runner.history_path) as db:
                db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',('ETHUSDT','60','strategy','source',rows[-2]['time'],'{}'))
                db.execute('INSERT INTO intrabar_observations VALUES(?,?,?)',('ETHUSDT','fixture',json.dumps(dict(session=runner.observation_session,fired=True,bar=rows[-1]['time']))))
            feed,_=self.feed(path,rows);runner.history('ETHUSDT','60',feed.fetch('ETHUSDT','60'))
            self.assertNotIn(('ETHUSDT','fixture'),runner.early_hour_states);self.assertIn(('SOLUSDT','fixture'),runner.early_hour_states)
            with sqlite3.connect(runner.history_path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],0)
                tracker=json.loads(db.execute('SELECT payload FROM intrabar_observations').fetchone()[0])
            self.assertTrue(tracker['fired']);self.assertEqual(tracker['session'],'hourly-cache-repaired')

    def test_strategy_timeframe_switch_during_download_cannot_mix_candle_intervals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,_,rows,_=self.fixture(tmp);feed,_=self.feed(path,rows,strategy='ob_sr_minute_hourly')
            with self.assertRaisesRegex(ValueError,'timeframe changed'):feed.fetch('ETHUSDT','60')
