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
LOGIN = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>THOTH · Sign in</title><style>body{background:#101918;color:#ecf1e9;font:16px system-ui;margin:12vh auto;max-width:360px;padding:24px}input,button{box-sizing:border-box;width:100%;padding:14px;border-radius:8px;margin:12px 0}button{background:#b9ee79;border:0}p{color:#a8b5aa}</style><h1>THOTH.</h1><p>Sign in to your trading desk.</p><form id="login"><label>Password<input id="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form><p id="error" role="status"></p><script src="/login.js" defer></script></html>'''


def token():
    body=f'{int(time.time())+28800}.{secrets.token_hex(16)}'
    return body+'.'+hmac.new(KEY,body.encode(),hashlib.sha256).hexdigest()


def authorized(request):
    if not PASSWORD:return True
    raw=request.cookies.get('thoth_session','')
    try:
        body,signature=raw.rsplit('.',1)
        expiry=int(body.split('.')[0])
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
    if path=='/api/login' and request.method=='POST':
        host=request.client.host if request.client else 'unknown'
        with _lock:
            attempts=_attempts[host]
            while attempts and attempts[0]<time.time()-900:attempts.popleft()
            if len(attempts)>=5:return JSONResponse({'detail':'Try later'},status_code=429)
            attempts.append(time.time())
        try:
            data=await request.json()
            supplied=data.get('password','')
            if not isinstance(supplied,str):raise ValueError()
        except (ValueError,AttributeError):return JSONResponse({'detail':'Invalid login'},status_code=400)
        if not PASSWORD or not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(PASSWORD.encode()).digest()):
            return JSONResponse({'detail':'Invalid login'},status_code=401)
        response=JSONResponse({'status':'signed_in'})
        response.set_cookie('thoth_session',token(),max_age=28800,httponly=True,
                            secure=not local,samesite='strict',path='/')
        return response
    if path in ('/healthz','/login.js','/icon.svg','/icon-192.png','/icon-512.png','/manifest.webmanifest') and request.method=='GET':return None
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
