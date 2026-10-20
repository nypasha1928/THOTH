"""Real local auth/virtual WebAuthn browser checks; no exchange credentials/orders.
Requires Playwright and /usr/bin/chromium. All state is temporary. Never prints
passwords, authenticator setup secrets, recovery codes, or credential material.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright,expect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thoth.security import totp

root=Path(__file__).resolve().parents[1]
PASSWORD='synthetic-security-password-only'
NEW='synthetic-changed-password-only'
RESET='synthetic-recovered-password-only'
with tempfile.TemporaryDirectory(prefix='thoth-security-browser-') as directory:
    env={k:v for k,v in os.environ.items() if not k.startswith('THOTH_')}
    env.update(THOTH_DB=directory+'/live.sqlite',THOTH_PASSWORD=PASSWORD,THOTH_ENABLE_LIVE='0')
    with open(directory+'/server.log','w') as log:
        server=subprocess.Popen([sys.executable,'-m','uvicorn','thoth.app:app','--host','127.0.0.1','--port','8098'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            base='http://localhost:8098'
            for _ in range(100):
                try:urllib.request.urlopen(base+'/healthz');break
                except Exception:time.sleep(.1)
            else:raise RuntimeError('Security fixture server did not become healthy')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
                context=browser.new_context(viewport=dict(width=412,height=915),is_mobile=True,has_touch=True)
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                def routes(route):
                    if '/api/chart/' in route.request.url or '/api/quote/' in route.request.url:
                        route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'Synthetic security check: market data disabled'}))
                    else:route.continue_()
                page.route('**/api/**',routes)
                page.goto(base+'/login');page.fill('#password',PASSWORD);page.click('#login button')
                expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
                page.click('[data-page-link=settings]');expect(page.locator('#securityStatus')).to_contain_text('Authenticator: off')
                old_cookie=next(c for c in context.cookies() if c['name']=='thoth_session')
                page.fill('#securityPassword',PASSWORD);page.fill('#newSecurityPassword',NEW);page.fill('#confirmSecurityPassword',NEW);page.click('#changePassword button')
                expect(page.locator('#securityMessage')).to_contain_text('Password changed')
                other=browser.new_context();other.add_cookies([old_cookie])
                assert other.request.get(base+'/api/security/status').status==401
                other.close()
                page.fill('#securityPassword',NEW);page.click('#generateRecovery')
                expect(page.locator('#securityRecovery')).to_be_visible()
                codes=page.locator('#securityRecoveryCodes').inner_text().splitlines();assert len(codes)==10
                page.click('#hideSecurityRecovery')
                cdp=context.new_cdp_session(page);cdp.send('WebAuthn.enable')
                cdp.send('WebAuthn.addVirtualAuthenticator',{'options':dict(protocol='ctap2',transport='internal',hasResidentKey=True,hasUserVerification=True,isUserVerified=True,automaticPresenceSimulation=True)})
                page.fill('#securityPassword',NEW);page.fill('#passkeyName','Synthetic Android device');page.click('#registerPasskey')
                expect(page.locator('#securityMessage')).to_contain_text('Passkey registered')
                expect(page.locator('#registeredPasskeys')).to_contain_text('Synthetic Android device')
                page.click('#logout');expect(page.locator('#password')).to_be_visible()
                expect(page.locator('#passkeyLogin')).to_be_visible();page.click('#passkeyLogin')
                expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
                page.click('[data-page-link=settings]');page.fill('#securityPassword',NEW);page.click('#setupTotp')
                expect(page.locator('#totpSetup')).to_be_visible()
                secret=page.locator('#totpSecret').input_value()
                page.fill('#totpConfirmCode',totp(secret,int(time.time()//30)));page.click('#confirmTotp')
                expect(page.locator('#securityMessage')).to_contain_text('Google Authenticator enabled')
                expect(page.locator('#totpSetup')).not_to_be_visible()
                assert page.locator('#totpSecret').input_value()==''
                assert not page.evaluate('document.documentElement.scrollWidth>window.innerWidth')
                page.click('#logout');expect(page.locator('#otpLabel')).to_be_visible()
                # User-verified passkey sign-in works when password sign-in requires TOTP.
                page.click('#passkeyLogin');expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
                page.click('#logout');page.locator('summary').click()
                page.fill('#recoveryCode',codes[0]);page.fill('#recoveryPassword',RESET);page.fill('#recoveryConfirm',RESET);page.click('#recover button')
                expect(page.locator('#recoveryResult')).to_be_visible()
                assert len(page.locator('#newCodes').inner_text().splitlines())==10
                # Recovery removed the old authenticator/passkey and replaced all codes.
                page.goto(base+'/login');expect(page.locator('#passkeyLogin')).not_to_be_visible()
                expect(page.locator('#otpLabel')).not_to_be_visible()
                page.fill('#password',RESET);page.click('#login button')
                expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
                assert not errors,errors
                with __import__('sqlite3').connect(directory+'/live.sqlite') as db:
                    state=json.loads(db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()[0])
                assert not state['management'] and not state['entries'] and not state['owned']
                browser.close()
            print('PASS: mobile password change, old-session revocation, recovery codes, real virtual-authenticator passkey registration/login, Google Authenticator enrollment, passkey login with TOTP enabled, full password recovery, no trading activation and no browser errors')
        finally:
            server.terminate();server.wait(timeout=15)
