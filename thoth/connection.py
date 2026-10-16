"""Read-only testnet account diagnostics. No execution activation endpoint."""
import threading
import hashlib
import hmac
from time import time

from .bybit_testnet import TestnetClient
from .credentials import runtime_secret, CredentialError


class Connection:
    def __init__(self, client_factory=TestnetClient, environment='testnet'):
        if environment not in ('testnet','live'):raise ValueError('Unsupported connection environment')
        self.environment=environment
        self.factory = client_factory
        self.lock = threading.Lock()
        self.checked_at = None
        self.verified = False
        self.reason = 'Not checked'
        self.margin_mode = None
        self.last_probe = 0
        self.fingerprint = None

    def _credentials(self):
        prefix='THOTH_BYBIT_LIVE' if self.environment=='live' else 'THOTH_BYBIT_TESTNET'
        return (runtime_secret(prefix+'_KEY'), runtime_secret(prefix+'_SECRET'))

    def status(self):
        try:
            key,secret = self._credentials()
            configured = bool(key and secret)
            source_error = False
            fingerprint = hashlib.sha256((key+'\0'+secret).encode()).digest()
        except CredentialError:
            configured = False
            source_error = True
            fingerprint = None
        verified = bool(self.verified and configured and self.fingerprint is not None
                        and fingerprint is not None and hmac.compare_digest(self.fingerprint,fingerprint)
                        and self.checked_at and time()-self.checked_at<300)
        reason = self.reason
        if source_error:
            reason = 'Credential configuration needs correction'
        elif not configured:
            reason = 'Testnet key and secret are not configured on the server'
        elif self.verified and not verified:
            reason = 'Credentials changed or verification expired; check connection again'
        return dict(environment=self.environment, configured=configured, verified=verified,
                    checked_at=self.checked_at, margin_mode=self.margin_mode if verified else None,
                    reason=reason if self.environment=='testnet' else reason.replace('Testnet','Live').replace('testnet','live'), execution_enabled=False, live_supported=self.environment=='live')

    def verify(self):
        # Serialize checks and throttle requests; never return upstream bodies/errors.
        with self.lock:
            if self.last_probe and time()-self.last_probe < 10:
                return self.status()
            self.last_probe = time()
            self.verified = False
            self.fingerprint = None
            self.margin_mode = None
            try:
                key,secret = self._credentials()
            except CredentialError:
                self.reason = 'Credential configuration needs correction'
                self.checked_at = time()
                return self.status()
            if not key or not secret:
                self.reason = 'Testnet key and secret are not configured on the server'
                self.checked_at = time()
                return self.status()
            client = self.factory(key,secret,armed=False)
            try:
                info = client.request('GET','/v5/user/query-api',{},private=True)
                # Permission response fields vary by account generation. Missing data
                # is not proof that withdrawals are disabled: fail closed.
                permissions = info.get('permissions')
                if not isinstance(permissions,dict) or 'Wallet' not in permissions:
                    self.reason = 'Cannot verify withdrawal permissions; review the testnet key'
                elif not isinstance(permissions['Wallet'],list) or any(
                    'withdraw' in str(permission).lower() for permission in permissions['Wallet']):
                    self.reason = 'Use a key with withdrawals disabled'
                elif not isinstance(info.get('readOnly'),(int,str)) or str(info['readOnly']) not in ('0','1'):
                    self.reason = 'Cannot verify key access mode'
                else:
                    account = client.request('GET','/v5/account/info',{},private=True)
                    mode = account.get('marginMode')
                    self.margin_mode = mode if mode in ('ISOLATED_MARGIN','REGULAR_MARGIN','PORTFOLIO_MARGIN') else 'unknown'
                    self.verified = True
                    self.fingerprint = hashlib.sha256((key+'\0'+secret).encode()).digest()
                    self.reason = 'Testnet account read access verified; execution remains disabled'
            except Exception:
                self.reason = 'Testnet verification failed; check network, key permissions, IP whitelist and server clock'
            self.checked_at = time()
            return self.status()
