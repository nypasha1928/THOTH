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
   levels={f'{f}_{s}_{k}':dict(value=2400+(-10 if s=='support' else 10)+(3 if k=='minor' else 0),pivot_time=1792300000+max(0,(i//100)*100-20)*60) for f in ('long','short') for s in ('support','resistance') for k in ('major','minor')}
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
   # Chart-only search and coin-name navigation must never modify trading settings.
   saved_before=copy.deepcopy(writes[-1][1]['symbols']);write_count=len(writes)
   page.click('#chartSearch');page.fill('#chartSearchInput','ETHUSDT')
   page.locator('#chartSearchResults button').filter(has_text='ETHUSDT').click()
   expect(page.locator('#symbol')).to_have_value('ETHUSDT')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   page.get_by_role('button',name='View SOLUSDT chart',exact=True).click()
   expect(page.locator('#symbol')).to_have_value('SOLUSDT')
   assert len(writes)==write_count and writes[-1][1]['symbols']==saved_before
   # Actual horizontal drag in both directions, including pointer capture across redraws.
   box=page.locator('#chart').bounding_box();sx=box['x']+box['width']*.45;sy=box['y']+box['height']*.5
   page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx+90,sy,steps=6);page.mouse.up()
   offset=page.evaluate('() => chartOffset');assert offset>0,offset
   page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx-50,sy,steps=6);page.mouse.up()
   assert page.evaluate('() => chartOffset')<offset
   page.click('#latest');expect(page.locator('#latest')).to_be_disabled()
   rays=page.locator('[data-sr-key]');expect(rays).to_have_count(8)
   major=page.locator('[data-sr-key="long_resistance_major"]')
   expect(major).to_have_attribute('stroke','#b70909');expect(major).to_have_attribute('stroke-width','4')
   assert ' V' not in major.get_attribute('d')
   expect(page.locator('[data-sr-key="short_support_major"]')).to_have_attribute('stroke-dasharray','12 10')
   page.click('#appearanceToggle');expect(page.locator('#chartAppearance')).to_be_visible()
   page.get_by_label('Bullish candle',exact=True).fill('#12abcd')
   expect(page.locator('[data-candle="bull"]').first).to_have_attribute('fill','#12abcd')
   page.get_by_label('Bullish signal',exact=True).fill('#aabbcc')
   expect(page.locator('[data-signal="Long"]').first).to_have_attribute('fill','#aabbcc')
   page.get_by_label('Long-term major Support',exact=True).fill('#33bb77')
   expect(page.locator('[data-sr-key="long_support_major"]')).to_have_attribute('stroke','#33bb77')
   page.get_by_label('Long-term major Show',exact=True).uncheck();expect(rays).to_have_count(6)
   page.get_by_label('Long-term major Show',exact=True).check()
   page.click('#maximizeChart');expect(page.locator('#maximizeChart')).to_have_attribute('aria-expanded','true')
   assert page.locator('#chartCard').bounding_box()['y']==0
   page.screenshot(path=str(root/'docs/desktop-interactive-chart-fixture.png'))
   page.click('#maximizeChart');expect(page.locator('#maximizeChart')).to_have_attribute('aria-expanded','false')
   page.click('#closeAppearance')
   page.screenshot(path=str(root/'docs/desktop-watchlist-fixture.png'),full_page=True)
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('SOLUSDT');expect(page.locator('#testnetDialog')).to_be_visible();page.keyboard.press('Escape');expect(page.locator('#testnetDialog')).not_to_be_visible()
   # Poll update locks every mutation without discarding the form draft.
   payloads['live']['entries']=True
   expect(page.locator('#clearSymbols')).to_be_disabled(timeout=10000)
   expect(page.locator('#coins input').first).to_be_disabled()
   payloads['live']['entries']=False
   expect(page.locator('#clearSymbols')).to_be_enabled(timeout=10000)
   page.set_viewport_size({'width':390,'height':844});page.reload()
   assert page.evaluate('() => chartStyle.bull')=='#12abcd'
   page.click('#appearanceToggle');page.click('#resetAppearance')
   assert page.evaluate('() => chartStyle.bull')=='#26a69a'
   page.click('#closeAppearance')
   page.select_option('#viewMode','live');expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   page.select_option('#symbol','ETHUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   page.click('#onlyChart');page.click('#saveSymbols');expect(page.locator('#savedSymbols')).to_contain_text('ETHUSDT')
   page.locator('#chart').click(position={'x':130,'y':120});expect(page.locator('#candleInfo')).to_contain_text(' O ')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   page.click('#chartSearch');page.fill('#chartSearchInput','ETHUSDT');page.keyboard.press('Enter')
   expect(page.locator('#chartSearchDialog')).not_to_be_visible()
   page.click('#maximizeChart');expect(page.locator('#maximizeChart')).to_have_attribute('aria-expanded','true')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   assert page.locator('#chartCard').bounding_box()['y']==0
   page.screenshot(path=str(root/'docs/mobile-interactive-chart-fixture.png'))
   page.click('#maximizeChart')
   page.screenshot(path=str(root/'docs/mobile-watchlist-fixture.png'),full_page=True)
   page.select_option('#viewMode','testnet');expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
   page.select_option('#viewMode','paper');expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
   page.evaluate("() => localStorage.setItem('thoth.chart.appearance.v1',JSON.stringify({bull:'bad',signalSize:999,long_major:{width:0,style:'unsafe',support:'bad'}}))")
   page.reload();expect(page.locator('#savedSymbols')).to_contain_text('BTCUSDT')
   assert page.evaluate('() => chartStyle.bull')=='#26a69a'
   assert page.evaluate('() => chartStyle.long_major.width')==4
   assert not errors,errors
   assert not activation,activation
   browser.close()
  print('PASS: desktop/mobile watchlist, separate saved/draft/chart symbols, saves, active-entry lock, dragging, maximization, chart search, S/R ray styles, appearance persistence/reset, mode separation, no JS errors/overflow, no activation requests')
 finally:
  server.terminate();server.wait(timeout=15);log.close()
