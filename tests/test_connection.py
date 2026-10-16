import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from thoth.credentials import runtime_secret, CredentialError
from thoth.connection import Connection


class CredentialTests(unittest.TestCase):
    def test_private_file_and_ambiguous_sources(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'secret';path.write_text('fixture-only-value\n');path.chmod(0o400)
            with patch.dict(os.environ,{'THOTH_FIXTURE_FILE':str(path)},clear=True):
                self.assertEqual(runtime_secret('THOTH_FIXTURE'),'fixture-only-value')
                with patch.dict(os.environ,{'THOTH_FIXTURE':'other'}):
                    with self.assertRaises(CredentialError):runtime_secret('THOTH_FIXTURE')
                path.chmod(0o666)
                with self.assertRaises(CredentialError):runtime_secret('THOTH_FIXTURE')

    def test_missing_file_error_does_not_include_contents_or_path(self):
        with patch.dict(os.environ,{'THOTH_FIXTURE_FILE':'/not/a/real/file'},clear=True):
            with self.assertRaises(CredentialError) as error:runtime_secret('THOTH_FIXTURE')
            self.assertNotIn('/not/a/real/file',str(error.exception))


class ConnectionTests(unittest.TestCase):
    def credentials(self):
        return patch.dict(os.environ,{'THOTH_BYBIT_TESTNET_KEY':'fixture-key','THOTH_BYBIT_TESTNET_SECRET':'fixture-secret'},clear=True)

    def test_missing_credentials_never_make_request(self):
        factory=Mock()
        with patch.dict(os.environ,{},clear=True):
            result=Connection(factory).verify()
        self.assertFalse(result['verified']);factory.assert_not_called()

    def test_probe_is_read_only_and_never_exposes_account_fields(self):
        client=Mock()
        client.request.side_effect=[{'permissions':{'Wallet':[]},'readOnly':0,'apiKey':'fixture-key','userID':'private-id'}, {'marginMode':'ISOLATED_MARGIN','accountID':'private'}]
        factory=Mock(return_value=client)
        with self.credentials():result=Connection(factory).verify()
        factory.assert_called_once_with('fixture-key','fixture-secret',armed=False)
        self.assertTrue(result['verified']);self.assertFalse(result['execution_enabled'])
        self.assertEqual([c.args[0] for c in client.request.call_args_list],['GET','GET'])
        text=json.dumps(result)
        for private in ('fixture-key','fixture-secret','private-id','accountID'):self.assertNotIn(private,text)

    def test_withdrawal_permissions_and_unknown_metadata_fail_closed(self):
        for permissions in ({'Wallet':['Withdraw']},{},None):
            client=Mock();client.request.return_value={'permissions':permissions,'readOnly':0}
            with self.credentials():result=Connection(Mock(return_value=client)).verify()
            self.assertFalse(result['verified']);self.assertEqual(client.request.call_count,1)

    def test_rotation_and_expiry_invalidate_verification(self):
        client=Mock();client.request.side_effect=[{'permissions':{'Wallet':[]},'readOnly':1},{'marginMode':'REGULAR_MARGIN'}]
        connection=Connection(Mock(return_value=client))
        with self.credentials():
            self.assertTrue(connection.verify()['verified'])
            with patch.dict(os.environ,{'THOTH_BYBIT_TESTNET_SECRET':'rotated-fixture'}):
                self.assertFalse(connection.status()['verified'])
                self.assertIsNone(connection.status()['margin_mode'])
            with patch('thoth.connection.time',return_value=connection.checked_at+301):
                self.assertFalse(connection.status()['verified'])

    def test_upstream_error_is_redacted_and_rechecks_throttled(self):
        client=Mock();client.request.side_effect=RuntimeError('fixture-secret upstream body')
        connection=Connection(Mock(return_value=client))
        with self.credentials():
            result=connection.verify();connection.verify()
        self.assertFalse(result['verified'])
        self.assertNotIn('fixture-secret',json.dumps(result))
        self.assertEqual(client.request.call_count,1)
