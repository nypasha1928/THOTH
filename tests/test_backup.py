import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from tools.backup import backup
from thoth.engine import Engine
from thoth.models import Settings


class BackupTests(unittest.TestCase):
    def test_backup_restores_settings_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'paper.sqlite'
            engine=Engine(source)
            engine.configure(Settings(daily_loss_pct=5))
            destination=Path(d)/'backup'
            backup(source,destination)
            restored=Engine(destination/'paper.sqlite').snapshot()
            self.assertEqual(restored['settings']['daily_loss_pct'],5)
            self.assertEqual(restored['balance'],250)
            self.assertEqual((destination/'paper.sqlite').stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):backup(source,destination)


class MultiDatabaseBackupTests(unittest.TestCase):
    def test_journal_and_risk_restore_without_credentials(self):
        from tools.backup_data import backup,verify,restore
        from thoth.testnet import TestnetSupervisor
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'data';data.mkdir()
            Engine(data/'paper.sqlite').configure(Settings(daily_loss_pct=7))
            supervisor=TestnetSupervisor(data/'testnet.sqlite')
            supervisor.state['risk']['daily_stop']=True;supervisor.save()
            (data/'secret.txt').write_text('fixture credential must not be backed up')
            backup(data,root/'backup');verify(root/'backup');restore(root/'backup',root/'restored')
            self.assertEqual(Engine(root/'restored'/'paper.sqlite').snapshot()['settings']['daily_loss_pct'],7)
            self.assertTrue(TestnetSupervisor(root/'restored'/'testnet.sqlite').load()['risk']['daily_stop'])
            self.assertFalse((root/'backup'/'secret.txt').exists())
            with (root/'backup'/'paper.sqlite').open('ab') as output:output.write(b'changed')
            with self.assertRaises(ValueError):verify(root/'backup')
