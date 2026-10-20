import _environment
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from thoth.app import app
from thoth import auth
from thoth.security import SecurityStore,totp,password_matches

PASSWORD='fixture-bootstrap-password'
NEW='fixture-new-password-2026'


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1792454400.]
        self.store=SecurityStore(self.temp.name,clock=lambda:self.now[0])
        self.patches=[patch.object(auth,'_store',self.store),patch.object(auth,'PASSWORD',PASSWORD)]
        for p in self.patches:p.start()
        auth._attempts.clear();self.client=TestClient(app,follow_redirects=False)
        self.login()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup();auth._attempts.clear()

    def login(self,password=PASSWORD,code=''):
        return self.client.post('/api/login',json=dict(password=password,code=code))

    def post(self,action,**data):return self.client.post('/api/security/'+action,json=data)

    def codes(self):
        response=self.post('recovery-codes',password=PASSWORD)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['recovery_codes']

    def enable_totp(self):
        codes=self.codes()
        pending=self.post('totp/setup',password=PASSWORD).json()
        code=totp(pending['secret'],int(self.now[0]//30))
        response=self.post('totp/confirm',challenge_id=pending['challenge_id'],code=code)
        self.assertEqual(response.status_code,200,response.text)
        return codes,pending

    def test_password_change_invalidates_other_sessions_and_old_bootstrap(self):
        old_cookie=self.client.cookies.get('thoth_session')
        self.assertEqual(self.post('password',password='wrong',new_password=NEW).status_code,400)
        self.assertEqual(self.post('password',password=PASSWORD,new_password='short').status_code,400)
        response=self.post('password',password=PASSWORD,new_password=NEW)
        self.assertEqual(response.status_code,200,response.text)
        other=TestClient(app,follow_redirects=False)
        other.cookies.set('thoth_session',old_cookie)
        self.assertEqual(other.get('/api/security/status').status_code,401)
        self.assertEqual(self.client.get('/api/security/status').status_code,200)
        self.assertEqual(self.login(PASSWORD).status_code,401)
        self.assertEqual(self.login(NEW).status_code,200)
        state=SecurityStore(self.temp.name).read()
        self.assertNotIn(NEW,json.dumps(state))
        self.assertTrue(password_matches(NEW,state['password'],PASSWORD))

    def test_recovery_codes_one_use_hash_only_and_full_recovery_revokes_factors(self):
        codes,pending=self.enable_totp()
        with self.store.edit() as (state,db):state['passkeys']=[dict(id='fixture',name='old',created=1)]
        db_text=self.store.path.read_bytes()
        self.assertNotIn(codes[0].encode(),db_text)
        self.assertNotIn(pending['secret'].encode(),db_text)
        old_cookie=self.client.cookies.get('thoth_session')
        self.client.cookies.clear()
        response=self.post('recover',recovery_code=codes[0],new_password=NEW)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['recovery_codes']),10)
        self.assertEqual(self.post('recover',recovery_code=codes[0],new_password=NEW).status_code,400)
        state=self.store.read();self.assertFalse(state['totp_secret']);self.assertFalse(state['passkeys'])
        self.assertEqual(self.login(NEW).status_code,200)
        other=TestClient(app);other.cookies.set('thoth_session',old_cookie)
        self.assertEqual(other.get('/api/security/status').status_code,401)

    def test_totp_enrollment_confirmation_replay_and_backup_login(self):
        codes,pending=self.enable_totp()
        self.assertTrue(self.store.read()['totp_secret'])
        self.client.cookies.clear()
        self.assertEqual(self.login().status_code,401)
        code=totp(pending['secret'],int(self.now[0]//30))
        self.assertEqual(self.login(code=code).status_code,401)  # Enrollment consumed it.
        self.now[0]+=30;fresh=totp(pending['secret'],int(self.now[0]//30))
        self.assertEqual(self.login(code=fresh).status_code,200)
        self.assertEqual(self.login(code=fresh).status_code,401)
        self.assertEqual(self.login(code=codes[0]).status_code,200)
        self.assertEqual(self.login(code=codes[0]).status_code,401)
        self.assertEqual(len(self.store.read()['recovery']),9)

    def test_totp_setup_expiry_and_session_binding(self):
        self.codes()
        setup=self.post('totp/setup',password=PASSWORD).json()
        old_cookie=self.client.cookies.get('thoth_session')
        self.login()
        code=totp(setup['secret'],int(self.now[0]//30))
        self.assertEqual(self.post('totp/confirm',challenge_id=setup['challenge_id'],code=code).status_code,400)
        self.client.cookies.set('thoth_session',old_cookie)
        self.now[0]+=601
        code=totp(setup['secret'],int(self.now[0]//30))
        self.assertEqual(self.post('totp/confirm',challenge_id=setup['challenge_id'],code=code).status_code,400)
        self.assertFalse(self.store.read()['totp_secret'])

    def test_recovery_codes_required_and_status_does_not_expose_secrets(self):
        self.assertEqual(self.post('totp/setup',password=PASSWORD).status_code,400)
        self.assertEqual(self.post('passkey/register/options',password=PASSWORD).status_code,400)
        self.codes()
        data=self.client.get('/api/security/status').json()
        self.assertEqual(data['recovery_codes_remaining'],10)
        self.assertNotIn('password',data);self.assertNotIn('totp_secret',data)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/security/status').status_code,401)
        self.assertEqual(self.client.get('/passkeys.js').status_code,200)
        self.assertEqual(self.client.get('/api/security/login-options').status_code,200)

    def test_no_get_mutations_and_rate_limit_survives_public_options(self):
        self.assertEqual(self.client.get('/api/security/recover').status_code,405)
        self.codes();self.client.cookies.clear()
        for _ in range(5):
            self.assertEqual(self.login('wrong').status_code,401)
            self.client.get('/api/security/login-options')
        self.assertEqual(self.login('wrong').status_code,429)

    def test_passkey_options_bind_rp_and_require_verification(self):
        self.codes()
        result=self.post('passkey/register/options',password=PASSWORD,name='Android').json()
        self.assertEqual(result['options']['rp']['id'],'testserver')
        self.assertEqual(result['options']['authenticatorSelection']['userVerification'],'required')
        self.assertEqual(result['options']['authenticatorSelection']['residentKey'],'required')
        self.assertEqual(self.post('passkey/register/verify',challenge_id=result['challenge_id'],credential={}).status_code,400)
        self.assertFalse(self.store.read()['passkeys'])

    def test_rfc_totp_vector(self):
        secret=base64.b32encode(b'12345678901234567890').decode()
        self.assertEqual(totp(secret,59//30),'287082')

    def registration(self,options,origin='http://testserver',uv=True):
        import cbor2,hashlib,secrets
        from cryptography.hazmat.primitives.asymmetric import ec
        from webauthn.helpers import bytes_to_base64url as b64
        key=ec.generate_private_key(ec.SECP256R1());numbers=key.public_key().public_numbers()
        cose={1:2,3:-7,-1:1,-2:numbers.x.to_bytes(32,'big'),-3:numbers.y.to_bytes(32,'big')}
        identifier=secrets.token_bytes(16)
        auth_data=hashlib.sha256(b'testserver').digest()+bytes([0x45 if uv else 0x41])+bytes(4)+bytes(16)+len(identifier).to_bytes(2,'big')+identifier+cbor2.dumps(cose)
        client=json.dumps(dict(type='webauthn.create',challenge=options['challenge'],origin=origin)).encode()
        credential=dict(id=b64(identifier),rawId=b64(identifier),type='public-key',response=dict(clientDataJSON=b64(client),attestationObject=b64(cbor2.dumps(dict(fmt='none',attStmt={},authData=auth_data)))))
        return credential,key

    def assertion(self,options,identifier,key,origin='http://testserver',uv=True):
        import hashlib
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from webauthn.helpers import bytes_to_base64url as b64
        client=json.dumps(dict(type='webauthn.get',challenge=options['challenge'],origin=origin)).encode()
        authenticator=hashlib.sha256(b'testserver').digest()+bytes([5 if uv else 1])+(1).to_bytes(4,'big')
        signature=key.sign(authenticator+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256()))
        return dict(id=identifier,rawId=identifier,type='public-key',response=dict(clientDataJSON=b64(client),authenticatorData=b64(authenticator),signature=b64(signature),userHandle=b64(bytes.fromhex(self.store.read()['user_id']))))

    def test_real_webauthn_signature_origin_uv_and_replay_checks(self):
        self.codes()
        start=self.post('passkey/register/options',password=PASSWORD).json()
        bad,key=self.registration(start['options'],origin='https://attacker.invalid')
        self.assertEqual(self.post('passkey/register/verify',challenge_id=start['challenge_id'],credential=bad).status_code,400)
        credential,key=self.registration(start['options'])
        self.assertEqual(self.post('passkey/register/verify',challenge_id=start['challenge_id'],credential=credential).status_code,200)
        self.client.cookies.clear()
        start=self.post('passkey/login/options').json()
        for origin,uv in [('https://attacker.invalid',True),('http://testserver',False)]:
            assertion=self.assertion(start['options'],credential['id'],key,origin=origin,uv=uv)
            self.assertEqual(self.post('passkey/login/verify',challenge_id=start['challenge_id'],credential=assertion).status_code,400)
            self.assertEqual(self.client.get('/api/security/status').status_code,401)
        valid=self.assertion(start['options'],credential['id'],key)
        self.assertEqual(self.post('passkey/login/verify',challenge_id=start['challenge_id'],credential=valid).status_code,200)
        self.assertEqual(self.store.read()['passkeys'][0]['sign_count'],1)
        self.assertEqual(self.post('passkey/login/verify',challenge_id=start['challenge_id'],credential=valid).status_code,400)

    def test_authenticator_survives_private_backup_restore(self):
        from tools.backup_data import backup,restore,verify
        codes,pending=self.enable_totp()
        with tempfile.TemporaryDirectory() as directory:
            backup(self.temp.name,Path(directory)/'backup')
            restore(Path(directory)/'backup',Path(directory)/'restored')
            restored=SecurityStore(Path(directory)/'restored',clock=lambda:self.now[0])
            self.now[0]+=30
            with restored.edit() as (state,db):
                restored.authenticate(state,PASSWORD,totp(pending['secret'],int(self.now[0]//30)),PASSWORD)
            self.assertEqual((Path(directory)/'restored'/'security.key').stat().st_mode&0o777,0o600)
            (Path(directory)/'backup'/'security.key').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):verify(Path(directory)/'backup')

    def test_ssh_recovery_changes_only_security_store(self):
        from thoth.security_recovery import main
        path=Path(self.temp.name)/'live.sqlite'
        path.write_bytes(b'trading-state-sentinel')
        self.enable_totp()
        with patch.dict('os.environ',{'THOTH_DB':str(path)}),patch('getpass.getpass',side_effect=[NEW,NEW]),patch('builtins.print'):
            main()
        self.assertEqual(path.read_bytes(),b'trading-state-sentinel')
        self.assertEqual(self.login(PASSWORD).status_code,401)
        self.assertEqual(self.login(NEW).status_code,200)
        self.assertFalse(self.store.read()['totp_secret'])
        self.assertFalse(self.store.read()['recovery'])

    def test_concurrent_revocation_cannot_issue_session_at_new_revision(self):
        original=auth.signed_in
        def revoked_before_cookie(local,revision):
            with self.store.edit() as (state,db):state['revision']+=1
            return original(local,revision)
        with patch.object(auth,'signed_in',side_effect=revoked_before_cookie):
            self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.client.get('/api/security/status').status_code,401)
