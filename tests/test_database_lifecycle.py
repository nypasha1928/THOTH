"""File handles must not depend on GC timing in the live account worker."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from thoth.database import connect


class DatabaseLifecycleTests(unittest.TestCase):
    def test_commit_rollback_and_close_on_both_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite'
            with connect(path) as db:
                db.execute('CREATE TABLE values_saved(value INTEGER)')
                db.execute('INSERT INTO values_saved VALUES(1)')
            with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')
            with self.assertRaisesRegex(ValueError,'fixture rollback'):
                with connect(path) as failed:
                    failed.execute('INSERT INTO values_saved VALUES(2)')
                    raise ValueError('fixture rollback')
            with self.assertRaises(sqlite3.ProgrammingError):failed.execute('SELECT 1')
            with connect(path) as check:self.assertEqual(check.execute('SELECT value FROM values_saved').fetchall(),[(1,)])

    def test_failed_commit_still_closes_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite'
            with connect(path) as db:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                db.execute('CREATE TABLE child(parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
            with self.assertRaises(sqlite3.IntegrityError):
                with connect(path) as failed:
                    failed.execute('PRAGMA foreign_keys=ON')
                    failed.execute('INSERT INTO child VALUES(42)')
            with self.assertRaises(sqlite3.ProgrammingError):failed.execute('SELECT 1')
            with connect(path) as check:self.assertEqual(check.execute('SELECT COUNT(*) FROM child').fetchone()[0],0)

    @unittest.skipUnless(Path('/proc/self/fd').exists(),'Linux descriptor accounting required')
    def test_large_journal_under_low_file_limit_with_gc_disabled(self):
        # Isolate resource limits/GC from the test runner and all other workers.
        script='''
import gc,json,os,resource,tempfile
from pathlib import Path
from thoth.database import connect
from thoth.execution import ExecutionJournal
from thoth.bybit_testnet import TestnetClient
with tempfile.TemporaryDirectory() as tmp:
    journal=ExecutionJournal(Path(tmp)/'execution.sqlite',TestnetClient('fixture','fixture',armed=False))
    with journal.db() as db:
        db.executemany('INSERT INTO execution VALUES(?,?,?,?,?,?)',[(str(i),json.dumps(dict(symbol='BTCUSDT',reduceOnly=False)),'closed','0.4',json.dumps(dict(thoth_strategy='order_block_sr_1h_early')),json.dumps(dict(reason='Position flat'))) for i in range(3000)])
    gc.collect();gc.disable()
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE,(min(128,soft),hard))
    before=len(os.listdir('/proc/self/fd'))
    for _ in range(20):
        rows=journal.records()
        assert len(rows)==3000 and rows[0]['payload']['symbol']=='BTCUSDT'
        assert rows[-1]['instrument']['thoth_strategy']=='order_block_sr_1h_early'
        assert journal.get('2999')==rows[-1]
        with connect(Path(tmp)/'observations.sqlite') as db:db.execute('SELECT 1').fetchone()
    after=len(os.listdir('/proc/self/fd'))
    assert after<=before+1,(before,after)
    print(json.dumps(dict(rows=3000,reads=20,fd_before=before,fd_after=after)))
'''
        result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout);self.assertLessEqual(data['fd_after'],data['fd_before']+1)

    def test_failed_open_records_curated_resource_counts_without_path(self):
        from thoth.diagnostics import failure_details,failure_summary
        try:
            with connect('/nonexistent-thoth-parent/private-account.sqlite'):pass
        except sqlite3.Error as exc:
            details=failure_details(exc,'account_monitor')
        self.assertEqual(details['sqlite_code'],sqlite3.SQLITE_CANTOPEN)
        self.assertIn('database_resources',details)
        self.assertNotIn('private-account',str(details))
        self.assertIn('Unable to open database',failure_summary(details))
