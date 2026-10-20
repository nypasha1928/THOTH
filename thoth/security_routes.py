"""Dashboard-only security endpoints. Never import a trading supervisor here."""
import hashlib
import json
import secrets
from urllib.parse import urlsplit
from webauthn import (generate_registration_options,verify_registration_response,
    generate_authentication_options,verify_authentication_response,options_to_json)
from webauthn.helpers import bytes_to_base64url,base64url_to_bytes
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,ResidentKeyRequirement,
    UserVerificationRequirement,PublicKeyCredentialDescriptor)
from .security import password_hash,new_codes,code_hash


def session_owner(request):
    return hashlib.sha256(request.cookies.get('thoth_session','').encode()).hexdigest()


def perform(store,request,data,bootstrap):
    """Return (response data, rotate cookie, authenticated revision). Caller enforces session and rate limits."""
    action=request.url.path.removeprefix('/api/security/')
    if action=='status':return store.status(),False,None
    if action=='login-options':
        state=store.read()
        return dict(totp_enabled=bool(state['totp_secret']),passkey_available=bool(state['passkeys'])),False,None
    origin=str(request.base_url).rstrip('/')
    rp_id=urlsplit(origin).hostname
    with store.edit() as (state,db):
        if action=='recover':
            digest=code_hash(data.get('recovery_code',''))
            saved=next((code for code in state['recovery'] if secrets.compare_digest(code,digest)),None)
            if not saved:raise ValueError('Recovery failed; use an unused recovery code')
            state.update(password=password_hash(data.get('new_password')),totp_secret=None,totp_last_step=-1,passkeys=[],revision=state['revision']+1)
            codes=new_codes(state);db.execute('DELETE FROM challenges')
            return dict(status='recovered',recovery_codes=codes,detail='Password reset. Authenticator and passkeys removed; enroll them again.'),True,state['revision']
        if action=='passkey/login/options':
            if not state['passkeys']:raise ValueError('No passkey registered')
            options=generate_authentication_options(rp_id=rp_id,user_verification=UserVerificationRequirement.REQUIRED)
            identifier=store.challenge(db,'passkey_login',dict(challenge=options.challenge.hex(),revision=state['revision'],origin=origin))
            return dict(challenge_id=identifier,options=json.loads(options_to_json(options))),False,None
        if action=='passkey/login/verify':
            pending=store.consume(db,data.get('challenge_id',''),'passkey_login')
            if pending['revision']!=state['revision'] or pending['origin']!=origin:raise ValueError('Request expired; start again')
            credential=data.get('credential',{})
            key=next((key for key in state['passkeys'] if key['id']==credential.get('id')),None)
            if not key:raise ValueError('Passkey sign-in failed')
            verified=verify_authentication_response(credential=credential,expected_challenge=bytes.fromhex(pending['challenge']),expected_rp_id=rp_id,expected_origin=origin,credential_public_key=bytes.fromhex(key['public_key']),credential_current_sign_count=key['sign_count'],require_user_verification=True)
            handle=credential.get('response',{}).get('userHandle')
            if handle and base64url_to_bytes(handle)!=bytes.fromhex(state['user_id']):raise ValueError('Passkey user mismatch')
            key['sign_count']=verified.new_sign_count
            return dict(status='signed_in'),True,state['revision']
        # Every security mutation requires the current password and, if enabled,
        # a fresh authenticator code or unused recovery code.
        if action not in ('totp/confirm','passkey/register/verify'):
            store.authenticate(state,data.get('password'),data.get('code',''),bootstrap)
            store.initialize(state,bootstrap)
        if action=='password':
            state.update(password=password_hash(data.get('new_password')),revision=state['revision']+1)
            db.execute('DELETE FROM challenges')
            return dict(status='password_changed'),True,state['revision']
        if action=='recovery-codes':
            codes=new_codes(state);state['revision']+=1
            db.execute('DELETE FROM challenges')
            return dict(recovery_codes=codes),True,state['revision']
        if action=='totp/setup':
            if not state['recovery']:raise ValueError('Save recovery codes before enabling an authenticator')
            if state['totp_secret']:raise ValueError('Authenticator already enabled')
            import base64
            secret=base64.b32encode(secrets.token_bytes(20)).decode()
            encrypted=store.cipher().encrypt(secret.encode()).decode()
            pending=dict(secret=encrypted,owner=session_owner(request),revision=state['revision'])
            identifier=store.challenge(db,'totp_setup',pending,600)
            return dict(challenge_id=identifier,secret=secret,qr=store.qr(secret,rp_id)),False,None
        if action=='totp/confirm':
            pending=store.consume(db,data.get('challenge_id',''),'totp_setup')
            if pending['owner']!=session_owner(request) or pending['revision']!=state['revision'] or state['totp_secret']:raise ValueError('Request expired; start again')
            temporary=dict(totp_secret=pending['secret'],totp_last_step=-1,recovery=[])
            store.second_factor(temporary,data.get('code',''))
            state.update(totp_secret=temporary['totp_secret'],totp_last_step=temporary['totp_last_step'],revision=state['revision']+1)
            db.execute('DELETE FROM challenges')
            return dict(status='authenticator_enabled'),True,state['revision']
        if action=='totp/disable':
            state.update(totp_secret=None,totp_last_step=-1,revision=state['revision']+1)
            db.execute('DELETE FROM challenges')
            return dict(status='authenticator_disabled'),True,state['revision']
        if action=='passkey/register/options':
            if not state['recovery']:raise ValueError('Save recovery codes before adding a passkey')
            if len(state['passkeys'])>=10:raise ValueError('Maximum ten passkeys; remove an old passkey first')
            options=generate_registration_options(rp_id=rp_id,rp_name='THOTH Trading',user_name='THOTH owner',user_id=bytes.fromhex(state['user_id']),authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,user_verification=UserVerificationRequirement.REQUIRED),exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p['id'])) for p in state['passkeys']])
            pending=dict(challenge=options.challenge.hex(),owner=session_owner(request),revision=state['revision'],origin=origin,name=str(data.get('name','My device'))[:60])
            identifier=store.challenge(db,'passkey_registration',pending)
            return dict(challenge_id=identifier,options=json.loads(options_to_json(options))),False,None
        if action=='passkey/register/verify':
            pending=store.consume(db,data.get('challenge_id',''),'passkey_registration')
            if pending['owner']!=session_owner(request) or pending['revision']!=state['revision'] or pending['origin']!=origin:raise ValueError('Request expired; start again')
            verified=verify_registration_response(credential=data.get('credential',{}),expected_challenge=bytes.fromhex(pending['challenge']),expected_rp_id=rp_id,expected_origin=origin,require_user_verification=True)
            key=dict(id=bytes_to_base64url(verified.credential_id),public_key=verified.credential_public_key.hex(),sign_count=verified.sign_count,name=pending['name'],created=store.clock())
            if any(p['id']==key['id'] for p in state['passkeys']):raise ValueError('Passkey already registered')
            state['passkeys'].append(key);state['revision']+=1
            db.execute('DELETE FROM challenges')
            return dict(status='passkey_registered'),True,state['revision']
        if action=='passkey/remove':
            state['passkeys']=[p for p in state['passkeys'] if p['id']!=data.get('id')]
            state['revision']+=1;db.execute('DELETE FROM challenges')
            return dict(status='passkey_removed'),True,state['revision']
        raise ValueError('Unknown security operation')
