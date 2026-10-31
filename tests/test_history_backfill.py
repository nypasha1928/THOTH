"""Read-only Bybit gap recovery uses only synthetic candle pages."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from thoth.history_backfill import recover
from thoth.runner import Runner
from thoth.models import Settings
from thoth.testnet_feed import FeedAdapter

STEP=3600

def candle(i):return dict(time=i*STEP,open=100,high=101,low=99,close=100,volume=10)

class HistoricalRecoveryTests(unittest.TestCase):
    def setup_history(self,tmp,total,missing):
        path=Path(tmp)/'live.sqlite';runner=Runner(SimpleNamespace(path=path))
        with sqlite3.connect(runner.history_path) as db:
            db.executemany('INSERT INTO candles VALUES(?,?,?,?)',[('ETHUSDT','60',i*STEP,json.dumps(candle(i))) for i in range(total) if i not in missing])
        return path,runner
    def fetch(self,calls,empty=False):
        def fetch(symbol,interval,limit=1000,end=None):
            calls.append(dict(symbol=symbol,interval=interval,limit=limit,end=end))
            if empty:return []
            last=end//1000//STEP
            return [candle(i) for i in range(last-limit+1,last+1)]
        return fetch
    def test_old_internal_hole_repairs_without_erasing_history_or_inventing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner=self.setup_history(tmp,120,{10});fresh=[candle(i) for i in (117,118,119)]
            with self.assertRaisesRegex(ValueError,'history contains a gap'):runner.history('ETHUSDT','60',fresh)
            calls=[];merged,status=recover(runner.history_path,'ETHUSDT','60',fresh,self.fetch(calls),120*STEP)
            self.assertEqual(len(calls),1);self.assertEqual(calls[0]['limit'],1)
            self.assertFalse(status['active']);self.assertEqual(status['recovered_bars'],1)
            # Download function itself never changes the database.
            with sqlite3.connect(runner.history_path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM candles').fetchone()[0],119)
            repaired=runner.history('ETHUSDT','60',merged)
            self.assertEqual(repaired,[candle(i) for i in range(120)])
            calls.clear();again,status=recover(runner.history_path,'ETHUSDT','60',fresh,self.fetch(calls),120*STEP+1)
            self.assertEqual(again,fresh);self.assertFalse(calls)
    def test_bounded_partial_progress_persists_and_next_scan_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner=self.setup_history(tmp,2301,set(range(10,2211)))
            fresh=[candle(i) for i in (2298,2299,2300)];calls=[]
            merged,status=recover(runner.history_path,'ETHUSDT','60',fresh,self.fetch(calls),2301*STEP)
            self.assertEqual(len(calls),2);self.assertTrue(status['active']);self.assertEqual(status['recovered_bars'],2000)
            with self.assertRaisesRegex(ValueError,'gap'):runner.history('ETHUSDT','60',merged)
            calls.clear();merged,status=recover(runner.history_path,'ETHUSDT','60',fresh,self.fetch(calls),2301*STEP+5)
            self.assertEqual(len(calls),1);self.assertFalse(status['active']);self.assertEqual(status['recovered_bars'],201)
            self.assertEqual(len(runner.history('ETHUSDT','60',merged)),2301)
    def test_unavailable_missing_candle_stays_blocked_and_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner=self.setup_history(tmp,120,{10});fresh=[candle(119)];calls=[]
            merged,status=recover(runner.history_path,'ETHUSDT','60',fresh,self.fetch(calls,True),120*STEP)
            self.assertEqual(merged,fresh);self.assertTrue(status['active']);self.assertEqual(status['retry_after'],120*STEP+60)
            with self.assertRaisesRegex(ValueError,'gap'):runner.history('ETHUSDT','60',merged)
            recover(runner.history_path,'ETHUSDT','60',fresh,self.fetch(calls,True),120*STEP+5,status)
            self.assertEqual(len(calls),1)
            for bad in ([{**candle(10),'time':10*STEP+1}],[{**candle(10),'high':98}],[candle(119)]):
                merged,s=recover(runner.history_path,'ETHUSDT','60',fresh,lambda *a,**k:bad,120*STEP+61)
                self.assertEqual(merged,fresh);self.assertTrue(s['active'])
    def test_normal_and_inverted_feed_repair_but_other_profiles_do_not_change(self):
        for strategy in ('order_block_sr_1h_close','order_block_sr_1h_inverted_close'):
            with self.subTest(strategy=strategy),tempfile.TemporaryDirectory() as tmp:
                path,runner=self.setup_history(tmp,120,{10});calls=[]
                def fetch(symbol,interval,limit=1000,end=None):
                    if end is None:
                        calls.append(dict(fresh=True,limit=limit));return [candle(i) for i in (117,118,119)]
                    return self.fetch(calls)(symbol,interval,limit,end)
                supervisor=SimpleNamespace(path=path,clock=lambda:119*STEP+10,client=SimpleNamespace(candles=fetch),load=lambda:dict(config=Settings(strategy=strategy,symbols=['ETHUSDT']).model_dump()))
                feed=FeedAdapter(supervisor);rows=feed.fetch('ETHUSDT','60')
                self.assertEqual(calls[0]['limit'],3);self.assertEqual(len(calls),2)
                self.assertEqual(len(runner.history('ETHUSDT','60',rows)),120)
                self.assertFalse(feed.history_recovery_status('ETHUSDT')['active'])
    def test_repair_never_replays_a_closed_hour_signal_after_a_failed_scan(self):
        from test_closed_inverted_ob_sr import NOW,history
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite';runner=Runner(SimpleNamespace(path=path));cfg=Settings(strategy='order_block_sr_1h_close')
            runner.analysis=lambda *a,**k:([dict(time=int(a[3])//3600*3600-3600,signal=None,entry_sources=[])],200)
            runner.scan_cycle=1;runner.closed_inverted_order_block_sr_observation('ETHUSDT',cfg,history(),NOW-5)
            # A missing bar makes scan 2 fail, even with the newest candle available.
            missing=history()[10]['time']
            with sqlite3.connect(runner.history_path) as db:db.execute('DELETE FROM candles WHERE symbol=? AND interval=? AND time=?',('ETHUSDT','60',missing))
            runner.scan_cycle=2
            with self.assertRaisesRegex(ValueError,'gap'):runner.closed_inverted_order_block_sr_observation('ETHUSDT',cfg,history()[-3:],NOW+1)
            runner.scan_cycle=3;result=runner.closed_inverted_order_block_sr_observation('ETHUSDT',cfg,history(),NOW+6)
            self.assertIsNone(result['signal']);self.assertIn('recovery baseline',result['volume_delta_status'])
