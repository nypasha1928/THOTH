"""Verify HTTPS, login isolation and safe account status on a deployed THOTH server.

Password is read from a private local file, never supplied as a command-line
value. No exchange orders, account verification or trading controls are invoked.
"""
import argparse
import http.cookiejar
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPCookieProcessor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thoth.credentials import runtime_secret


def check(base,password):
    parts=urlsplit(base)
    local=parts.hostname in ('127.0.0.1','localhost','::1')
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Provide only the server origin, without credentials, path or query')
    if parts.scheme!='https' and not (local and parts.scheme=='http'):raise ValueError('Remote checks require HTTPS')
    base=base.rstrip('/')
    cookies=http.cookiejar.CookieJar();opener=build_opener(HTTPCookieProcessor(cookies))
    def request(path,body=None):
        req=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {})
        with opener.open(req,timeout=15) as response:return json.load(response)
    if request('/healthz').get('status')!='ok':raise ValueError('Server health check failed')
    try:
        request('/api/live')
        raise ValueError('Account state is accessible without login')
    except HTTPError as error:
        if error.code!=401:raise ValueError('Unexpected unauthenticated response') from None
    request('/api/login',{'password':password})
    live=request('/api/live');readiness=request('/api/readiness')
    result=dict(https=parts.scheme=='https',unauthenticated_state_blocked=True,login_verified=True,
                live_entries=live['entries'],
                reference_verified=readiness['live_reference']['verified'])
    request('/api/logout',{})
    try:request('/api/live');raise ValueError('Logout did not remove authenticated access')
    except HTTPError as error:
        if error.code!=401:raise ValueError('Unexpected logout response') from None
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('origin');parser.add_argument('--password-file',type=Path,required=True);args=parser.parse_args()
    os.environ['THOTH_CHECK_PASSWORD_FILE']=str(args.password_file)
    try:result=check(args.origin,runtime_secret('THOTH_CHECK_PASSWORD'))
    except Exception:raise SystemExit('Server check failed; inspect HTTPS, private credential permissions, authentication and deployment status. No trading actions were requested.') from None
    print(json.dumps(result,indent=2))
