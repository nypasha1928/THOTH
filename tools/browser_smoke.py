"""Optional Chromium smoke check with synthetic account/market fixtures only.
Run: python tools/browser_smoke.py (requires Playwright and /usr/bin/chromium).
No credentials or exchange execution are used. Writes labelled fixture previews.
"""
import os,subprocess,tempfile,time,json,math,urllib.request,copy,sys
from urllib.parse import parse_qs
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from thoth.analytics import analyze
with tempfile.TemporaryDirectory(prefix='thoth-ui-') as d:
 env={k:v for k,v in os.environ.items() if not k.startswith('THOTH_')};env['THOTH_DB']=d+'/paper.sqlite'
 log=open(d+'/server.log','w')
 server=subprocess.Popen([sys.executable,'-m','uvicorn','thoth.app:app','--host','127.0.0.1','--port','8097'],cwd=root,env=env,stdout=log,stderr=log)
 try:
  base='http://127.0.0.1:8097'
  for _ in range(100):
   try:
    urllib.request.urlopen(base+'/healthz');break
   except Exception:time.sleep(.1)
  def get(path):return json.load(urllib.request.urlopen(base+'/api/'+path))
  payloads={path:get(path) for path in ['live','watchlist','runner','readiness']}
  payloads['live']['enabled_on_server']=True
  payloads['live']['unverified_live_allowed']=True
  payloads['live']['config']['daily_loss_pct']=5
  rows=[];bars=[];sma=[]
  for i in range(1000):
   stamp=1792300000+i*60;value=2400+math.sin(i/9)*8+i*.02
   rows.append(dict(time=stamp,open=value-(1 if i%2==0 else -2),close=value+1,high=value+3,low=value-3,volume=100+(i%30)*50))
   levels={f'{f}_{s}_{k}':dict(value=2400+(-10 if s=='support' else 10)+(3 if k=='minor' else 0),pivot_time=1792300000+max(0,(i//100)*100-20)*60) for f in ('long','short') for s in ('support','resistance') for k in ('major','minor')}
   bars.append(dict(time=stamp,sma=value-4,signal='Long' if i%23==0 else 'Short' if i%29==0 else None,levels=levels));sma.append(dict(time=stamp,value=value-4))
  rsi_bars=[dict(time=row['time'],rsi=50+math.sin(i/9)*20,thresholds={'static30':30,'static70':70,'ml_long':60,'ml_short':40},vwap=row['close']-1,ml_ready=True,rsi_samples=3001,rsi_cluster=0 if math.sin(i/9)<-.3 else 2 if math.sin(i/9)>.3 else 1,events=[dict(kind=['static30_up','static70_down','ml_long_up','ml_short_down','ml_short_up','ml_long_down'][(i//10)%6],side='Long' if (i//10)%6 in (0,2,4) else 'Short')] if i%10==0 else []) for i,row in enumerate(rows)]
  rsi_bars[980]['events'].append(dict(kind='static30_up',side='Long'))
  chart=dict(rsi_analysis=rsi_bars,rsi_status='Corrected ML RSI · synthetic fixture',indicator_status='S/R reference arrows: confirmed candle-close breakouts; not historical live intrabar triggers',source='SIMULATED BROWSER FIXTURE · not exchange results',candles=rows,analysis=bars,sma=sma)
  chart_daily=copy.deepcopy(chart)
  for array in ('candles','analysis','sma','rsi_analysis'):
   for i,row in enumerate(chart_daily[array]):
    row['time']=1700006400+i*86400
    if array=='analysis':
     for level in row['levels'].values():level['pivot_time']=1700006400+max(0,(i//100)*100-20)*86400
  chart_intervals={'1':chart,'D':chart_daily}
  for interval,duration in [('5',300),('60',3600),('240',14400)]:
   item=copy.deepcopy(chart);origin=1792300000//duration*duration
   for array in ('candles','analysis','sma','rsi_analysis'):
    for i,row in enumerate(item[array]):
     row['time']=origin+i*duration
     if array=='analysis':
      for level in row['levels'].values():level['pivot_time']=origin+max(0,(i//100)*100-20)*duration
   chart_intervals[interval]=item
  chart_older=copy.deepcopy(chart)
  old_rows=[];old_bars=[];old_sma=[]
  for i in range(-1000,0):
   stamp=chart['candles'][0]['time']+i*60
   old_rows.append(dict(time=stamp,open=2400,high=2402,low=2398,close=2401,volume=50))
   old_bars.append(dict(time=stamp,levels={},signal=None,sma=2400));old_sma.append(dict(time=stamp,value=2400))
  chart_older['candles']=old_rows+chart['candles'];chart_older['analysis']=old_bars+chart['analysis'];chart_older['sma']=old_sma+chart['sma']
  writes=[];activation=[];errors=[];chart_queries=[];history_many=[]
  def route(req):
   url=req.request.url.split('/api/')[1];path=url.split('?')[0];method=req.request.method
   if path.startswith('chart/'):
    chart_queries.append(url)
    params=parse_qs(url.split('?',1)[1] if '?' in url else '')
    result=chart_older if 'lookback=2000' in url else chart_intervals[params.get('interval',['1'])[0]]
   elif path.startswith('quote/'):
    symbol=path.split('/')[1];environment='live'
    result=dict(symbol=symbol,environment=environment,price=2412.34,observed_at=time.time())
   elif path=='security/status':result=dict(totp_enabled=False,recovery_codes_remaining=0,passkeys=[])
   elif path=='live/history':
    result=dict(items=[dict(symbol='ETHUSDT',side='Long',entry=2400,qty=.02,opened_at=1792299900.123,closed_at=1792300000,pnl=.24,reason='SMA 7 crossed below SMA 50 (intrabar exit)',strategy='sma_cross'),dict(symbol='BTCUSDT',side='Short',entry=60000,qty=.001,closed_at=1792299900,pnl=-.31,reason='SMA 7 crossed above SMA 50 (intrabar exit)',strategy='sr_breakout'),dict(symbol='SOLUSDT',side='Long',entry=100,qty=.5,closed_at=1792299800,pnl=0,reason='Manual close',strategy='sr_breakout')],total=3,offset=0,limit=50)
    if history_many:
     params=parse_qs(url.split('?',1)[1] if '?' in url else '')
     offset=int(params.get('offset',['0'])[0]);result=dict(items=history_many[offset:offset+50],total=len(history_many),offset=offset,limit=50)
   elif path=='live/analysis':
    params=parse_qs(url.split('?',1)[1] if '?' in url else '')
    records=[dict(symbol='ETHUSDT',strategy='sma_cross',pnl=.24),dict(symbol='BTCUSDT',strategy='sr_breakout',pnl=-.31),dict(symbol='SOLUSDT',strategy='sr_breakout',pnl=0)]
    if params.get('symbol'):records=[r for r in records if r['symbol']==params['symbol'][0]]
    if params.get('strategy'):records=[r for r in records if r['strategy']==params['strategy'][0]]
    result=analyze(records)
   elif path.startswith('connection'):result=dict(verified=False,configured=False,reason='Browser fixture: no credentials',balance=None,balance_reason='Not connected')
   elif path in payloads:
    result=payloads[path]
    if path=='live':result={**result,'server_time':time.time()}
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
   # Read-only risk displays use the server clock and never activate orders.
   payloads['live']['risk'].update(day='2026-10-20',baseline_ms=1792454402000,peak_ms=1792458000000,baseline=100,peak=150,loss_allowance=5,loss_used=2,remaining=3,remaining_pct=3,stop_equity=145,loss_pct=2,symbols={'XVGUSDT':dict(streak=3,pause_until=time.time()+125)})
   payloads['live']['events']=[dict(time=time.time()-40+i,message='Synthetic activity '+str(i)) for i in range(40)]
   page.evaluate('(data)=>{updateAccount(data);render();}',copy.deepcopy(payloads['live']))
   expect(page.locator('#dailyCountdown')).to_contain_text('Time until next UTC day:')
   expect(page.locator('#dailyCountdown')).to_contain_text('00:00 UTC')
   page.evaluate("() => { window.savedRiskOffset=riskClockOffset; riskClockOffset=Date.UTC(2026,9,20,23,59,50)-Date.now(); renderRiskOverview(); }")
   expect(page.locator('#dailyCountdown')).to_contain_text('00:10')
   page.evaluate("() => { riskClockOffset=Date.UTC(2026,9,21,0,0,0)-Date.now(); renderRiskOverview(); }")
   expect(page.locator('#dailyCountdown')).to_contain_text('24:00:00')
   page.evaluate("() => { riskClockOffset=window.savedRiskOffset; renderRiskOverview(); }")
   expect(page.locator('#sma_exit_enabled')).to_be_checked()
   expect(page.locator('#sma_enabled')).not_to_be_checked()
   expect(page.locator('#sma_7_50_filter_enabled')).not_to_be_checked()
   expect(page.locator('#dailyBudget')).to_contain_text('$3.00')
   expect(page.locator('#dailyBudgetDetails')).to_contain_text('Allowance: $5.00')
   page.evaluate("() => { lv.sma_exit_blocks={GALAUSDT:{details:{error_type:'ValidationError'},retry_after:0}}; render(); }")
   expect(page.locator('#smaDataStatus')).to_contain_text('blocked only for GALAUSDT')
   page.evaluate("() => { lv.sma_exit_blocks={}; render(); }")
   expect(page.locator('#smaDataStatus')).to_be_empty()
   page.evaluate("() => { window.beforeRateFixture={status:lv.status,reason:lv.reason,entries:lv.entries}; Object.assign(lv,{status:'rate_limited',reason:'Bybit account read rate-limited; entries waiting for verified recovery',entries:false}); render(); }")
   expect(page.locator('#status')).to_contain_text('rate limited')
   expect(page.locator('#start')).to_be_disabled()
   expect(page.locator('#pause')).to_be_enabled()
   page.evaluate("() => { Object.assign(lv,window.beforeRateFixture); render(); }")
   expect(page.locator('#dailyBudgetDetails')).to_contain_text('stop threshold: $145.00')
   expect(page.locator('#dailyBudgetDetails')).to_contain_text('Baseline recorded: 2026-10-20 00:00:02 UTC')
   expect(page.locator('#dailyBudgetDetails')).to_contain_text('Peak recorded: 2026-10-20 01:00:00 UTC')
   page.evaluate("() => { lv.risk.baseline_required=true; renderRiskOverview(); }")
   expect(page.locator('#dailyBudget')).to_contain_text('pending baseline review')
   expect(page.locator('#dailyBudget')).not_to_contain_text('$3.00')
   page.evaluate("() => { lv.risk.baseline_required=false; renderRiskOverview(); }")
   expect(page.locator('#coinCooldowns')).to_contain_text('XVGUSDT')
   first_timer=page.locator('#coinCooldowns').inner_text();page.wait_for_timeout(1200)
   assert page.locator('#coinCooldowns').inner_text()!=first_timer
   expect(page.locator('#events li')).to_have_count(30)
   expect(page.locator('#events li').first).to_contain_text('Synthetic activity 39')
   expect(page.locator('#events li').last).to_contain_text('Synthetic activity 10')
   expect(page.locator('#events')).to_have_css('overflow-y','auto')
   page.select_option('#symbol','ETHUSDT')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   expect(page.locator('#chartScope')).to_contain_text('Included in saved trading list')
   page.click('[data-page-link=security]');expect(page.locator('#accountSecurity')).to_be_visible()
   expect(page.locator('#settings')).not_to_be_visible()
   page.reload();expect(page.locator('#page-security')).to_be_visible()
   page.click('[data-page-link=chart]');page.select_option('#symbol','ETHUSDT')
   page.click('[data-page-link=settings]');expect(page.locator('#accountSecurity')).not_to_be_visible()
   expect(page.locator('#page-chart')).not_to_be_visible()
   page.click('#onlyChart');expect(page.locator('#selectedSymbols button')).to_have_count(1)
   expect(page.locator('#selectedSymbols')).to_contain_text('ETHUSDT')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   # Draft edits cannot trigger activation; no order route is reached.
   page.click('#start');expect(page.locator('#symbolSaveMessage')).to_contain_text('Save your strategy')
   assert not activation
   page.click('[data-page-link=chart]');page.select_option('#symbol','SOLUSDT')
   page.click('[data-page-link=settings]');page.click('#saveSymbols');expect(page.locator('#savedSymbols')).not_to_contain_text('SOLUSDT')
   expect(page.locator('#symbol')).to_have_value('ETHUSDT')
   assert writes[-1][1]['symbols']==['ETHUSDT']
   if page.locator('#sma_7_50_filter_enabled').is_visible():
    page.check('#sma_enabled');page.check('#sma_7_50_filter_enabled');page.click('#saveSymbols')
    assert writes[-1][1]['sma_7_50_filter_enabled'] is True
    assert writes[-1][1]['sma_enabled'] is True
    page.uncheck('#sma_enabled');page.uncheck('#sma_7_50_filter_enabled');page.click('#saveSymbols')
   page.click('[data-page-link=chart]');page.select_option('#symbol','SOLUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   assert writes[-1][1]['symbols']==['ETHUSDT']
   page.click('[data-page-link=settings]');page.fill('#search','BTC');page.click('#selectVisible');expect(page.locator('#selectedSymbols button')).to_have_count(2)
   page.check('#selectedOnly');expect(page.locator('#coins input')).to_have_count(1)
   page.fill('#search','NO_SUCH_SYMBOL');expect(page.locator('#coins')).to_contain_text('No matching symbols')
   page.click('#clearSymbols');expect(page.locator('#selectedSymbols button')).to_have_count(0)
   page.click('#onlyChart');page.fill('#search','');page.uncheck('#selectedOnly');page.click('#saveSymbols')
   expect(page.locator('#savedSymbols')).to_contain_text('Saved trading symbols (1): SOLUSDT')
   page.click('[data-page-link=chart]');page.click('#older');expect(page.locator('#latest')).to_be_enabled();page.click('#latest');expect(page.locator('#latest')).to_be_disabled()
   page.locator('#chart').click(position={'x':100,'y':160});expect(page.locator('#candleInfo')).to_contain_text(' O ')
   assert page.locator('#chart path').count()>30
   page.uncheck('#showLevels');page.uncheck('#showSignals');page.check('#fitLevels')
   page.check('#showLevels');page.check('#showSignals')
   page.locator('#zoom').fill('35');page.locator('#zoom').dispatch_event('input')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   # Chart-only search and coin-name navigation must never modify trading settings.
   saved_before=copy.deepcopy(writes[-1][1]['symbols']);write_count=len(writes)
   page.click('#chartSearch');page.fill('#chartSearchInput','ETHUSDT')
   page.locator('#chartSearchResults button').filter(has_text='ETHUSDT').click()
   expect(page.locator('#symbol')).to_have_value('ETHUSDT')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   page.click('[data-page-link=settings]');page.get_by_role('button',name='View SOLUSDT chart',exact=True).click()
   expect(page.locator('#symbol')).to_have_value('SOLUSDT')
   assert len(writes)==write_count and writes[-1][1]['symbols']==saved_before
   # Actual horizontal drag in both directions, including pointer capture across redraws.
   page.locator('#chart').scroll_into_view_if_needed()
   box=page.locator('#chart').bounding_box();sx=box['x']+box['width']*.45;sy=box['y']+box['height']*.5
   page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx+90,sy,steps=6);page.mouse.up()
   offset=page.evaluate('() => chartOffset');assert offset>0,offset
   page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx-50,sy,steps=6);page.mouse.up()
   assert page.evaluate('() => chartOffset')<offset
   page.click('#latest');expect(page.locator('#latest')).to_be_disabled()
   # Wheel zoom acts on the chart; vertical mouse dragging moves the price view.
   page.locator('#chart').scroll_into_view_if_needed()
   box=page.locator('#chart').bounding_box();sx=box['x']+box['width']*.5;sy=box['y']+box['height']*.5
   page.mouse.move(sx,sy);before=page.evaluate('() => visibleBars');page.mouse.wheel(0,-200)
   expect(page.locator('#zoom')).not_to_have_value(str(before))
   zoomed=page.evaluate('() => visibleBars');assert zoomed<before
   page.mouse.wheel(0,200)
   expect(page.locator('#zoom')).not_to_have_value(str(zoomed))
   if page.locator('#latest').is_enabled():page.click('#latest')
   page.locator('#chart').scroll_into_view_if_needed()
   box=page.locator('#chart').bounding_box();sx=box['x']+box['width']*.5;sy=box['y']+box['height']*.5
   page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx,sy+45,steps=6);page.mouse.up()
   assert page.evaluate('() => chartPriceShift')>0
   expect(page.locator('#latest')).to_be_enabled()
   page.click('#resetChartView');expect(page.locator('#zoom')).to_have_value('70')
   assert page.evaluate('() => chartPriceShift')==0
   expect(page.locator('#latest')).to_be_disabled()
   # Ruler is display-only: a drag shows signed price/percentage, intervals and time.
   page.click('#measureChart');page.locator('#chart').scroll_into_view_if_needed()
   box=page.locator('#chart').bounding_box();sx=box['x']+box['width']*.25;sy=box['y']+box['height']*.3
   page.mouse.move(sx,sy);page.mouse.down();page.mouse.move(sx+80,sy-30,steps=6);page.mouse.up()
   expect(page.locator('[data-measurement]')).to_have_count(1)
   expect(page.locator('#measureChart')).to_have_attribute('aria-pressed','false')
   expect(page.locator('[data-ruler-summary]')).to_contain_text('%')
   finished=page.locator('#measurementReadout').text_content();page.mouse.move(sx+110,sy+20)
   expect(page.locator('#measurementReadout')).to_have_text(finished)
   expect(page.locator('#measurementReadout')).to_contain_text('Price move:')
   expect(page.locator('#measurementReadout')).to_contain_text('candle intervals')
   stats=page.evaluate('() => measurementStats({time:0,price:100},{time:120,price:105},[{time:0},{time:60},{time:120}])')
   assert stats==dict(delta=5,percent=5,bars=2,seconds=120),stats
   page.click('#clearMeasurement');expect(page.locator('[data-measurement]')).to_have_count(0)
   page.click('#measureChart')
   page.locator('#chart').click(position={'x':90,'y':80});expect(page.locator('#measurementReadout')).to_contain_text('First point set')
   page.locator('#chart').click(position={'x':180,'y':120});expect(page.locator('#measurementReadout')).to_contain_text('Price move:')
   page.click('#clearMeasurement')
   expect(page.locator('#measureChart')).to_have_attribute('aria-pressed','false')
   # Price-axis zoom changes only price span, not candle count or symbol settings.
   page.locator('#chart').scroll_into_view_if_needed();box=page.locator('#chart').bounding_box()
   ax=box['x']+box['width']-25;ay=box['y']+box['height']*.5
   count=page.evaluate('() => visibleBars')
   page.mouse.move(ax,ay);page.mouse.wheel(0,-160)
   page.locator('#chart').hover(position={'x':box['width']-25,'y':box['height']*.5})
   assert page.evaluate('() => chartPriceZoom')<1
   assert page.evaluate('() => visibleBars')==count
   page.mouse.move(ax,ay);page.mouse.down();page.mouse.move(ax,ay+40,steps=5);page.mouse.up()
   assert page.evaluate('() => chartPriceZoom')<.85
   page.mouse.dblclick(ax,ay);assert page.evaluate('() => chartPriceZoom')==1
   page.uncheck('#autoChart');page.wait_for_function('() => !chartLoading')
   before_refresh=len(chart_queries)
   page.evaluate('async () => {chartPriceZoom=.8;chartPriceShift=2;chartOffset=0;await loadChart(true)}')
   assert len(chart_queries)>before_refresh
   assert page.evaluate('() => chartPriceZoom')==.8 and page.evaluate('() => chartPriceShift')==2
   page.click('#autoPriceScale');page.check('#autoChart')
   expect(page.locator('[data-extreme="Now"]')).to_contain_text('2,412.34')
   expect(page.locator('#quoteStatus')).to_contain_text('Current price:')
   expect(page.locator('[data-extreme="High"]')).to_contain_text('High')
   expect(page.locator('[data-extreme="Low"]')).to_contain_text('Low')
   assert page.locator('[data-time-tick]').count()==70
   ticks=page.locator('[data-time-tick]').evaluate_all('(nodes) => nodes.map(n=>Number(n.dataset.timeTick))')
   assert all(b-a==60 for a,b in zip(ticks,ticks[1:]))
   page.locator('#chart').click(position={'x':150,'y':120})
   expect(page.locator('[data-crosshair-time]')).to_be_visible()
   expect(page.locator('[data-crosshair-time]')).to_contain_text('UTC')
   page.select_option('#tf','D');expect(page.locator('#title')).to_contain_text('1D')
   expect(page.locator('[data-time-label]').first).to_contain_text('2026-')
   expect(page.locator('[data-time-tick]')).to_have_count(70)
   expect(page.locator('[data-time-tick]').first).to_have_attribute('data-time-tick',str(chart_daily['candles'][-70]['time']))
   ticks=page.locator('[data-time-tick]').evaluate_all('(nodes) => nodes.map(n=>Number(n.dataset.timeTick))')
   assert all(b-a==86400 for a,b in zip(ticks,ticks[1:]))
   for interval,label,duration in [('5','5m',300),('60','1h',3600),('240','4h',14400)]:
    page.select_option('#tf',interval);expect(page.locator('#title')).to_contain_text(label)
    ticks=page.locator('[data-time-tick]').evaluate_all('(nodes)=>nodes.map(n=>Number(n.dataset.timeTick))')
    assert all(b-a==duration for a,b in zip(ticks,ticks[1:]))
    expect(page.locator('[data-sma50]')).to_have_count(1);expect(page.locator('[data-sma200]')).to_have_count(1)
    expect(page.locator('#strategyTf')).to_have_value('1')
   page.select_option('#tf','1');expect(page.locator('#title')).to_contain_text('1m')
   # Display-only overlays use pre-viewport warmup and independent volume scale.
   expect(page.locator('[data-sma7]')).to_have_attribute('stroke','#ffffff')
   expect(page.locator('[data-volume-bar]')).to_have_count(70)
   expect(page.locator('[data-volume-ma]')).to_have_attribute('stroke','#f2b84b')
   expect(page.locator('[data-extreme="Now"] rect').first).to_have_attribute('fill','#ffdf00')
   expect(page.locator('[data-price-line="Current price"]')).to_have_attribute('stroke','#ffdf00')
   average=page.evaluate('() => {const r=Array.from({length:25},(_,i)=>({time:i,close:i+1,volume:(i+1)*10}));return [displayAverage(r,7,"close").get(24),displayAverage(r,20,"volume").get(24),displayAverage(r,7,"close").has(5)]}')
   assert average==[22,155,False],average
   averages=page.evaluate('() => {const rows=Array.from({length:250},(_,i)=>({time:i,close:i+1}));return [displayAverage(rows,50,"close").get(249),displayAverage(rows,200,"close").get(249),displayAverage(rows,200,"close").has(198)]}')
   assert averages==[225.5,150.5,False],averages
   assert payloads['live']['config']['sma_enabled'] is False
   expect(page.locator('[data-sma50]')).to_have_count(1)
   expect(page.locator('[data-sma200]')).to_have_count(1)
   # Quote/candle alignment: update only the forming candle and retain observed
   # extrema; never change closed bars, volume, source arrays, or trade settings.
   check=page.evaluate('''() => {
    const now=Math.floor(Date.now()/1000),stamp=Math.floor(now/60)*60;
    const rows=[{time:stamp-60,open:99,high:101,low:98,close:100,volume:7},{time:stamp,open:100,high:101,low:99,close:100,volume:3}];
    const original=JSON.stringify(rows),context={symbol:'SOLUSDT',environment:'live',interval:'1',now:stamp+30};
    const q={symbol:'SOLUSDT',environment:'live',price:103,observed_at:stamp+30};
    const first=synchronizeFormingCandle(rows,q,context);
    const second=synchronizeFormingCandle(rows,{...q,price:102},context,first.state);
    return {unchanged:original===JSON.stringify(rows),closed:first.rows[0].close,close:second.rows[1].close,high:second.rows[1].high,volume:second.rows[1].volume,
      historical:synchronizeFormingCandle(rows,q,{...context,now:stamp+60}).rows===rows,
      wrongSymbol:synchronizeFormingCandle(rows,{...q,symbol:'ETHUSDT'},context).rows===rows,
      stale:synchronizeFormingCandle(rows,{...q,observed_at:stamp},context).rows===rows};
   }''')
   assert check==dict(unchanged=True,closed=100,close=102,high=103,volume=3,historical=True,wrongSymbol=True,stale=True),check
   geometry=page.evaluate('''() => {
    const saved=chartRows,oldQuote=currentQuote,oldState=formingDisplayState;
    const stamp=Math.floor(Date.now()/60000)*60;
    chartRows=Array.from({length:30},(_,i)=>({time:stamp-(29-i)*60,open:100,high:101,low:99,close:100,volume:2}));
    currentQuote={symbol:$('symbol').value,environment:mode,price:103,observed_at:Date.now()/1000};draw();
    const tag=document.querySelector('[data-extreme="Now"]'),rect=tag.querySelector('rect'),high=document.querySelector('[data-extreme="High"] rect');
    const result={close:Number(document.querySelector('[data-candle]:last-of-type')?.dataset.candleClose||[...document.querySelectorAll('[data-candle]')].at(-1).dataset.candleClose),
      labelCenter:Number(rect.getAttribute('y'))+10,priceY:Number(tag.dataset.priceY),highCenter:Number(high.getAttribute('y'))+10};
    chartRows=saved;currentQuote=oldQuote;formingDisplayState=oldState;draw();return result;
   }''')
   assert geometry['close']==103,geometry
   assert abs(geometry['labelCenter']-geometry['priceY'])<.001,geometry
   assert abs(geometry['labelCenter']-geometry['highCenter'])>=22,geometry
   countdowns=page.evaluate('() => [candleCountdown(0,"1"),candleCountdown(59000,"1"),candleCountdown(60000,"1"),candleCountdown(86399000,"D"),candleCountdown(86400000,"D")]')
   assert countdowns==['01:00','00:01','01:00','00:00:01','24:00:00'],countdowns
   assert page.evaluate('() => [candleCountdown(0,"5"),candleCountdown(0,"60"),candleCountdown(0,"240")]')==['05:00','01:00:00','04:00:00']
   expect(page.locator('[data-candle-countdown]')).to_be_visible()
   timer=page.locator('[data-candle-countdown]').text_content()
   expect(page.locator('[data-candle-countdown]')).not_to_have_text(timer,timeout=3000)
   expect(page.locator('#candleCountdown')).to_contain_text('1m candle closes in')
   rays=page.locator('[data-sr-key]');expect(rays).to_have_count(8)
   major=page.locator('[data-sr-key="long_resistance_major"]')
   expect(major).to_have_attribute('stroke','#b70909');expect(major).to_have_attribute('stroke-width','4')
   assert ' V' not in major.get_attribute('d')
   expect(page.locator('[data-sr-key="short_support_major"]')).to_have_attribute('stroke-dasharray','12 10')
   page.click('#appearanceToggle');expect(page.locator('#chartAppearance')).to_be_visible()
   candle=page.locator('[data-candle]').last
   before_x=float(candle.get_attribute('x'))
   page.get_by_label('Chart background',exact=True).fill('#f1f5f2')
   expect(page.locator('#chart')).to_have_css('background-color','rgb(241, 245, 242)')
   page.get_by_label('Show current price line and label',exact=True).uncheck()
   expect(page.locator('[data-extreme="Now"]')).to_have_count(0)
   expect(page.locator('[data-price-line="Current price"]')).to_have_count(0)
   page.get_by_label('Show current price line and label',exact=True).check()
   expect(page.locator('[data-extreme="Now"]')).to_have_count(1)
   page.get_by_label('Chart background',exact=True).fill('#16202b')
   page.get_by_label('Right spacing (bars)',exact=True).fill('48')
   assert float(page.locator('[data-candle]').last.get_attribute('x'))<before_x
   page.get_by_label('Volume with MA 20',exact=True).uncheck();expect(page.locator('[data-volume-bar]')).to_have_count(0)
   page.get_by_label('Volume with MA 20',exact=True).check();expect(page.locator('[data-volume-bar]')).to_have_count(70)
   for period,color in [(7,'#ffffff'),(50,'#81d4fa'),(200,'#2962ff')]:
    expect(page.locator('[data-sma'+str(period)+']')).to_have_attribute('stroke',color)
   page.get_by_label('SMA 50 color',exact=True).fill('#55bbdd')
   expect(page.locator('[data-sma50]')).to_have_attribute('stroke','#55bbdd')
   page.get_by_label('SMA 200 color',exact=True).fill('#3344cc')
   expect(page.locator('[data-sma200]')).to_have_attribute('stroke','#3344cc')
   page.get_by_label('SMA 7 color',exact=True).fill('#eeeeee')
   expect(page.locator('[data-sma7]')).to_have_attribute('stroke','#eeeeee')
   saved_colors=page.evaluate('() => JSON.parse(localStorage.getItem(chartStyleKey))')
   assert saved_colors['sma7']=='#eeeeee' and saved_colors['sma50']=='#55bbdd' and saved_colors['sma']=='#3344cc'
   page.get_by_label('Bullish candle',exact=True).fill('#12abcd')
   expect(page.locator('[data-candle="bull"]').first).to_have_attribute('fill','#12abcd')
   page.get_by_label('S/R / SMA bullish arrow',exact=True).fill('#aabbcc')
   expect(page.locator('[data-rsi-line=rsi]')).to_have_count(1)
   for color in ['#00ff00','#ff0000','#808080']:
    assert page.locator('[data-rsi-segment="'+color+'"]').count()>0
   for value in [30,70]:expect(page.locator('[data-rsi-static-level="'+str(value)+'"]')).to_have_attribute('stroke','#81d4fa')
   expected_levels={'static30_up':27,'static70_down':73,'ml_long_up':57,'ml_short_down':43,'ml_short_up':37,'ml_long_down':63}
   expected_colors={'static30_up':'#4caf50','static70_down':'#ff5252','ml_long_up':'#00e676','ml_short_down':'#ffeb3b','ml_short_up':'#2962ff','ml_long_down':'#ff9800'}
   for kind,level in expected_levels.items():
    pane=page.locator('[data-rsi-pane-signal][data-rsi-kind="'+kind+'"]').first
    chart_marker=page.locator('[data-rsi-signal][data-rsi-kind="'+kind+'"]').first
    expect(pane).to_have_attribute('data-rsi-value',str(level))
    expect(chart_marker).to_have_attribute('fill',expected_colors[kind])
    assert pane.get_attribute('x')==chart_marker.get_attribute('x')
   pane=page.locator('[data-rsi-pane-signal][data-rsi-kind="ml_short_up"]').first
   pane.hover();expect(page.locator('[data-signal-tooltip]')).to_have_attribute('visibility','visible')
   expect(page.locator('[data-signal-tooltip]')).to_contain_text('Bullish ML Short Up')
   expect(page.locator('#rsiHoverReadout')).to_contain_text('RSI at ')
   cross_d=page.locator('[data-chart-crosshair]').get_attribute('d');geometry=page.evaluate('() => chartGeometry')
   assert 'V'+str(int(geometry['H']-55)) in cross_d,cross_d
   page.get_by_label('Color candles on S/R signals',exact=True).check()
   assert page.locator('[data-sr-candle="Long"]').count()>0
   page.get_by_label('Show S/R breakout arrows',exact=True).uncheck()
   expect(page.locator('[data-signal-source="S/R"]')).to_have_count(0)
   assert page.locator('[data-sr-candle="Long"]').count()>0
   page.get_by_label('Show S/R breakout arrows',exact=True).check()
   page.get_by_label('Color candles on S/R signals',exact=True).uncheck()
   expect(page.locator('[data-sr-candle="Long"]')).to_have_count(0)
   assert page.locator('[data-rsi-signal]').count()>0
   page.locator('#rsiVisualSettings').evaluate('(el)=>el.open=true')
   page.locator('[data-rsi-style="ml_short_up"]').evaluate('(el)=>el.open=true')
   page.get_by_label('Bullish ML Short Up chart color',exact=True).fill('#2244ff')
   expect(page.locator('[data-rsi-signal][data-rsi-kind="ml_short_up"]').first).to_have_attribute('fill','#2244ff')
   expect(page.locator('[data-rsi-pane-signal][data-rsi-kind="ml_short_up"]').first).to_have_attribute('fill','#2962ff')
   stored_rsi=page.evaluate('() => JSON.parse(localStorage.getItem(chartStyleKey)).rsiSignalStyles.ml_short_up')
   assert stored_rsi['chartColor']=='#2244ff' and stored_rsi['paneColor']=='#2962ff'
   page.get_by_label('Bullish ML Short Up chart color',exact=True).fill('#2962ff')
   page.locator('#rsiVisualSettings').evaluate('(el)=>el.open=false')
   display_write_count=len(writes)
   page.get_by_label('Show RSI signal arrows',exact=True).uncheck()
   expect(page.locator('[data-rsi-signal]')).to_have_count(0)
   expect(page.locator('[data-rsi-line=rsi]')).to_have_count(1)
   page.get_by_label('Show RSI signal arrows',exact=True).check()
   page.get_by_label('Show RSI indicator pane',exact=True).uncheck()
   expect(page.locator('[data-rsi-line=rsi]')).to_have_count(0)
   assert page.locator('[data-rsi-signal]').count()>0
   page.get_by_label('Show RSI indicator pane',exact=True).check()
   assert len(writes)==display_write_count
   expect(page.locator('#chartStatus')).to_contain_text('S/R reference arrows')
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
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('SOLUSDT');expect(page.locator('#liveDialog')).to_be_visible();page.click('#activateLive');expect(page.locator('#activationError')).to_contain_text('Type ENABLE LIVE ORDERS exactly');assert not activation;page.keyboard.press('Escape');expect(page.locator('#liveDialog')).not_to_be_visible()
   # Poll update locks every mutation without discarding the form draft.
   payloads['live']['entries']=True
   expect(page.locator('#clearSymbols')).to_be_disabled(timeout=10000)
   expect(page.locator('#coins input').first).to_be_disabled()
   payloads['live']['entries']=False
   expect(page.locator('#clearSymbols')).to_be_enabled(timeout=10000)
   page.set_viewport_size({'width':390,'height':844});page.reload()
   assert page.evaluate('() => chartStyle.bull')=='#12abcd'
   assert page.evaluate('() => chartStyle.background')=='#16202b'
   page.click('#appearanceToggle');page.click('#resetAppearance')
   assert page.evaluate('() => chartStyle.bull')=='#26a69a'
   page.click('#closeAppearance')
   expect(page.locator('#savedSymbols')).to_contain_text('SOLUSDT')
   page.select_option('#symbol','ETHUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   page.click('[data-page-link=settings]');page.click('#onlyChart');page.click('#saveSymbols');expect(page.locator('#savedSymbols')).to_contain_text('ETHUSDT')
   page.select_option('#strategyLogic','sma_cross');expect(page.locator('#strategyDescription')).to_contain_text('SMA 7')
   for control in ['sma_enabled','sma_7_50_filter_enabled','sma_length']:
    expect(page.locator('#'+control)).to_be_visible()
   expect(page.locator('#sma_enabled').locator('..')).to_contain_text('S/R entry filter')
   expect(page.locator('#sma_7_50_filter_enabled').locator('..')).to_contain_text('S/R entry filter')
   for interval in ['5','60','240']:
    page.select_option('#strategyTf',interval);page.click('#saveSymbols')
    page.wait_for_function('(value)=>lv.config.timeframe===value',arg=interval)
    assert writes[-1][1]['timeframe']==interval
    expect(page.locator('#tf')).to_have_value('1')
   page.select_option('#strategyTf','1')
   page.select_option('#oppositeAction','close_only');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.strategy==='sma_cross' && lv.config.opposite_action==='close_only'")
   assert writes[-1][1]['strategy']=='sma_cross' and writes[-1][1]['opposite_action']=='close_only'
   page.select_option('#strategyLogic','sr_hourly_intrabar')
   expect(page.locator('#strategyTf')).to_have_value('60');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#sma_exit_enabled')).to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#major')).to_be_visible();expect(page.locator('#minor')).to_be_visible()
   expect(page.locator('#strategyDescription')).to_contain_text('1-minute SMA exits')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_hourly_intrabar'")
   assert writes[-1][1]['timeframe']=='60' and writes[-1][1]['sma_exit_enabled']
   expect(page.locator('#tf')).to_have_value('1')
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('S/R 1h → Exit 1m');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','rsi_hourly_intrabar')
   expect(page.locator('#strategyDescription')).to_contain_text('Corrected ML + static RSI')
   expect(page.locator('#strategyTf')).to_have_value('60');expect(page.locator('#strategyTf')).to_be_disabled()
   page.locator('#rsiSettings').evaluate('(el)=>el.open=true')
   expect(page.locator('#rsi_ml_signals')).to_be_checked();expect(page.locator('#rsi_static_signals')).to_be_checked()
   page.fill('#rsi_max_data','50');page.uncheck('#rsi_volume_filter');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.strategy==='rsi_hourly_intrabar' && lv.config.rsi.max_data===50")
   assert not writes[-1][1]['rsi']['volume_filter']
   page.select_option('#strategyLogic','sma_cross')
   expect(page.locator('#strategyTf')).to_be_enabled();expect(page.locator('#sma_exit_enabled')).to_be_enabled()
   page.select_option('#strategyTf','1');page.click('#saveSymbols')
   expect(page.locator('#take_profit_enabled')).not_to_be_checked()
   page.check('#take_profit_enabled');page.fill('#take_profit_dollars','1.25');page.click('#saveSymbols')
   page.wait_for_function('() => lv.config.take_profit_enabled && lv.config.take_profit_dollars===1.25')
   assert writes[-1][1]['take_profit_enabled'] and writes[-1][1]['take_profit_dollars']==1.25
   page.uncheck('#take_profit_enabled');page.click('#saveSymbols')
   expect(page.locator('#lossStreakScope')).to_have_value('per_coin')
   page.check('#trailing_enabled');page.fill('#trailing_activation_pct','12.5');page.fill('#trailing_distance_pct','0.8')
   expect(page.locator('#trailingActivationBasis')).to_have_value('margin');expect(page.locator('#trailingBasis')).to_have_value('price')
   page.click('#saveSymbols');page.wait_for_function('() => lv.config.trailing_enabled && lv.config.trailing_activation_pct===12.5')
   assert writes[-1][1]['trailing_distance_pct']==.8
   page.locator('#page-settings').screenshot(path=str(root/'docs/mobile-settings-fixture.png'))
   page.click('[data-page-link=chart]')
   page.locator('#chart').click(position={'x':130,'y':120});expect(page.locator('#candleInfo')).to_contain_text(' O ')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   page.click('#chartSearch');page.fill('#chartSearchInput','ETHUSDT');page.keyboard.press('Enter')
   expect(page.locator('#chartSearchDialog')).not_to_be_visible()
   page.click('#maximizeChart');expect(page.locator('#maximizeChart')).to_have_attribute('aria-expanded','true')
   assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
   assert page.locator('#chartCard').bounding_box()['y']==0
   page.screenshot(path=str(root/'docs/mobile-interactive-chart-fixture.png'))
   page.click('#maximizeChart')
   # Real touch events exercise Android measurement without native page scrolling.
   page.click('#measureChart');page.locator('#chart').scroll_into_view_if_needed()
   expect(page.locator('#chart')).to_have_css('touch-action','none')
   box=page.locator('#chart').bounding_box();geometry=page.evaluate('() => chartGeometry');tx=box['x']+box['width']*.2;ty=box['y']+box['height']*(geometry['T']+geometry['B'])/2/geometry['H']
   touch=page.context.new_cdp_session(page);touch.send('Emulation.setTouchEmulationEnabled',{'enabled':True,'maxTouchPoints':1})
   touch.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':tx,'y':ty}]})
   for step in range(1,7):touch.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':tx+step*10,'y':ty-step*4}]})
   touch.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
   expect(page.locator('#measureChart')).to_have_attribute('aria-pressed','false')
   expect(page.locator('[data-ruler-summary]')).to_contain_text('%')
   expect(page.locator('#measurementReadout')).to_contain_text('candle intervals')
   page.locator('#chartCard').screenshot(path=str(root/'docs/mobile-measurement-fixture.png'))
   page.click('#clearMeasurement')
   page.click('#measureChart');page.locator('#chart').click(position={'x':100,'y':80})
   expect(page.locator('#measurementReadout')).to_contain_text('First point set')
   page.click('#measureChart');expect(page.locator('#measureChart')).to_have_attribute('aria-pressed','false')
   expect(page.locator('[data-measurement]')).to_have_count(0)
   page.click('#resetChartView');page.locator('#chart').evaluate("el=>el.scrollIntoView({block:'center'})")
   box=page.locator('#chart').bounding_box();tx=box['x']+box['width']*.3;ty=box['y']+box['height']*.4
   scroll=page.evaluate('scrollY')
   touch.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':tx,'y':ty}]})
   for i in range(1,7):touch.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':tx+i*8,'y':ty+i*4}]})
   touch.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
   assert page.evaluate('() => chartOffset')>0
   assert page.evaluate('() => chartPriceShift')!=0
   assert abs(page.evaluate('scrollY')-scroll)<2
   touch.send('Emulation.setTouchEmulationEnabled',{'enabled':True,'maxTouchPoints':2})
   before=page.evaluate('() => visibleBars');cx=box['x']+box['width']*.4
   touch.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':cx-25,'y':ty,'id':0},{'x':cx+25,'y':ty,'id':1}]})
   queries=len(chart_queries);page.evaluate('async () => await loadChart(true)');assert len(chart_queries)==queries
   for i in range(1,7):touch.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':cx-25-i*5,'y':ty,'id':0},{'x':cx+25+i*5,'y':ty,'id':1}]})
   touch.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
   assert page.evaluate('() => visibleBars')<before
   assert page.evaluate('() => chartTouches.size')==0
   touch.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':cx-25,'y':ty,'id':0},{'x':cx+25,'y':ty,'id':1}]})
   touch.send('Input.dispatchTouchEvent',{'type':'touchCancel','touchPoints':[]})
   assert page.evaluate('() => chartTouches.size===0 && chartPinch===null')
   page.click('#resetChartView')
   # Display-only trailing overlays use the managed position's frozen settings.
   overlay_position=dict(symbol=page.locator('#symbol').input_value(),side='Long',qty='.02',entry='2410',mark='2420',pnl=.2,stop='2415',stop_dollars=1,take_profit=dict(enabled=True,dollars=1,verified=True,exchange_price='2425'),trailing=dict(enabled=True,active=True,activation_price='2412',stop='2415'))
   payloads['live']['positions']=[overlay_position]
   page.evaluate('(position) => {lv.positions=[position];draw()}',overlay_position)
   expect(page.locator('[data-price-line="Trailing exchange stop"]')).to_have_count(1)
   expect(page.locator('[data-price-line="Trailing activated"]')).to_have_count(1)
   page.locator('#chartCard').screenshot(path=str(root/'docs/mobile-trailing-fixture.png'))
   expect(page.locator('[data-price-line="Exchange take profit"]')).to_have_count(1)
   payloads['live']['positions']=[]
   page.evaluate('() => {lv.positions=[];draw();}')
   page.screenshot(path=str(root/'docs/mobile-watchlist-fixture.png'),full_page=True)
   page.click('#loadOlderHistory');expect(page.locator('#historyStatus')).to_contain_text('2000 candles loaded',timeout=10000)
   page.click('#oldest');expect(page.locator('#older')).to_be_disabled()
   assert page.evaluate('() => chartRows.length')==2000
   expect(page.locator('#historyStatus')).to_contain_text('Historical replay')
   queries=len(chart_queries);page.evaluate('async () => await loadChart(true)');assert len(chart_queries)==queries
   assert page.evaluate('() => chartLookback')==2000
   page.click('#latest');expect(page.locator('#latest')).to_be_disabled()
   expect(page.locator('#historyStatus')).to_contain_text('1000 candles loaded')
   assert page.evaluate('() => chartLookback')==1000
   page.click('[data-page-link=positions]');expect(page.locator('#page-positions')).to_be_visible();expect(page.locator('#page-chart')).not_to_be_visible()
   page.evaluate("() => {lv.positions=[{symbol:'ETHUSDT',side:'Long',qty:'.02',entry:'2400',mark:'2400',pnl:0,opened_at:1792299900123}];render();}")
   expect(page.locator('#positions time')).to_have_attribute('datetime','2026-10-18T05:05:00.123Z')
   expect(page.locator('#positions time')).to_contain_text('EDT')
   page.evaluate('() => {lv.positions=[];render();}')
   page.click('[data-page-link=history]');expect(page.locator('#historyRows')).to_contain_text('ETHUSDT');expect(page.locator('#historyRows')).to_contain_text('SMA 7/200')
   expect(page.locator('#historyRows')).to_contain_text('S/R Breakout')
   expect(page.locator('#historyRows tr').first.locator('time').first).to_have_attribute('datetime','2026-10-18T05:05:00.123Z')
   expect(page.locator('#historyRows tr').first.locator('time').first).to_contain_text('EDT')
   expect(page.locator('#historyRows .reason-cell').filter(has_text='SMA 7/50 (EXIT)')).to_have_count(2)
   expect(page.locator('#historyRows .reason-cell').first).to_have_attribute('title','SMA 7 crossed below SMA 50 (intrabar exit)')
   expect(page.locator('#historyRows tr').nth(1).locator('td').first).to_have_text('—')

   expect(page.locator('#historyRows tr.trade-win')).to_have_count(1)
   expect(page.locator('#historyRows tr.trade-win')).to_contain_text('ETHUSDT')
   expect(page.locator('#historyRows .pnl-win')).to_have_text('$0.24')
   assert page.locator('#historyRows tr.trade-win td').first.evaluate('(el) => getComputedStyle(el).backgroundColor')=='rgba(82, 196, 125, 0.15)'
   expect(page.locator('#historyRows tr:not(.trade-win):not(.trade-loss)')).to_have_count(1)
   expect(page.locator('#historyRows tr.trade-loss')).to_have_count(1)
   expect(page.locator('#historyRows tr.trade-loss')).to_contain_text('BTCUSDT')
   expect(page.locator('#historyRows .pnl-loss')).to_have_text('-$0.31')
   assert page.locator('#historyRows tr.trade-loss td').first.evaluate('(el) => getComputedStyle(el).backgroundColor')=='rgba(255, 110, 110, 0.15)'
   expect(page.get_by_role('heading',name='THOTH Trading',exact=True)).to_have_count(1)
   page.locator('#page-history').screenshot(path=str(root/'docs/mobile-history-fixture.png'))
   history_many.extend(dict(symbol='ETHUSDT',side='Long',entry=2400,qty=.02,closed_at=1792300000-i,pnl=.01,reason='Synthetic trade '+str(i),strategy='sr_breakout') for i in range(123))
   page.click('#historyRefresh');expect(page.locator('#historyRows tr')).to_have_count(50)
   page.get_by_role('button',name='History page 2',exact=True).click()
   expect(page.locator('#tradeHistoryStatus')).to_contain_text('51–100 of 123')
   expect(page.locator('#historyRows tr').first).to_contain_text('Synthetic trade 50')
   page.fill('#historyPageNumber','3');page.click('#historyJump button')
   expect(page.locator('#tradeHistoryStatus')).to_contain_text('101–123 of 123')
   expect(page.locator('#historyRows tr')).to_have_count(23)
   expect(page.get_by_role('button',name='History page 3',exact=True)).to_have_attribute('aria-current','page')
   page.get_by_role('button',name='History page 1',exact=True).click()
   expect(page.locator('#tradeHistoryStatus')).to_contain_text('1–50 of 123')
   history_many.clear();page.click('#historyRefresh');expect(page.locator('#historyRows tr')).to_have_count(3)

   page.click('[data-page-link=analysis]');expect(page.locator('#analysisStatus')).to_contain_text('3 confirmed')
   expect(page.locator('#coinAnalysis')).to_contain_text('BTCUSDT');expect(page.locator('#strategyAnalysis')).to_contain_text('SMA 7/200')
   page.fill('#analysisSymbol','ETHUSDT');expect(page.locator('#analysisStatus')).to_contain_text('1 confirmed')
   expect(page.locator('#coinAnalysis tr.trade-win')).to_have_count(1)
   expect(page.locator('#coinAnalysis tr')).to_have_count(1);expect(page.locator('#coinAnalysis')).to_contain_text('$0.24')
   page.select_option('#analysisStrategy','sr_breakout');expect(page.locator('#analysisStatus')).to_contain_text('No recorded')
   page.fill('#analysisSymbol','');page.select_option('#analysisStrategy','');expect(page.locator('#analysisStatus')).to_contain_text('3 confirmed')
   page.locator('#page-analysis').screenshot(path=str(root/'docs/mobile-analysis-fixture.png'))
   page.reload();expect(page.locator('#page-analysis')).to_be_visible()
   page.click('[data-page-link=history]')
   page.reload();expect(page.locator('#page-history')).to_be_visible()
   page.click('[data-page-link=chart]')
   expect(page.locator('#viewMode')).to_have_count(0)
   expect(page.locator('#paperPanel')).to_have_count(0)
   page.evaluate("() => localStorage.setItem('thoth.chart.appearance.v1',JSON.stringify({bull:'bad',signalSize:999,long_major:{width:0,style:'unsafe',support:'bad'}}))")
   page.reload();expect(page.locator('#savedSymbols')).to_contain_text('ETHUSDT')
   assert page.evaluate('() => chartStyle.bull')=='#26a69a'
   assert page.evaluate('() => chartStyle.long_major.width')==4
   assert not errors,errors
   assert not activation,activation
   browser.close()
  print('PASS: fixed-dollar daily budget display, per-coin cooldown countdown, 30 scrollable activities, live confirmation validation, separate Chart/Positions/History/Settings pages, bookmark reload, SMA strategy and close-only settings saves, retained trade display, desktop/mobile watchlist, separate saved/draft/chart symbols, saves, active-entry lock, mouse dragging in time/price, wheel zoom/reset, right price-axis zoom, visible High/Low, synchronized current price/candle, fixed price-label geometry, second-by-second candle countdown, paginated history, one-shot mouse/touch price/time ruler, yellow current price, right spacing, volume MA20 and white SMA7, minute/day ticks, save-to-chart synchronization, maximization, chart search, S/R ray styles, appearance persistence/reset, live-only controls, no JS errors/overflow, no activation requests')
 finally:
  server.terminate();server.wait(timeout=15);log.close()
