"""Optional password sessions for the mobile dashboard.

Local development may omit a password. A public host requires one and HTTPS.
Credentials are read from runtime variables, never persisted in the repository.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from .security import SecurityStore,password_matches
from .security_routes import perform

from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .credentials import runtime_secret

PASSWORD = runtime_secret('THOTH_PASSWORD')
PUBLIC_HOST = os.environ.get('THOTH_PUBLIC_HOST', '')
if PUBLIC_HOST and (not PASSWORD or len(PASSWORD) < 16):
    raise RuntimeError('Public hosting requires THOTH_PASSWORD of at least 16 characters')
if any(c in PUBLIC_HOST for c in '/:@ '):
    raise RuntimeError('THOTH_PUBLIC_HOST must be a hostname only')
KEY = hmac.new(PASSWORD.encode(), b'THOTH-session-v2'+secrets.token_bytes(32), hashlib.sha256).digest()
_revoked = {}
_attempts = defaultdict(deque)
_lock = threading.Lock()
_store = None


def security_store():
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                directory=Path(os.environ.get('THOTH_DB',str(Path(__file__).resolve().parent.parent/'data'/'live.sqlite'))).parent
                _store=SecurityStore(directory)
    return _store


def limited(host):
    with _lock:
        attempts=_attempts[host]
        while attempts and attempts[0]<time.time()-900:attempts.popleft()
        if len(attempts)>=5:return True
        attempts.append(time.time())
    return False


def signed_in(local,revision):
    response=JSONResponse({'status':'signed_in'})
    response.set_cookie('thoth_session',token(revision),max_age=28800,httponly=True,
                        secure=not local,samesite='strict',path='/')
    return response

LOGIN = (Path(__file__).resolve().parent.parent/'web'/'login.html').read_text()


def token(revision=None):
    if revision is None:revision=security_store().read()['revision']
    body=f"{int(time.time())+28800}.{secrets.token_hex(16)}.{revision}"
    return body+'.'+hmac.new(KEY,body.encode(),hashlib.sha256).hexdigest()


def authorized(request):
    if not PASSWORD:return True
    raw=request.cookies.get('thoth_session','')
    try:
        body,signature=raw.rsplit('.',1)
        expiry_text,nonce,revision=body.split('.')
        expiry=int(expiry_text)
        if int(revision)!=security_store().read()['revision']:return False
        if hashlib.sha256(raw.encode()).hexdigest() in _revoked:return False
        return time.time()<expiry<=time.time()+28801 and hmac.compare_digest(
            signature,hmac.new(KEY,body.encode(),hashlib.sha256).hexdigest())
    except (ValueError,TypeError):return False


async def guard(request):
    local=request.url.hostname in ('localhost','127.0.0.1','::1','testserver')
    if not local and request.url.scheme!='https':
        return JSONResponse({'detail':'HTTPS required'},status_code=400)
    path=request.url.path
    if path=='/login':return HTMLResponse(LOGIN) if PASSWORD else RedirectResponse('/')
    public_security=path in ('/api/security/login-options','/api/security/recover','/api/security/passkey/login/options','/api/security/passkey/login/verify')
    if path=='/api/login' and request.method=='POST':
        host=request.client.host if request.client else 'unknown'
        bucket=host+':login'
        if limited(bucket):return JSONResponse({'detail':'Too many attempts. Try again in 15 minutes.'},status_code=429)
        try:
            raw=await request.body()
            if len(raw)>65536:return JSONResponse({'detail':'Request too large'},status_code=413)
            import json
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('Invalid login')
            supplied=data.get('password','')
            if not isinstance(supplied,str):raise ValueError('Invalid login')
            if not PASSWORD:raise ValueError('Invalid login')
            with security_store().edit() as (state,db):
                security_store().authenticate(state,supplied,data.get('code',''),PASSWORD)
                verified_revision=state['revision']
        except (ValueError,AttributeError):return JSONResponse({'detail':'Invalid password, authenticator code, or recovery code. Authenticator codes can be used only once.'},status_code=401)
        with _lock:_attempts.pop(bucket,None)
        return signed_in(local,verified_revision)
    if path.startswith('/api/security/'):
        if not PASSWORD:return JSONResponse({'detail':'Configure a dashboard password first'},status_code=403)
        if not public_security and not authorized(request):return JSONResponse({'detail':'Login required'},status_code=401)
        if request.method not in ('GET','POST'):return JSONResponse({'detail':'Method not allowed'},status_code=405)
        action=path.removeprefix('/api/security/')
        if request.method=='GET' and action not in ('status','login-options'):return JSONResponse({'detail':'Method not allowed'},status_code=405)
        host=request.client.host if request.client else 'unknown'
        bucket=host+':'+path
        if request.method=='POST' and limited(bucket):return JSONResponse({'detail':'Too many attempts. Try again in 15 minutes.'},status_code=429)
        try:
            raw=await request.body() if request.method=='POST' else b'{}'
            if len(raw)>65536:return JSONResponse({'detail':'Request too large'},status_code=413)
            import json
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('Invalid request')
            result,rotate,verified_revision=perform(security_store(),request,data,PASSWORD)
        except Exception as exc:
            message=str(exc) if type(exc) is ValueError else 'Security verification failed; start again and verify your credentials.'
            return JSONResponse({'detail':message},status_code=400)
        if request.method=='POST' and action not in ('passkey/login/options',):
            with _lock:_attempts.pop(bucket,None)
        response=signed_in(local,verified_revision) if rotate else JSONResponse(result)
        if rotate:
            import json
            response.body=json.dumps(result).encode()
            response.headers['content-length']=str(len(response.body))
        return response
    if path in ('/healthz','/login.js','/passkeys.js','/icon.svg','/icon-192.png','/icon-512.png','/manifest.webmanifest') and request.method=='GET':return None
    if not authorized(request):
        return JSONResponse({'detail':'Login required'},status_code=401) if path.startswith('/api/') else RedirectResponse('/login')
    if path=='/api/logout' and request.method=='POST':
        raw=request.cookies.get('thoth_session','')
        with _lock:
            for identifier,expiry in list(_revoked.items()):
                if expiry<time.time():del _revoked[identifier]
            if raw:
                try:_revoked[hashlib.sha256(raw.encode()).hexdigest()]=int(raw.split('.')[0])
                except ValueError:pass
        response=JSONResponse({'status':'signed_out'})
        response.delete_cookie('thoth_session')
        return response
    return None
