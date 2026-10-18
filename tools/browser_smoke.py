"""Optional Chromium smoke check with synthetic account/market fixtures only.
Run: python tools/browser_smoke.py (requires Playwright and /usr/bin/chromium).
No credentials or exchange execution are used. Writes labelled fixture previews.
"""
import os,subprocess,tempfile,time,json,math,urllib.request,copy
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='thoth-ui-') as d:
 env={k:v for k,v in os.environ.items() if not k.startswith('THOTH_')};env['THOTH_DB']=d+'/paper.sqlite'
 log=open(d+'/server.log','w')
 server=subprocess.Popen(['python','-m','uvicorn','thoth.app:app','--host','127.0.0.1','--port','8097'],cwd=root,env=env,stdout=log,stderr=log)
 try:
  base='http://127.0.0.1:8097'
  for _ in range(100):
   try:
    urllib.request.urlopen(base+'/healthz');break
   except Exception:time.sleep(.1)
  def get(path):return json.load(urllib.request.urlopen(base+'/api/'+path))
  payloads={path:get(path) for path in ['state','testnet','live','watchlist','runner','readiness']}
  payloads['live']['enabled_on_server']=True
  payloads['live']['unverified_live_allowed']=True
  rows=[];bars=[];sma=[]
  for i in range(600):
   stamp=1792300000+i*60;value=2400+math.sin(i/9)*8+i*.02
   rows.append(dict(time=stamp,open=value-1,close=value+1,high=value+3,low=value-3))
   levels={f'{f}_{s}_{k}':dict(value=2400+(-10 if s=='support' else 10)+(3 if k=='minor' else 0),time=stamp) for f in ('long','short') for s in ('support','resistance') for k in ('major','minor')}
   bars.append(dict(time=stamp,sma=value-4,signal='Long' if i%23==0 else 'Short' if i%29==0 else None,levels=levels));sma.append(dict(time=stamp,value=value-4))
  chart=dict(source='SIMULATED BROWSER FIXTURE · not exchange results',candles=rows,analysis=bars,sma=sma)
  writes=[];activation=[];errors=[]
  def route(req):
   url=req.request.url.split('/api/')[1];path=url.split('?')[0];method=req.request.method
   if path.startswith('chart/'):result=chart
   elif path.startswith('connection'):result=dict(verified=False,configured=False,reason='Browser fixture: no credentials',balance=None,balance_reason='Not connected')
   elif path in payloads:result=payloads[path]
   elif path.endswith('/settings') and method=='PUT':
    target=path.split('/')[0];cfg=req.request.post_data_json;payloads[target]['config']=cfg;writes.append((target,cfg));result=payloads[target]
   elif path.endswith('/activate'):
    activation.append(path);req.fulfill(status=400,json={'detail':'Fixture refuses trading'});return
   else:raise AssertionError((method,path))
   req.fulfill(json=copy.deepcopy(result))
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
   page=browser.new_page(viewport={'width':1440,'height':1080})
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.route('**/api/**',route);page.goto(base);expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   page.select_option('#viewMode','live');page.select_option('#symbol','ETHUSDT')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   expect(page.locator('#chartScope')).to_contain_text('Included in saved trading list')
   page.click('#onlyChart');expect(page.locator('#selectedSymbols button')).to_have_count(1)
   expect(page.locator('#selectedSymbols')).to_contain_text('ETHUSDT')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   # Draft edits cannot trigger activation; no order route is reached.
   page.click('#start');expect(page.locator('#symbolSaveMessage')).to_contain_text('Save your strategy')
   assert not activation
   page.click('#saveSymbols');expect(page.locator('#savedSymbols')).not_to_contain_text('SOLUSDT')
   assert writes[-1][1]['symbols']==['ETHUSDT']
   page.select_option('#symbol','SOLUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   assert writes[-1][1]['symbols']==['ETHUSDT']
   page.fill('#search','BTC');page.click('#selectVisible');expect(page.locator('#selectedSymbols button')).to_have_count(2)
   page.check('#selectedOnly');expect(page.locator('#coins input')).to_have_count(1)
   page.fill('#search','NO_SUCH_SYMBOL');expect(page.locator('#coins')).to_contain_text('No matching symbols')
   page.click('#clearSymbols');expect(page.locator('#selectedSymbols button')).to_have_count(0)
   page.click('#onlyChart');page.fill('#search','');page.uncheck('#selectedOnly');page.click('#saveSymbols')
   expect(page.locator('#savedSymbols')).to_contain_text('Saved trading symbols (1): SOLUSDT')
   page.click('#older');expect(page.locator('#latest')).to_be_enabled();page.click('#latest');expect(page.locator('#latest')).to_be_disabled()
   page.locator('#chart').click(position={'x':100,'y':160});expect(page.locator('#candleInfo')).to_contain_text(' O ')
   assert page.locator('#chart path').count()>30
   page.uncheck('#showLevels');page.uncheck('#showSma');page.uncheck('#showSignals');page.check('#fitLevels')
   page.check('#showLevels');page.check('#showSma');page.check('#showSignals')
   page.locator('#zoom').fill('35');page.locator('#zoom').dispatch_event('input')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   page.screenshot(path=str(root/'docs/desktop-watchlist-fixture.png'),full_page=True)
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('SOLUSDT');expect(page.locator('#testnetDialog')).to_be_visible();page.keyboard.press('Escape');expect(page.locator('#testnetDialog')).not_to_be_visible()
   # Poll update locks every mutation without discarding the form draft.
   payloads['live']['entries']=True
   expect(page.locator('#clearSymbols')).to_be_disabled(timeout=10000)
   expect(page.locator('#coins input').first).to_be_disabled()
   payloads['live']['entries']=False
   expect(page.locator('#clearSymbols')).to_be_enabled(timeout=10000)
   page.set_viewport_size({'width':390,'height':844});page.reload()
   page.select_option('#viewMode','live');expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   page.select_option('#symbol','ETHUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   page.click('#onlyChart');page.click('#saveSymbols');expect(page.locator('#savedSymbols')).to_contain_text('ETHUSDT')
   page.locator('#chart').click(position={'x':130,'y':120});expect(page.locator('#candleInfo')).to_contain_text(' O ')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   page.screenshot(path=str(root/'docs/mobile-watchlist-fixture.png'),full_page=True)
   page.select_option('#viewMode','testnet');expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
   page.select_option('#viewMode','paper');expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
   assert not errors,errors
   assert not activation,activation
   browser.close()
  print('PASS: desktop/mobile watchlist, separate saved/draft/chart symbols, saves, active-entry lock, chart controls, mode separation, no JS errors/overflow, no activation requests')
 finally:
  server.terminate();server.wait(timeout=15);log.close()
