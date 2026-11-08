"""Reproduce an old internal 4h gap despite successful recent overlap refresh."""
import json, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from thoth.models import Settings
from thoth.runner import Runner
from thoth.testnet_feed import FeedAdapter
from thoth.timeframes import interval_seconds
from thoth.history_backfill import recover


def candle(i,d):return dict(time=i*d,open=100,high=101,low=99,close=100,volume=0)

class HigherHistoryRecoveryTests(unittest.TestCase):
    def fixture(self,tmp,missing_by_tf,unavailable=()):
        path=Path(tmp)/'live.sqlite';path.write_bytes(b'Account execution and risk sentinel')
        runner=Runner(SimpleNamespace(path=path));calls=[];now=[120*86400+10]
        with sqlite3.connect(runner.history_path) as db:
            for tf,missing in missing_by_tf.items():
                d=interval_seconds(tf);last=int(now[0])//d
                db.executemany('INSERT INTO candles VALUES(?,?,?,?)',[('BTCUSDT',tf,i*d,json.dumps(candle(i,d))) for i in range(last-119,last+1) if i not in {last-119+j for j in missing}])
        def fetch(symbol,tf,limit=1000,end=None):
            calls.append((tf,limit,end));d=interval_seconds(tf);last=int(now[0])//d if end is None else end//1000//d
            if end is not None and tf in unavailable:return []
            return [candle(i,d) for i in range(last-(2 if end is None else limit-1),last+1)]
        cfg=Settings(strategy='sr_4h_daily_unfiltered',symbols=['BTCUSDT'])
        service=SimpleNamespace(path=path,clock=lambda:now[0],client=SimpleNamespace(candles=fetch),load=lambda:dict(config=cfg.model_dump()))
        return path,runner,FeedAdapter(service),calls,now

    def test_internal_gaps_both_frames_recover_actual_rows_and_do_not_touch_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,feed,calls,now=self.fixture(tmp,{'240':{10,11},'D':{20}})
            sentinel=path.read_bytes();packet=feed.fetch('BTCUSDT','240')
            for tf in ('240','D'):
                self.assertEqual(len(runner.history('BTCUSDT',tf,packet['primary'][tf])),120)
            self.assertEqual([(tf,n) for tf,n,end in calls if end is not None],[('240',2),('D',1)])
            report=feed.history_recovery_status('BTCUSDT');self.assertFalse(report['active']);self.assertEqual(set(report['frames']),{'240','D'})
            self.assertEqual(path.read_bytes(),sentinel)
            calls.clear();feed.fetch('BTCUSDT','240');self.assertTrue(all(end is None for _,_,end in calls))

    def test_recovered_old_hole_invalidates_later_indicator_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,runner,feed,_,now=self.fixture(tmp,{'240':{10},'D':set()})
            d=14400;stamp=int(now[0])//d*d-d
            with sqlite3.connect(runner.history_path) as db:
                db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',('BTCUSDT','240','fixture','fixture',stamp,'{}'))
                db.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?)',('BTCUSDT','240','fixture','fixture',stamp,'{}'))
            packet=feed.fetch('BTCUSDT','240');runner.history('BTCUSDT','240',packet['primary']['240'])
            with sqlite3.connect(runner.history_path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],0)

    def test_unavailable_4h_retry_not_erased_by_successful_daily_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,runner,feed,calls,now=self.fixture(tmp,{'240':{10},'D':set()},unavailable={'240'})
            packet=feed.fetch('BTCUSDT','240')
            with self.assertRaisesRegex(ValueError,'history contains a gap'):runner.history('BTCUSDT','240',packet['primary']['240'])
            self.assertEqual(len(runner.history('BTCUSDT','D',packet['primary']['D'])),120)
            report=feed.history_recovery_status('BTCUSDT');self.assertTrue(report['active']);self.assertIn('unavailable',report['frames']['240']['reason'])
            calls.clear();now[0]+=5;feed.fetch('BTCUSDT','240');self.assertFalse(any(end is not None for _,_,end in calls))
            now[0]+=60;feed.fetch('BTCUSDT','240');self.assertEqual(sum(end is not None for _,_,end in calls),1)

    def test_missing_long_span_work_is_bounded_and_progress_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite';runner=Runner(SimpleNamespace(path=path));d=14400
            with sqlite3.connect(runner.history_path) as db:
                db.executemany('INSERT INTO candles VALUES(?,?,?,?)',[('BTCUSDT','240',i*d,json.dumps(candle(i,d))) for i in [0,2300]])
            calls=[]
            def fetch(s,tf,limit,end):
                calls.append(limit);last=end//1000//d;return [candle(i,d) for i in range(last-limit+1,last+1)]
            fresh=[candle(2300,d)];merged,status=recover(runner.history_path,'BTCUSDT','240',fresh,fetch,2300*d+10)
            self.assertEqual(calls,[1000,1000]);self.assertTrue(status['active'])
            with self.assertRaisesRegex(ValueError,'gap'):runner.history('BTCUSDT','240',merged)
            merged,status=recover(runner.history_path,'BTCUSDT','240',fresh,fetch,2300*d+15)
            self.assertEqual(calls[-1],299);self.assertFalse(status['active']);self.assertEqual(len(runner.history('BTCUSDT','240',merged)),2301)

    def test_recovery_resumes_after_restart_and_does_not_replay_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,feed,calls,now=self.fixture(tmp,{'240':{10},'D':{10}})
            packet=feed.fetch('BTCUSDT','240');cfg=Settings(strategy='sr_4h_daily_unfiltered')
            # Healthy repaired inputs establish a baseline, never an immediate entry.
            runner.analysis=lambda symbol,c,history,t,**kw:([dict(time=int(t)//interval_seconds(c.timeframe)*interval_seconds(c.timeframe)-interval_seconds(c.timeframe),levels={'long_resistance_major':dict(value=99,pivot_time=0)})],300)
            from unittest.mock import patch
            with patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.scan_cycle=1;self.assertIsNone(runner.sr_unfiltered_observation('BTCUSDT',cfg,packet,now[0])['signal'])
            restarted=FeedAdapter(feed.supervisor);calls.clear();packet=restarted.fetch('BTCUSDT','240')
            self.assertFalse(any(end is not None for _,_,end in calls))
            self.assertEqual(path.read_bytes(),b'Account execution and risk sentinel')

    def test_shifted_cache_or_download_is_not_treated_as_a_missing_real_candle(self):
        for tf in ('240','D'):
            with tempfile.TemporaryDirectory() as tmp:
                _,runner,feed,calls,now=self.fixture(tmp,{tf:{10}});d=interval_seconds(tf)
                with self.assertRaisesRegex(ValueError,'Invalid .* timestamps'):
                    recover(runner.history_path,'BTCUSDT',tf,[{**candle(120,d),'time':120*d+60}],lambda *a,**k:[],now[0])
                with sqlite3.connect(runner.history_path) as db:db.execute('INSERT INTO candles VALUES(?,?,?,?)',('BTCUSDT',tf,60,json.dumps({**candle(0,d),'time':60})))
                with self.assertRaisesRegex(ValueError,'Mixed-resolution'):
                    recover(runner.history_path,'BTCUSDT',tf,[candle(120,d)],lambda *a,**k:[],now[0])
