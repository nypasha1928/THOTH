import sqlite3
import unittest
from thoth.diagnostics import failure_details, failure_summary


class DatabaseDiagnosticsTests(unittest.TestCase):
    def test_real_open_failure_is_saved_without_path(self):
        try:
            sqlite3.connect('/nonexistent-thoth-parent/private-account.sqlite')
        except sqlite3.Error as exc:
            details=failure_details(exc,'account_monitor')
        self.assertEqual(details['error_type'],'DatabaseError')
        self.assertEqual(details['sqlite_code'],sqlite3.SQLITE_CANTOPEN)
        self.assertEqual(details['database_reason'],'Unable to open database')
        self.assertIn('Unable to open database',failure_summary(details))
        self.assertNotIn('private-account',str(details))

    def test_extended_code_and_no_raw_exception_text(self):
        exc=sqlite3.OperationalError('secret SQL and account data')
        exc.sqlite_errorcode=sqlite3.SQLITE_BUSY | (2 << 8)
        details=failure_details(exc,'account_monitor')
        self.assertEqual(details['database_reason'],'Database busy')
        self.assertEqual(details['sqlite_code'],517)
        self.assertNotIn('secret',str(details))

    def test_missing_code_is_not_guessed(self):
        details=failure_details(sqlite3.OperationalError('database is locked'),'account_monitor')
        self.assertEqual(details['error_type'],'DatabaseError')
        self.assertNotIn('database_reason',details)
        self.assertNotIn('sqlite_code',details)
