"""Synthetic phone notification UI check. No real push subscription or trading requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright,expect

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='thoth-phone-ui-') as directory:
    env={k:v for k,v in os.environ.items() if not k.startswith('THOTH_')};env['THOTH_DB']=directory+'/live.sqlite'
    with open(directory+'/server.log','w') as log:
        server=subprocess.Popen([sys.executable,'-m','uvicorn','thoth.app:app','--host','127.0.0.1','--port','8098'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            base='http://127.0.0.1:8098'
            for _ in range(100):
                try:urllib.request.urlopen(base+'/healthz');break
                except Exception:time.sleep(.1)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                for width in (1440,390):
                    page=browser.new_page(viewport={'width':width,'height':850});errors=[];calls=[];devices=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.add_init_script('''
                        window.fixturePermissionCalls=0;window.fixtureSubscription=null;window.fixtureClipboard='';
                        Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.fixtureClipboard=text}}});
                        window.PushManager=function(){};
                        window.Notification={permission:'default',requestPermission:async()=>{window.fixturePermissionCalls++;Notification.permission='granted';return 'granted'}};
                        const publicKey=new Uint8Array([4,...Array(64).fill(1)]);
                        const reg={pushManager:{getSubscription:async()=>window.fixtureSubscription,subscribe:async options=>{
                          const value={endpoint:'https://fcm.googleapis.com/fcm/send/fixture',keys:{auth:'YWFhYWFhYWFhYWFhYWFhYQ',p256dh:'fixture'}};
                          window.fixtureSubscription={endpoint:value.endpoint,options:{applicationServerKey:publicKey.buffer},toJSON:()=>value,unsubscribe:async()=>{window.fixtureSubscription=null;return true}};
                          return window.fixtureSubscription;
                        }}};
                        Object.defineProperty(navigator,'serviceWorker',{value:{register:async()=>reg,ready:Promise.resolve(reg)}});
                    ''')
                    import base64,hashlib
                    key=base64.urlsafe_b64encode(bytes([4]+[1]*64)).decode().rstrip('=')
                    identity=hashlib.sha256(b'https://fcm.googleapis.com/fcm/send/fixture').hexdigest()
                    def api(route):
                        request=route.request;calls.append((request.method,request.url))
                        if request.method=='GET':result=dict(public_key=key,devices=devices,recent_pauses=[dict(time=1793088000,body='Account monitoring unavailable · KeyError · testnet.py:800 monitor')])
                        elif request.url.endswith('/enable'):devices.append(dict(id=identity,status='registered'));result=dict(id=identity)
                        elif request.url.endswith('/disable'):devices.clear();result=dict(disabled=True)
                        elif request.url.endswith('/test'):result=dict(queued=True)
                        else:raise AssertionError(request.url)
                        route.fulfill(json=result)
                    page.route('**/api/phone-alerts**',api)
                    page.route('**/phone-fixture',lambda route:route.fulfill(content_type='text/html',body='<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/desk.css"><div id="page-security"></div><script src="/phone-alerts.js"></script>'))
                    page.goto(base+'/phone-fixture')
                    expect(page.locator('#phoneAlertStatus')).to_contain_text('Not enabled')
                    assert page.evaluate('fixturePermissionCalls')==0
                    page.click('#enablePhoneAlerts');expect(page.locator('#phoneAlertStatus')).to_contain_text('Enabled on this device')
                    assert page.evaluate('fixturePermissionCalls')==1
                    page.click('#testPhoneAlerts');expect(page.locator('#phoneAlertStatus')).to_contain_text('Test queued')
                    page.click('summary');expect(page.locator('#recentEntryPauses')).to_contain_text('Account monitoring unavailable')
                    expect(page.locator('#recentEntryPauses')).to_contain_text('EDT')
                    page.click('#copyPauseReport');expect(page.locator('#phoneAlertStatus')).to_contain_text('Pause report copied')
                    assert 'Account monitoring unavailable' in page.evaluate('fixtureClipboard')
                    assert 'EDT' in page.evaluate('fixtureClipboard')
                    page.click('#disablePhoneAlerts');expect(page.locator('#phoneAlertStatus')).to_contain_text('Not enabled')
                    assert not errors,errors
                    assert all('/api/phone-alerts' in url for _,url in calls)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                    page.close()
                browser.close()
            print('PASS: desktop/mobile opt-in permission, enable/test/disable, saved pause reason and New York time, no trading calls or page errors')
        finally:server.terminate();server.wait(timeout=15)
