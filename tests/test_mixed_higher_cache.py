"""Repair reported off-grid 4h cache rows using archived originals and real bars."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from thoth.history_backfill import quarantine_interval,has_mixed_interval
from thoth.models import Settings
from thoth.runner import Runner
from thoth.testnet_feed import FeedAdapter
from thoth.timeframes import interval_seconds

class MixedHigherCacheTests(unittest.TestCase):
    def fixture(self,tmp,tf,all_bad=False,payload_mismatch=False):
        d=interval_seconds(tf);base=1793210400//d*d
        path=Path(tmp)/'live.sqlite';path.write_bytes(b'Account orders and risk settings unchanged')
        runner=Runner(SimpleNamespace(path=path))
        def row(t,price=100):return dict(time=t,open=price,high=price+1,low=price-1,close=price,volume=10)
        clean=[row(base+i*d) for i in range(-150,51)]
        if not all_bad:runner.history('BTCUSDT',tf,clean)
        wrong=[row(base+(23+i)*60,100.25) for i in range(1000)]
        if payload_mismatch:wrong=[row(base+60,100.25)]
        with sqlite3.connect(runner.history_path) as db:
            db.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?)',[('BTCUSDT',tf,base if payload_mismatch else r['time'],json.dumps(r)) for r in wrong])
            db.execute('INSERT INTO candles VALUES(?,?,?,?)',('ETHUSDT',tf,base,json.dumps(row(base))))
        return path,runner,clean,wrong

    def feed(self,path,tf,clean,bootstrap='full',missing_available=True):
        calls=[];d=interval_seconds(tf);available=[missing_available]
        def fetch(symbol,frame,limit=1000,end=None):
            self.assertEqual(frame,tf);calls.append((limit,end))
            if end is not None:
                if not available[0]:return []
                last=end//1000//d;return [r for r in clean if last-limit+1<=r['time']//d<=last]
            if limit==1000:
                if bootstrap=='empty':return []
                if bootstrap=='wrong':return [{**clean[-1],'time':clean[-1]['time']+60}]
                if bootstrap=='full':return clean
            return clean[-3:]
        now=[clean[-1]['time']+10]
        cfg=Settings(strategy='sr_4h_daily_unfiltered')
        feed=FeedAdapter(SimpleNamespace(path=path,clock=lambda:now[0],client=SimpleNamespace(candles=fetch),load=lambda:dict(config=cfg.model_dump())))
        return feed,calls,now,available

    def test_minute_in_4h_or_daily_cache_archived_and_replaced_including_aligned_overwrites(self):
        for tf in ('240','D'):
            with tempfile.TemporaryDirectory() as tmp:
                path,runner,clean,wrong=self.fixture(tmp,tf);sentinel=path.read_bytes();feed,calls,_,_=self.feed(path,tf,clean)
                result=feed.staged_primary_inputs('BTCUSDT',tf)
                report=feed.history_recovery_status('BTCUSDT');self.assertGreaterEqual(report['quarantined_bars'],1000)
                self.assertEqual([n for n,end in calls if end is None],[3,1000])
                self.assertEqual(runner.history('BTCUSDT',tf,result),clean)
                self.assertEqual(path.read_bytes(),sentinel)
                with sqlite3.connect(runner.history_path) as db:
                    saved=[json.loads(p) for p, in db.execute('SELECT payload FROM candle_quarantine')]
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM candles WHERE symbol=?',('ETHUSDT',)).fetchone()[0],1)
                self.assertTrue(all(r in saved for r in wrong));self.assertFalse(has_mixed_interval(runner.history_path,'BTCUSDT',tf))
                self.assertEqual(quarantine_interval(runner.history_path,'BTCUSDT',tf,0)['quarantined_bars'],0)
                calls.clear();feed.staged_primary_inputs('BTCUSDT',tf);self.assertEqual(calls,[(3,None)])

    def test_failed_or_wrong_bootstrap_preserves_cache_and_restart_retries(self):
        for bootstrap in ('empty','wrong'):
            with tempfile.TemporaryDirectory() as tmp:
                path,runner,clean,_=self.fixture(tmp,'240');feed,_,_,_=self.feed(path,'240',clean,bootstrap)
                with sqlite3.connect(runner.history_path) as db:before=db.execute('SELECT * FROM candles ORDER BY symbol,interval,time').fetchall()
                with self.assertRaises(ValueError):feed.staged_primary_inputs('BTCUSDT','240')
                with sqlite3.connect(runner.history_path) as db:self.assertEqual(db.execute('SELECT * FROM candles ORDER BY symbol,interval,time').fetchall(),before)
                self.assertTrue(has_mixed_interval(runner.history_path,'BTCUSDT','240'))
                restarted,calls,_,_=self.feed(path,'240',clean)
                repaired=restarted.staged_primary_inputs('BTCUSDT','240');self.assertIn((1000,None),calls)
                self.assertEqual(runner.history('BTCUSDT','240',repaired),clean)

    def test_unavailable_historical_replacements_remain_blocked_then_recover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,clean,_=self.fixture(tmp,'240');feed,calls,now,available=self.feed(path,'240',clean,'overlap',False)
            result=feed.staged_primary_inputs('BTCUSDT','240');report=feed.history_recovery_status('BTCUSDT')
            self.assertTrue(report['active']);self.assertIn('unavailable',report['reason'])
            with self.assertRaisesRegex(ValueError,'gap'):runner.history('BTCUSDT','240',result)
            now[0]+=5;calls.clear();feed.staged_primary_inputs('BTCUSDT','240');self.assertFalse(any(end is not None for _,end in calls))
            now[0]+=60;available[0]=True
            self.assertEqual(runner.history('BTCUSDT','240',feed.staged_primary_inputs('BTCUSDT','240')),clean)

    def test_all_bad_cache_bootstraps_without_needing_older_valid_gap_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,clean,_=self.fixture(tmp,'240',all_bad=True);feed,calls,_,_=self.feed(path,'240',clean)
            repaired=feed.staged_primary_inputs('BTCUSDT','240')
            self.assertEqual(runner.history('BTCUSDT','240',repaired),clean);self.assertIn((1000,None),calls)

    def test_payload_timestamp_mismatch_also_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,clean,wrong=self.fixture(tmp,'D',payload_mismatch=True);feed,_,_,_=self.feed(path,'D',clean)
            self.assertTrue(has_mixed_interval(runner.history_path,'BTCUSDT','D'))
            self.assertEqual(runner.history('BTCUSDT','D',feed.staged_primary_inputs('BTCUSDT','D')),clean)
            with sqlite3.connect(runner.history_path) as db:
                self.assertTrue(any(json.loads(p)==wrong[0] for p, in db.execute('SELECT payload FROM candle_quarantine')))

    def test_quarantine_invalidates_levels_and_live_trackers_without_replaying(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,runner,clean,_=self.fixture(tmp,'240');feed,_,now,_=self.feed(path,'240',clean)
            cfg=Settings(strategy='sr_4h_daily_unfiltered')
            from thoth.verification import strategy_fingerprint
            from thoth.sr_unfiltered import observe
            from unittest.mock import patch
            identity=strategy_fingerprint(cfg)+':native-unfiltered-sr';lv={'long_resistance_major':dict(value=99,pivot_time=0)}
            old=[dict(clean[-2]),{**clean[-1],'close':98,'low':97}];tracker={};observe(tracker,old,lv,cfg,now[0]-5,30)
            tracker.update(session=runner.observation_session,scan_cycle=1)
            with sqlite3.connect(runner.history_path) as db:
                db.execute('INSERT INTO intrabar_observations VALUES(?,?,?)',('BTCUSDT',identity,json.dumps(tracker)))
                db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',('BTCUSDT','240','old','old',clean[-2]['time'],'{}'))
            repaired=feed.staged_primary_inputs('BTCUSDT','240');runner.scan_cycle=2
            runner.analysis=lambda *a,**k:([dict(time=clean[-2]['time'],levels=lv)],300)
            with patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                result=runner.sr_unfiltered_observation('BTCUSDT',cfg,dict(primary={'240':repaired,'D':ValueError('Daily unavailable')},sample_received_at=now[0]),now[0])
            self.assertIsNone(result['signal'])
            with sqlite3.connect(runner.history_path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],0)
