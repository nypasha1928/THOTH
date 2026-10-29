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
  rsi_bars=[dict(time=row['time'],rsi=50+math.sin(i/9)*20,thresholds={'static30':30,'static70':70,'ml_long':60,'ml_short':40},vwap=row['close']-1,ml_ready=True,rsi_samples=3001,rsi_cluster=0 if math.sin(i/9)<-.3 else 2 if math.sin(i/9)>.3 else 1,events=[dict(kind=['static30_up','static70_down','ml_long_up','ml_short_down','ml_short_up','ml_long_down','static30_down','static70_up'][(i//8)%8],side='Long' if (i//8)%8 in (0,2,4,7) else 'Short')] if i%8==0 else []) for i,row in enumerate(rows)]
  for bar in rsi_bars:bar['crossings']=copy.deepcopy(bar['events'])
  rsi_bars[975]['crossings']=[dict(kind='static70_up',side='Long')]
  rsi_bars[975]['events']=[]
  rsi_bars[980]['events'].extend([dict(kind='static30_up',side='Long'),dict(kind='ml_long_up',side='Long')])
  rsi_bars[980]['crossings']=copy.deepcopy(rsi_bars[980]['events'])
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
  comparison_writes=[];writes=[];activation=[];errors=[];chart_queries=[];history_many=[];analysis_many=[];alert_items=[];entry_delay_items=[];delta_preview=[False];ob_preview=[False];sr_preview=[False]
  def route(req):
   url=req.request.url.split('/api/')[1];path=url.split('?')[0];method=req.request.method
   if path.startswith('chart/'):
    chart_queries.append(url)
    params=parse_qs(url.split('?',1)[1] if '?' in url else '')
    result=chart_older if 'lookback=2000' in url else chart_intervals[params.get('interval',['1'])[0]]
    if delta_preview[0] and params.get('include_volume_delta')==['true']:result={**result,'volume_delta_analysis':[dict(time=result['candles'][-1]['time'],signal='Long')],'volume_delta_status':'Synthetic Volume Delta fixture; not exchange evidence'}
    if ob_preview[0] and params.get('include_order_block')==['true']:
     last=result['candles'][-1];result={**result,'order_block_analysis':[dict(time=last['time'],events=[dict(kind='bullish_formed',side='Long')],zones={'demand':[dict(time=last['time']-600,high=last['close']+1,low=last['close']-2)],'supply':[dict(time=last['time']-3600,high=last['close']+7,low=last['close']+5)]})],'order_block_status':'Synthetic Order Block reference fixture'}
    if sr_preview[0]:result={**result,'sr_analysis':[{**bar,'signal':'Long','provisional':True} if bar['time']==result['candles'][-1]['time'] else bar for bar in result['analysis']]}
   elif path.startswith('quote/'):
    symbol=path.split('/')[1];environment='live'
    result=dict(symbol=symbol,environment=environment,price=2412.34,observed_at=time.time())
   elif path=='live/signal-alerts':
    params=parse_qs(url.split('?',1)[1] if '?' in url else '')
    records=[item for item in alert_items if not params.get('symbol') or item['symbol']==params['symbol'][0]]
    offset=int(params.get('offset',['0'])[0]);limit=int(params.get('limit',['50'])[0])
    watched={int(value) for value in params.get('watch_ids',[''])[0].split(',') if value}
    result=dict(items=list(reversed(records))[offset:offset+limit],updates=[item for item in records if item['id'] in watched],total=len(records),limit=limit,offset=offset)
   elif path=='security/status':result=dict(totp_enabled=False,recovery_codes_remaining=0,passkeys=[])
   elif path=='live/trailing-comparison':
    result=dict(mode='observation_only',executor='fixed',settings=dict(enabled=True,minimum=.1,maximum=.4,multiplier=2,fee_bps=5.5),total=1,offset=0,limit=50,dropped=0,error=None,items=[dict(id=1,started=1792300000,position_count=11,symbols=['ETHUSDT','BTCUSDT'],fixed_triggered=1792300100,fixed_gross=1.4,realized=1.25,actual_closed_at=1792300105,last_sample=1792300130,dynamic=dict(active=True,triggered=True,triggered_at=1792300130,result_gross=1.6,peak=2,threshold=1.6,distance=.4),assumed_fees=.2,estimate_after_assumed_fees_before_funding=1.4,gross_quote_difference=.2,warnings=[])])
   elif path=='live/trailing-comparison/settings' and method=='PUT':
    result=dict(settings=req.request.post_data_json);comparison_writes.append(req.request.post_data_json)
   elif path=='live/market-leaders':
    from thoth.market_leaders import report
    result=report()
   elif path=='live/watchlist-review':result=dict(items=[dict(symbol='WLDUSDT',trades=5,wins=3,losses=2,realized_pnl=.5,signal_events=8,skipped_events=2,rejected_orders=1,rejection_events=1,entry_check_failures=1,median_check_gap_seconds=192.6,median_qualified_to_submit_seconds=4,latest_speed=None,review_note='Review recorded rejection/check failure',rejection_codes={'110126':1})],selected=1,traded=1,closed_trades=5,realized_pnl=.5,scope=parse_qs(url.split('?',1)[1] if '?' in url else '').get('scope',['current'])[0],strategy='order_block_sr_intrabar',limitations='Retained records only; no automatic changes.')
   elif path=='live/fast-movers':result=dict(items=[dict(symbol='WLDUSDT',change_pct=1.2,speed_pct_per_minute=.4,gap_seconds=180,age_seconds=12)],total_ranked=1,tracked=1,stale_excluded=0,strategy='order_block_intrabar')
   elif path=='live/entry-diagnostics':result=dict(items=entry_delay_items,total=len(entry_delay_items),limit=50,offset=0)
   elif path=='live/history':
    result=dict(items=[dict(symbol='ETHUSDT',side='Long',entry=2400,qty=.02,opened_at=1792299900.123,closed_at=1792300000,pnl=.24,reason='SMA 7 crossed below SMA 50 (intrabar exit)',strategy='sma_cross'),dict(symbol='BTCUSDT',side='Short',entry=60000,qty=.001,closed_at=1792299900,pnl=-.31,reason='SMA 7 crossed above SMA 50 (intrabar exit)',strategy='sr_breakout',entry_source_label='S/R short-term minor support'),dict(symbol='SOLUSDT',side='Long',entry=100,qty=.5,closed_at=1792299800,pnl=0,reason='Open P&L trailing stop',strategy='sr_breakout')],total=3,offset=0,limit=50)
    if history_many:
     params=parse_qs(url.split('?',1)[1] if '?' in url else '')
     offset=int(params.get('offset',['0'])[0]);result=dict(items=history_many[offset:offset+50],total=len(history_many),offset=offset,limit=50)
   elif path=='live/analysis':
    params=parse_qs(url.split('?',1)[1] if '?' in url else '')
    records=list(analysis_many) if analysis_many else [dict(symbol='ETHUSDT',strategy='sma_cross',pnl=.24,opened_at=1792501200,closed_at=1792508400),dict(symbol='BTCUSDT',strategy='sr_breakout',entry_sources=['sr:short_support_minor'],pnl=-.31,opened_at=1792504800,closed_at=1792512000),dict(symbol='SOLUSDT',strategy='sr_breakout',pnl=0,closed_at=1792515600)]
    if params.get('symbol'):records=[r for r in records if r['symbol']==params['symbol'][0]]
    if params.get('strategy'):records=[r for r in records if r['strategy']==params['strategy'][0]]
    result=analyze(records,params.get('time_basis',['entry'])[0],params.get('source',[None])[0])
   elif path=='phone-alerts':result=dict(public_key='',devices=[],recent_pauses=[])
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
   page.route('**/api/**',route);page.goto(base);expect(page.locator('#savedSymbolList')).to_contain_text('SOLUSDT')
   # S/R visibility is independent of bot filters/timeframe and other arrow layers.
   for interval in ['1','5','60','240','D']:
    page.select_option('#tf',interval);expect(page.locator('[data-signal-source="S/R"]').first).to_be_visible()
   page.select_option('#tf','1');page.uncheck('#showSignals')
   expect(page.locator('[data-signal-source="S/R"]').first).to_be_visible()
   page.uncheck('#showSRSignals');expect(page.locator('[data-signal-source="S/R"]')).to_have_count(0)
   page.check('#showSRSignals');page.check('#showSignals')
   sr_preview[0]=True;page.click('#refresh')
   expect(page.locator('[data-sr-provisional="true"]')).to_have_count(1)
   expect(page.locator('[data-sr-provisional="true"] title')).to_contain_text('Forming candle preview')
   sr_preview[0]=False;page.click('#refresh')
   page.evaluate("() => localStorage.setItem(chartStyleKey,JSON.stringify({...chartStyle,showSRArrows:false,srDisplayVersion:1}))")
   page.reload();expect(page.locator('#showSRSignals')).to_be_checked()
   page.uncheck('#showSRSignals');page.reload();expect(page.locator('#showSRSignals')).not_to_be_checked()
   page.check('#showSRSignals');expect(page.locator('[data-signal-source="S/R"]').first).to_be_visible()
   assert not writes  # Chart controls never saved bot configuration.

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
   page.evaluate("() => {lv.risk.symbols.FIXTUREUSDT={streak:2,pause_until:0};renderRiskOverview()}")
   expect(page.locator('#coinLossStreaks')).to_contain_text('FIXTUREUSDT')
   expect(page.locator('#coinCooldowns')).not_to_contain_text('FIXTUREUSDT')
   assert page.locator('#coinLossStreaks').evaluate('(el)=>getComputedStyle(el).overflowY')=='auto'
   assert not page.locator('.saved-symbol-details').evaluate('(el)=>el.open')
   first_timer=page.locator('#coinCooldowns').inner_text()
   expect(page.locator('#coinCooldowns')).not_to_have_text(first_timer,timeout=5000)
   expect(page.locator('#events li')).to_have_count(30)
   expect(page.locator('#events li').first).to_contain_text('Synthetic activity 39')
   expect(page.locator('#events li').last).to_contain_text('Synthetic activity 10')
   expect(page.locator('#events')).to_have_css('overflow-y','auto')
   page.select_option('#symbol','ETHUSDT')
   expect(page.locator('#savedSymbolList')).to_contain_text('SOLUSDT')
   expect(page.locator('#chartScope')).to_contain_text('Included in saved trading list')
   page.click('[data-page-link=security]');expect(page.locator('#accountSecurity')).to_be_visible()
   expect(page.locator('#settings')).not_to_be_visible()
   page.reload();expect(page.locator('#page-security')).to_be_visible()
   page.click('[data-page-link=chart]');page.select_option('#symbol','ETHUSDT')
   page.click('[data-page-link=settings]');expect(page.locator('#accountSecurity')).not_to_be_visible()
   expect(page.locator('#page-chart')).not_to_be_visible()
   page.click('#onlyChart');expect(page.locator('#selectedSymbols button')).to_have_count(1)
   expect(page.locator('#selectedSymbols')).to_contain_text('ETHUSDT')
   expect(page.locator('#savedSymbolList')).to_contain_text('SOLUSDT')
   # Draft edits cannot trigger activation; no order route is reached.
   page.click('#start');expect(page.locator('#symbolSaveMessage')).to_contain_text('Save your strategy')
   assert not activation
   page.click('[data-page-link=chart]');page.select_option('#symbol','SOLUSDT')
   page.click('[data-page-link=settings]');page.click('#saveSymbols');expect(page.locator('#savedSymbolList')).not_to_contain_text('SOLUSDT')
   expect(page.locator('#symbol')).to_have_value('ETHUSDT')
   assert writes[-1][1]['symbols']==['ETHUSDT']
   if page.locator('#sma_7_50_filter_enabled').is_visible():
    page.check('#sma_enabled');page.check('#sma_7_50_filter_enabled');page.click('#saveSymbols');page.wait_for_function('() => lv.config.sma_7_50_filter_enabled && lv.config.sma_enabled')
    assert writes[-1][1]['sma_7_50_filter_enabled'] is True
    assert writes[-1][1]['sma_enabled'] is True
    page.uncheck('#sma_enabled');page.uncheck('#sma_7_50_filter_enabled');page.click('#saveSymbols');page.wait_for_function('() => !lv.config.sma_7_50_filter_enabled && !lv.config.sma_enabled')
   page.click('[data-page-link=chart]');page.select_option('#symbol','SOLUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   assert writes[-1][1]['symbols']==['ETHUSDT']
   page.click('[data-page-link=settings]');page.fill('#search','BTC');page.click('#selectVisible');expect(page.locator('#selectedSymbols button')).to_have_count(2)
   page.check('#selectedOnly');expect(page.locator('#coins input')).to_have_count(1)
   page.fill('#search','NO_SUCH_SYMBOL');expect(page.locator('#coins')).to_contain_text('No matching symbols')
   page.click('#selectAllCoins');expect(page.locator('#selectedSymbols button')).to_have_count(page.evaluate('watchlist.filter(x=>x.selectable&&inActiveSymbolCatalogue(x)).length'))
   page.click('#saveSymbols');page.wait_for_function('() => lv.config.symbols.length===watchlist.filter(x=>x.selectable&&inActiveSymbolCatalogue(x)).length')
   assert set(writes[-1][1]['symbols'])==set(page.evaluate('watchlist.filter(x=>x.selectable&&inActiveSymbolCatalogue(x)).map(x=>x.symbol)'))
   page.reload();page.click('[data-page-link=settings]');expect(page.locator('#selectedSymbols button')).to_have_count(len(writes[-1][1]['symbols']))
   page.click('[data-page-link=chart]');page.select_option('#symbol','SOLUSDT');page.click('[data-page-link=settings]')
   page.click('#clearSymbols');expect(page.locator('#selectedSymbols button')).to_have_count(0)
   page.click('#onlyChart');page.fill('#search','');page.uncheck('#selectedOnly');page.click('#saveSymbols')
   expect(page.locator('#savedSymbolList')).to_contain_text('SOLUSDT');expect(page.locator('#savedSymbols')).to_contain_text('Saved trading symbols (1)')
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
   expect(page.locator('#savedSymbolList')).to_contain_text('SOLUSDT')
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
   expect(page.locator('[data-crosshair-time]')).to_contain_text('EDT')
   page.select_option('#tf','D');expect(page.locator('#title')).to_contain_text('1D')
   expect(page.locator('[data-time-label]').first).to_contain_text('2026-')
   # Chart timezone is independent of the browser and handles both DST changes.
   assert page.evaluate("() => chartTime(Date.parse('2026-10-22T08:36:44Z')/1000)")=='2026-10-22 04:36 EDT'
   assert page.evaluate("() => chartTime(Date.parse('2026-01-22T08:36:44Z')/1000)")=='2026-01-22 03:36 EST'
   assert page.evaluate("() => chartTime(Date.parse('2026-03-08T06:59:00Z')/1000)")=='2026-03-08 01:59 EST'
   assert page.evaluate("() => chartTime(Date.parse('2026-03-08T07:00:00Z')/1000)")=='2026-03-08 03:00 EDT'
   assert page.evaluate("() => chartTime(Date.parse('2026-11-01T05:30:00Z')/1000)")=='2026-11-01 01:30 EDT'
   assert page.evaluate("() => chartTime(Date.parse('2026-11-01T06:30:00Z')/1000)")=='2026-11-01 01:30 EST'
   expect(page.locator('#historyStatus')).to_contain_text('New York')
   expect(page.locator('#candleCountdown')).to_contain_text('New York display')

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
   expect(page.locator('[data-volume-ma]')).to_have_attribute('stroke','#81d4fa')
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
   assert page.locator('#appearanceFields > details.indicator-dropdown').count()==10
   delta_preview[0]=True;page.click('#refresh')
   expect(page.locator('[data-signal-source="Volume Delta"]')).to_have_count(1)
   assert page.evaluate('referenceSignalStrategy')!='volume_delta_intrabar'
   expect(page.locator('[data-signal-source="Volume Delta"]')).to_contain_text('▲')
   page.get_by_text('Volume Delta signals',exact=True).click()
   page.get_by_label('Show Volume Delta reference arrows',exact=True).uncheck();expect(page.locator('[data-signal-source="Volume Delta"]')).to_have_count(0)
   page.get_by_label('Show Volume Delta reference arrows',exact=True).check();expect(page.locator('[data-signal-source="Volume Delta"]')).to_have_count(1)
   delta_preview[0]=False;page.click('#refresh');expect(page.locator('[data-signal-source="Volume Delta"]')).to_have_count(0)
   page.get_by_text('Volume Delta signals',exact=True).click()
   page.get_by_text('Order Block boxes',exact=True).click();ob_preview[0]=True;page.click('#refresh')
   expect(page.locator('[data-order-block-zone="demand"]')).to_have_count(1)
   expect(page.locator('[data-order-block-zone="demand"]')).to_have_attribute('stroke','#2157f3')
   expect(page.locator('[data-order-block-strength="demand"]')).to_contain_text('Strength: 3')
   expect(page.locator('[data-order-block-zone="supply"]')).to_have_attribute('stroke','#ff5d00')
   page.locator('#chart').screenshot(path='/tmp/thoth-order-block-boxes-fixture.png')
   expect(page.locator('[data-signal-source^="Order Block"]')).to_have_count(0)
   page.get_by_label('Bullish block color',exact=True).fill('#123abc')
   expect(page.locator('[data-order-block-zone="demand"]')).to_have_attribute('stroke','#123abc')
   page.get_by_label('Show Order Block boxes',exact=True).uncheck();expect(page.locator('[data-order-block-zone]')).to_have_count(0)
   page.get_by_label('Show Order Block boxes',exact=True).check();expect(page.locator('[data-order-block-zone="demand"]')).to_have_count(1)
   ob_preview[0]=False;page.click('#refresh');page.get_by_text('Order Block boxes',exact=True).click()
   assert page.locator('#appearanceFields > details.indicator-dropdown[open]').count()==0
   page.locator('#appearanceFields > details.indicator-dropdown').evaluate_all('(items)=>items.forEach(el=>el.open=true)')
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
   for color in ['#00ff00','#ff0000','#787b86']:
    assert page.locator('[data-rsi-segment="'+color+'"]').count()>0
   expect(page.locator('[data-rsi-cluster]')).to_have_count(0)
   expect(page.locator('[data-rsi-segment]').first).to_have_attribute('stroke-width','3')
   for value in [30,70]:expect(page.locator('[data-rsi-static-level="'+str(value)+'"]')).to_have_attribute('stroke','#81d4fa')
   expected_levels={'static30_down':33,'static70_up':67,'static30_up':27,'static70_down':73,'ml_long_up':57,'ml_short_down':43,'ml_short_up':37,'ml_long_down':63}
   expected_colors={'static30_down':'#e91e63','static70_up':'#26a69a','static30_up':'#4caf50','static70_down':'#ff5252','ml_long_up':'#00e676','ml_short_down':'#ffeb3b','ml_short_up':'#2962ff','ml_long_down':'#ff9800'}
   for kind,level in expected_levels.items():
    pane=page.locator('[data-rsi-pane-signal][data-rsi-kind="'+kind+'"]').first
    chart_marker=page.locator('[data-rsi-signal][data-rsi-kind="'+kind+'"]').first
    expect(pane).to_have_attribute('data-rsi-value',str(level))
    expect(chart_marker).to_have_attribute('fill',expected_colors[kind])
    assert pane.get_attribute('x')==chart_marker.get_attribute('x')
   pane=page.locator('[data-rsi-pane-signal][data-rsi-kind="ml_short_up"]').first
   pane.hover()
   # Quote polling replaces the SVG; verify one hover event's results atomically.
   hover_geometry=page.evaluate("() => {const el=document.querySelector('[data-rsi-pane-signal][data-rsi-kind=ml_short_up]');const r=el.getBoundingClientRect(); el.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2,pointerType:'mouse'})); const tip=document.querySelector('[data-signal-tooltip]');return {path:document.querySelector('[data-chart-crosshair]').getAttribute('d'),height:chartGeometry.H,tooltip:tip.textContent,visible:tip.getAttribute('visibility'),readout:document.querySelector('#rsiHoverReadout').textContent};}")
   assert hover_geometry['visible']=='visible'
   assert 'Bullish Signal (ML Short Up)' in hover_geometry['tooltip']
   assert 'RSI at ' in hover_geometry['readout']
   assert 'V'+str(int(hover_geometry['height']-55)) in hover_geometry['path'],hover_geometry
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
   page.get_by_label('RSI Long colour (curve and threshold)',exact=True).fill('#11ee55')
   expect(page.locator('[data-rsi-line=ml_long]')).to_have_attribute('stroke','#11ee55')
   assert page.locator('[data-rsi-segment="#11ee55"]').count()>0
   expect(page.locator('[data-rsi-value-tag=ml_long]')).to_have_attribute('fill','#11ee55')
   page.get_by_label('RSI Short colour (curve and threshold)',exact=True).fill('#ee2255')
   expect(page.locator('[data-rsi-line=ml_short]')).to_have_attribute('stroke','#ee2255')
   assert page.locator('[data-rsi-segment="#ee2255"]').count()>0
   expect(page.locator('[data-rsi-value-tag=ml_short]')).to_have_attribute('fill','#ee2255')
   page.get_by_label('RSI Long colour (curve and threshold)',exact=True).fill('#00ff00')
   page.get_by_label('RSI Short colour (curve and threshold)',exact=True).fill('#ff0000')
   raw_arrow_count=page.locator('[data-rsi-signal]').count()
   page.get_by_label('Show only RSI crossings passing entry filters',exact=True).check()
   assert page.locator('[data-rsi-signal]').count()<raw_arrow_count
   page.get_by_label('Show only RSI crossings passing entry filters',exact=True).uncheck()
   assert page.locator('[data-rsi-signal]').count()==raw_arrow_count
   page.locator('[data-rsi-style="ml_short_up"]').evaluate('(el)=>el.open=true')
   page.get_by_label('Bullish Signal (ML Short Up) chart color',exact=True).fill('#2244ff')
   expect(page.locator('[data-rsi-signal][data-rsi-kind="ml_short_up"]').first).to_have_attribute('fill','#2244ff')
   expect(page.locator('[data-rsi-pane-signal][data-rsi-kind="ml_short_up"]').first).to_have_attribute('fill','#2962ff')
   stored_rsi=page.evaluate('() => JSON.parse(localStorage.getItem(chartStyleKey)).rsiSignalStyles.ml_short_up')
   assert stored_rsi['chartColor']=='#2244ff' and stored_rsi['paneColor']=='#2962ff'
   page.get_by_label('Bullish Signal (ML Short Up) chart color',exact=True).fill('#2962ff')
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
   pane_write_count=len(writes);expanded_price_height=page.evaluate('() => chartGeometry.B-chartGeometry.T')
   page.click('#toggleVolumePane');expect(page.locator('[data-volume-bar]')).to_have_count(0)
   expect(page.locator('[data-volume-minimized]')).to_have_count(1)
   expect(page.locator('#toggleVolumePane')).to_have_text('Expand Volume')
   assert page.evaluate('() => chartGeometry.B-chartGeometry.T')>expanded_price_height
   page.click('#toggleVolumePane');expect(page.locator('[data-volume-bar]')).to_have_count(70)
   page.click('#toggleRSIPane');expect(page.locator('[data-rsi-line=rsi]')).to_have_count(0)
   expect(page.locator('[data-rsi-minimized]')).to_have_count(1)
   assert page.evaluate('() => chartGeometry.B-chartGeometry.T')>expanded_price_height
   page.click('#toggleRSIPane');expect(page.locator('[data-rsi-line=rsi]')).to_have_count(1)
   page.click('#closeRSIPane');expect(page.locator('[data-rsi-line=rsi]')).to_have_count(0)
   expect(page.locator('[data-rsi-minimized]')).to_have_count(0)
   expect(page.locator('#showRSIPane')).to_be_visible()
   assert page.evaluate('() => JSON.parse(localStorage.getItem(chartStyleKey)).showRSI') is False
   page.click('#showRSIPane');expect(page.locator('[data-rsi-line=rsi]')).to_have_count(1)
   assert len(writes)==pane_write_count
   page.screenshot(path=str(root/'docs/desktop-watchlist-fixture.png'),full_page=True)
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('SOLUSDT');expect(page.locator('#liveDialog')).to_be_visible();page.click('#activateLive');expect(page.locator('#activationError')).to_contain_text('Type ENABLE LIVE ORDERS exactly');assert not activation;page.keyboard.press('Escape');expect(page.locator('#liveDialog')).not_to_be_visible()
   # Poll update locks every mutation without discarding the form draft.
   payloads['live']['entries']=True
   expect(page.locator('#clearSymbols')).to_be_disabled(timeout=10000)
   expect(page.locator('#selectAllCoins')).to_be_disabled()
   expect(page.locator('#coins input').first).to_be_disabled()
   payloads['live']['entries']=False
   expect(page.locator('#clearSymbols')).to_be_enabled(timeout=10000)
   page.set_viewport_size({'width':390,'height':844});page.reload()
   assert page.evaluate('() => chartStyle.bull')=='#12abcd'
   assert page.evaluate('() => chartStyle.background')=='#16202b'
   page.click('#appearanceToggle');page.click('#resetAppearance')
   assert page.evaluate('() => chartStyle.bull')=='#26a69a'
   page.click('#closeAppearance')
   expect(page.locator('#savedSymbolList')).to_contain_text('SOLUSDT')
   page.select_option('#symbol','ETHUSDT');expect(page.locator('#chartScope')).to_contain_text('not enabled for trading')
   page.click('[data-page-link=settings]');page.click('#onlyChart');page.click('#saveSymbols');expect(page.locator('#savedSymbolList')).to_contain_text('ETHUSDT')
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
   page.check('#coin_pnl_trailing_enabled');page.fill('#coin_pnl_trailing_activation_dollars','0.5');page.fill('#coin_pnl_trailing_drop_dollars','0.025');page.click('#saveSymbols')
   page.wait_for_function('() => lv.config.coin_pnl_trailing_enabled && lv.config.coin_pnl_trailing_activation_dollars===0.5 && lv.config.coin_pnl_trailing_drop_dollars===0.025')
   expect(page.locator('#pnl_trailing_enabled')).not_to_be_checked()
   page.uncheck('#coin_pnl_trailing_enabled');page.click('#saveSymbols')
   page.wait_for_function('() => !lv.config.coin_pnl_trailing_enabled')
   for new_strategy in ['sma_7_50','sr_sma_7_50']:
    page.select_option('#strategyLogic',new_strategy)
    expect(page.locator('#strategyTf')).to_be_enabled();page.select_option('#strategyTf','5')
    expect(page.locator('#sma_exit_enabled')).to_be_enabled()
    page.uncheck('#sma_exit_enabled');page.click('#saveSymbols')
    page.wait_for_function('(name) => lv.config.strategy===name && !lv.config.sma_exit_enabled',arg=new_strategy)
    assert writes[-1][1]['strategy']==new_strategy and writes[-1][1]['timeframe']=='5'
    page.check('#sma_exit_enabled');page.click('#saveSymbols')
    page.wait_for_function('() => lv.config.sma_exit_enabled')
   page.select_option('#strategyLogic','sr_hourly_intrabar')
   expect(page.locator('#strategyTf')).to_be_enabled();page.select_option('#strategyTf','60')
   expect(page.locator('#sma_exit_enabled')).to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#major')).to_be_visible();expect(page.locator('#minor')).to_be_visible()
   expect(page.locator('#strategyDescription')).to_contain_text('1-minute SMA exits')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_hourly_intrabar'")
   assert writes[-1][1]['timeframe']=='60' and writes[-1][1]['sma_exit_enabled']
   expect(page.locator('#tf')).to_have_value('1')
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('S/R intrabar → Exit 1m');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','volume_delta_intrabar')
   expect(page.locator('#volumeDeltaSettings')).to_be_visible()
   expect(page.locator('#vd_sma_filter')).to_be_checked()
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('NO SMA EXIT')
   page.select_option('#strategyTf','5');page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='volume_delta_intrabar'")
   assert writes[-1][1]['volume_delta']['sma_filter'] and not writes[-1][1]['sma_exit_enabled']
   page.uncheck('#vd_sma_filter');page.click('#saveSymbols');page.wait_for_function('() => !lv.config.volume_delta.sma_filter')
   page.check('#vd_sma_filter');page.click('#saveSymbols');page.wait_for_function('() => lv.config.volume_delta.sma_filter')
   page.select_option('#strategyLogic','sr_rsi_volume_gate')
   expect(page.locator('#strategyTf')).to_have_value('1');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#vd_sma_filter')).to_be_checked();expect(page.locator('#vd_sma_filter')).to_be_disabled()
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#lossStreakScope')).to_have_value('per_coin');expect(page.locator('#lossStreakScope')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('opens Long OR Short')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_rsi_volume_gate'")
   assert writes[-1][1]['timeframe']=='1' and writes[-1][1]['volume_delta']['sma_filter']
   assert not writes[-1][1]['sma_exit_enabled'] and not writes[-1][1]['rsi']['volume_filter']
   page.evaluate("() => {lv.entry_gates={ETHUSDT:{phase:'armed',losses:2,primary_timeframe:'D'}};renderRiskOverview()}")
   expect(page.locator('#entryGateList')).to_contain_text('ARMED · 1m Volume Delta may enter Long or Short')
   page.evaluate("() => {lv.entry_gates.ETHUSDT={phase:'cooldown',losses:3,cooldown_until:Date.now()/1000+3600};renderRiskOverview()}")
   expect(page.locator('#entryGateList')).to_contain_text('LOCKED · cooldown')
   protected_fields=['daily_loss_pct','stop_dollars','pnl_trailing_enabled','pnl_trailing_activation_dollars','pnl_trailing_drop_dollars','coin_pnl_trailing_enabled','daily_equity_trailing_enabled','three_day_equity_trailing_enabled','weekly_equity_trailing_enabled','consecutive_losses','pause_minutes']
   before_ob_risk={key:payloads['live']['config'][key] for key in protected_fields}
   page.select_option('#strategyLogic','order_block_intrabar')
   expect(page.locator('#strategyTf')).to_have_value('240');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#oppositeAction')).to_be_enabled()
   expect(page.locator('#orderBlockSettings')).not_to_be_visible();expect(page.locator('#rsiSettings')).not_to_be_visible()
   expect(page.locator('#volumeDeltaSettings')).not_to_be_visible()
   expect(page.locator('#sma_enabled')).not_to_be_checked();expect(page.locator('#sma_enabled')).to_be_disabled()
   expect(page.locator('#sma_7_50_filter_enabled')).not_to_be_checked()
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('DO NOT wait for close')
   expect(page.locator('#strategyDescription')).to_contain_text('volume MA 20')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='order_block_intrabar'")
   assert writes[-1][1]['timeframe']=='240' and not writes[-1][1]['order_block']['filter_breaks']
   assert all(writes[-1][1]['order_block'][kind] for kind in ['bullish_formed','bearish_formed','supply_breakout','demand_breakdown'])
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('order_block_intrabar')
   expect(page.locator('#rsiSettings')).not_to_be_visible();expect(page.locator('#strategyTf')).to_have_value('240')
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('Order Block · 4h · Volume MA');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','order_block_sr_intrabar')
   expect(page.locator('#strategyTf')).to_have_value('240');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#major')).to_be_visible();expect(page.locator('#major')).to_be_checked();expect(page.locator('#major')).to_be_disabled()
   expect(page.locator('#minor')).to_be_visible();expect(page.locator('#minor')).to_be_checked();expect(page.locator('#minor')).to_be_disabled()
   expect(page.locator('#sma_enabled')).not_to_be_checked();expect(page.locator('#sma_exit_enabled')).not_to_be_checked()
   expect(page.locator('#strategyDescription')).to_contain_text('Order Block OR S/R Major and Minor independently')
   expect(page.locator('#strategyDescription')).to_contain_text('BOTH branches require forming 4h Volume > MA 20')
   expect(page.locator('#rsiSettings')).not_to_be_visible()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='order_block_sr_intrabar'")
   assert writes[-1][1]['timeframe']=='240' and writes[-1][1]['major'] and writes[-1][1]['minor']
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('order_block_sr_intrabar')
   expect(page.locator('#major')).to_be_checked();expect(page.locator('#minor')).to_be_checked()
   expect(page.locator('#rsiSettings')).not_to_be_visible()
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('Order Block + S/R · 4h · Volume MA');page.keyboard.press('Escape')
   # Separate TradFi list is draft-only; original Main selection survives saving/reload.
   main_symbols=page.evaluate('formDraft.symbols.slice()')
   page.select_option('#activeSymbolList','tradfi')
   expect(page.locator('#selectedSymbols button')).to_have_count(294)
   assert page.evaluate('lv.config.active_symbol_list')=='main'
   page.fill('#search','UMC');expect(page.locator('#coins input')).to_have_count(1)
   page.fill('#search','');page.click('#selectAllCoins')
   expect(page.locator('#selectedSymbols button')).to_have_count(294)
   page.select_option('#strategyLogic','order_block_sr_1h')
   expect(page.locator('#strategyTf')).to_have_value('60');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('BOTH branches require forming 1h Volume > MA 20')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='order_block_sr_1h'")
   assert writes[-1][1]['saved_symbol_lists']['main']==main_symbols
   assert writes[-1][1]['active_symbol_list']=='tradfi' and writes[-1][1]['timeframe']=='60'
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#activeSymbolList')).to_have_value('tradfi')
   expect(page.locator('#selectedSymbols button')).to_have_count(294)
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('Order Block + S/R · 1h');page.keyboard.press('Escape')
   # Curated leader drafts never overwrite the full lists or enable entries.
   original_tradfi=page.evaluate('formDraft.symbols.slice()')
   writes_before_leaders=len(writes)
   page.select_option('#activeSymbolList','tradfi_leaders')
   expect(page.locator('#selectedSymbols button')).to_have_count(50)
   page.click('#clearSymbols');expect(page.locator('#selectedSymbols button')).to_have_count(0)
   page.click('#loadMarketLeaders');expect(page.locator('#selectedSymbols button')).to_have_count(50)
   expect(page.locator('#marketLeadersStatus')).to_contain_text('not a verified')
   assert len(writes)==writes_before_leaders
   assert set(page.evaluate('formDraft.symbols'))>=set(['EURUSDUSDT','GBPUSDUSDT','USDJPYUSDT','XAUUSDT'])
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.active_symbol_list==='tradfi_leaders'")
   assert writes[-1][1]['saved_symbol_lists']['tradfi']==original_tradfi
   page.reload();expect(page.locator('#activeSymbolList')).to_have_value('tradfi_leaders')
   expect(page.locator('#selectedSymbols button')).to_have_count(50)
   page.select_option('#activeSymbolList','main_leaders');expect(page.locator('#selectedSymbols button')).to_have_count(50)
   expect(page.locator('#selectedSymbols')).to_contain_text('BTCUSDT')
   page.select_option('#activeSymbolList','main')
   assert page.evaluate('formDraft.symbols')==main_symbols
   page.select_option('#strategyLogic','order_block_sr_intrabar');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.active_symbol_list==='main'")
   page.select_option('#strategyLogic','order_block_sr_1h_inverted_close')
   expect(page.locator('#strategyTf')).to_have_value('60');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('bullish/Long source opens SHORT')
   expect(page.locator('#strategyDescription')).to_contain_text('bearish/Short source opens LONG')
   expect(page.locator('#strategyDescription')).to_contain_text('NO volume filter')
   expect(page.locator('#strategyDescription')).to_contain_text('CLOSED candles only')
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_enabled')).not_to_be_checked()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='order_block_sr_1h_inverted_close'")
   assert writes[-1][1]['timeframe']=='60' and not writes[-1][1]['order_block']['filter_breaks']
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('order_block_sr_1h_inverted_close')
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('Inverted · Bar Close');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','order_block_sr_1h_close')
   expect(page.locator('#strategyTf')).to_have_value('60');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('bullish/Long source opens LONG')
   expect(page.locator('#strategyDescription')).to_contain_text('bearish/Short source opens SHORT')
   expect(page.locator('#strategyDescription')).to_contain_text('NO entry filters')
   expect(page.locator('#strategyDescription')).to_contain_text('CLOSED candles only')
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_enabled')).not_to_be_checked()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='order_block_sr_1h_close'")
   assert writes[-1][1]['timeframe']=='60' and not writes[-1][1]['order_block']['filter_breaks']
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('order_block_sr_1h_close')
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('Order Block + S/R · 1h · Bar Close');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','ob_sr_minute_hourly')
   expect(page.locator('#strategyTf')).to_have_value('1');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#oppositeAction')).to_have_value('close_only');expect(page.locator('#oppositeAction')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('Maximum Positions also caps TOTAL new entries')
   expect(page.locator('#minuteEntryWindow')).to_be_visible();expect(page.locator('#minuteEntryWindow')).to_have_value('5');page.select_option('#minuteEntryWindow','10')
   expect(page.locator('#strategyDescription')).to_contain_text('No higher-timeframe permission')
   expect(page.locator('#strategyDescription')).to_contain_text('NO bar-close wait and NO entry filters')
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#rsiSettings')).not_to_be_visible()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='ob_sr_minute_hourly'")
   assert writes[-1][1]['timeframe']=='1' and writes[-1][1]['opposite_action']=='close_only'
   assert writes[-1][1]['minute_entry_window_minutes']==10
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('ob_sr_minute_hourly');expect(page.locator('#minuteEntryWindow')).to_have_value('10')
   page.evaluate("() => {lv.reason='';lv.hourly_entry_slot={enabled:true,used_count:lv.config.max_positions,limit:lv.config.max_positions,entry_window_open:true};render()}")
   expect(page.locator('#status')).to_contain_text('Hourly entry limit reached')
   expect(page.locator('#status')).to_contain_text('opposite 1m exits remain active')
   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('1m · Once per Hour');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','ob_sr_hour_gate')
   expect(page.locator('#strategyTf')).to_have_value('1');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#oppositeAction')).to_have_value('close_only');expect(page.locator('#oppositeAction')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('NEW 1m Order Block OR S/R')
   expect(page.locator('#strategyDescription')).to_contain_text('closes ONLY, never reverses')
   expect(page.locator('#strategyDescription')).to_contain_text('no entry filters')
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#sma_enabled')).not_to_be_checked();expect(page.locator('#sma_7_50_filter_enabled')).not_to_be_checked()
   expect(page.locator('#rsiSettings')).not_to_be_visible();expect(page.locator('#orderBlockSettings')).not_to_be_visible()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='ob_sr_hour_gate'")
   assert writes[-1][1]['timeframe']=='1' and writes[-1][1]['opposite_action']=='close_only'
   assert not writes[-1][1]['order_block']['filter_breaks']
   assert {key:writes[-1][1][key] for key in protected_fields}==before_ob_risk
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('ob_sr_hour_gate')
   page.evaluate("() => {lv.entry_gates={ETHUSDT:{phase:'armed',losses:0,primary_timeframe:'60'}};renderRiskOverview()}")
   expect(page.locator('#entryGateList')).to_contain_text('1m Order Block / S/R may enter Long or Short')
   page.evaluate("() => {lv.entry_gates={ETHUSDT:{phase:'consumed',losses:1,primary_timeframe:'60'}};renderRiskOverview()}")
   expect(page.locator('#entryGateList')).to_contain_text('Permission USED')
   expect(page.locator('#entryGateList')).to_contain_text('NEW CLOSED 1h')
   assert page.evaluate("signalAlertSources({entry_sources:['htf:60:ob:bullish_formed']})")=='1-hour Bullish Order Block formed'
   assert page.evaluate("signalAlertSources({entry_sources:['htf:1:sr:short_resistance_major']})")=='1-minute S/R short-term major resistance'
   assert page.evaluate("signalAlertSources({entry_sources:['htf:240:ob:bearish_formed']})")=='4-hour Bearish Order Block formed'
   assert page.evaluate("signalAlertSources({entry_sources:['htf:D:ob:bearish_formed']})")=='Daily Bearish Order Block formed'
   expect(page.locator('#strategyDescription')).to_contain_text('One entry CONSUMES its hourly permission')
   expect(page.locator('#strategyDescription')).to_contain_text('CLOSED 1m candle')
   expect(page.locator('#strategyDescription')).to_contain_text('uploaded Pine Bounce/Rejection rules')
   expect(page.locator('#strategyDescription')).to_contain_text('previous candle close')
   expect(page.locator('#strategyDescription')).to_contain_text('Hourly S/R breakouts and all Order Block rules stay unchanged')
   assert page.evaluate("signalAlertSources({entry_sources:['htf:1:sr_bounce:long_support_major']})")=='1-minute S/R Bounce · long-term major support'
   assert page.evaluate("signalAlertSources({entry_sources:['htf:1:sr_rejection:short_resistance_minor']})")=='1-minute S/R Rejection · short-term minor resistance'


   page.click('#start');expect(page.locator('#activationSymbols')).to_contain_text('1h Close → 1m Entry / Exit');page.keyboard.press('Escape')
   page.select_option('#strategyLogic','sr_order_block_gate')
   expect(page.locator('#strategyTf')).to_have_value('5');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#sma_length')).to_have_value('200');expect(page.locator('#sma_length')).to_be_disabled()
   expect(page.locator('#ob_sma200_required')).to_be_checked();expect(page.locator('#ob_sma200_required')).to_be_disabled()
   expect(page.locator('#orderBlockSettings')).to_be_visible();expect(page.locator('#rsiSettings')).not_to_be_visible()
   expect(page.locator('#volumeDeltaSettings')).not_to_be_visible()
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#strategyDescription')).to_contain_text('NO RSI permission')
   expect(page.locator('#strategyDescription')).to_contain_text('Supply breakout Long above')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_order_block_gate'")
   assert writes[-1][1]['timeframe']=='5' and writes[-1][1]['order_block']['filter_breaks']
   assert not writes[-1][1]['sma_enabled'] and not writes[-1][1]['sma_7_50_filter_enabled']
   page.reload();expect(page.locator('#strategyLogic')).to_have_value('sr_order_block_gate');expect(page.locator('#rsiSettings')).not_to_be_visible()
   page.evaluate("() => {lv.entry_gates={ETHUSDT:{phase:'armed',losses:0,primary_timeframe:'240'}};renderRiskOverview()}")
   expect(page.locator('#entryGateList')).to_contain_text('5m Order Block may enter Long or Short')
   page.select_option('#strategyLogic','sma_7_50_intrabar')
   expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#strategyTf')).to_have_value('240')
   expect(page.locator('#oppositeAction')).to_be_disabled()
   expect(page.locator('#oppositeAction')).to_have_value('reverse')
   expect(page.locator('#sma_ob_exit_enabled')).to_be_visible()
   expect(page.locator('#sma_ob_exit_enabled')).to_be_checked()
   expect(page.locator('#strategyDescription')).to_contain_text('Same-direction blocks and ALL block breaks do nothing')
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked()
   expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#rsiSettings')).to_be_hidden()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sma_7_50_intrabar'")
   assert writes[-1][1]['timeframe']=='240' and writes[-1][1]['opposite_action']=='reverse'
   assert not writes[-1][1]['sma_exit_enabled'] and writes[-1][1]['sma_ob_exit_enabled']
   page.uncheck('#sma_ob_exit_enabled');page.click('#saveSymbols')
   page.wait_for_function("() => !lv.config.sma_ob_exit_enabled")
   page.check('#sma_ob_exit_enabled');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.sma_ob_exit_enabled")
   expect(page.locator('#weekly_equity_trailing_enabled')).not_to_be_checked()
   expect(page.locator('#daily_equity_trailing_enabled')).not_to_be_checked()
   expect(page.locator('#three_day_equity_trailing_enabled')).not_to_be_checked()
   page.check('#weekly_equity_trailing_enabled');page.fill('#weekly_equity_trailing_drop_dollars','2.50')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.weekly_equity_trailing_enabled")
   assert writes[-1][1]['weekly_equity_trailing_drop_dollars']==2.5
   period_now=time.time()
   payloads['live']['equity_trailing']={'weekly':dict(period_start=period_now-600,period_end=period_now+3600,peak='105',current='104.5',drop='2.5',remaining='2.0',stop_equity='102.5',triggered=False)}
   page.evaluate("async()=>{updateAccount(await api('live'));renderRiskOverview()}")
   expect(page.locator('[data-equity-period=weekly]')).to_contain_text('TRACKING')
   expect(page.locator('[data-equity-period=weekly]')).to_contain_text('$105.00')
   expect(page.locator('[data-equity-period=weekly]')).to_contain_text('$102.50')
   expect(page.locator('[data-equity-period=weekly]')).to_contain_text('remaining')
   payloads['live']['equity_trailing']['weekly']['triggered']=True
   page.evaluate("async()=>{updateAccount(await api('live'));renderRiskOverview()}")
   expect(page.locator('[data-equity-period=weekly]')).to_contain_text('PAUSED UNTIL PERIOD END')
   page.uncheck('#weekly_equity_trailing_enabled');page.click('#saveSymbols')
   page.wait_for_function("() => !lv.config.weekly_equity_trailing_enabled")
   expect(page.locator('[data-equity-period=weekly]')).to_contain_text('PAUSED UNTIL PERIOD END')
   payloads['live']['equity_trailing']={}
   page.evaluate("async()=>{updateAccount(await api('live'));renderRiskOverview()}")
   expect(page.locator('#equityTrailingStatus')).to_be_hidden()
   page.select_option('#strategyLogic','sr_volume_intrabar')
   expect(page.locator('#strategyDescription')).to_contain_text('REQUIRED')
   expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#sma_exit_enabled')).not_to_be_checked()
   expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   expect(page.locator('#sma_enabled')).not_to_be_checked()
   expect(page.locator('#sma_enabled')).to_be_disabled()
   expect(page.locator('#sma_7_50_filter_enabled')).not_to_be_checked()
   expect(page.locator('#rsiSettings')).to_be_hidden()
   expect(page.locator('#sr_volume_period')).to_have_value('20')
   expect(page.locator('#oppositeAction')).to_be_enabled()
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_volume_intrabar'")
   assert writes[-1][1]['sr_volume_period']==20 and not writes[-1][1]['sma_exit_enabled']
   assert not writes[-1][1]['sma_enabled'] and not writes[-1][1]['sma_7_50_filter_enabled']
   page.select_option('#strategyLogic','sr_inverse_intrabar')
   expect(page.locator('#strategyTf')).to_have_value('240')
   expect(page.locator('#strategyDescription')).to_contain_text('Bullish resistance breakout → SHORT')
   expect(page.locator('#strategyDescription')).to_contain_text('actual SHORT exits when SMA 7 crosses BELOW SMA 50')
   expect(page.locator('#strategyDescription')).to_contain_text('actual LONG exits when SMA 7 crosses ABOVE SMA 50')
   expect(page.locator('#inverse_direction_confirmed')).to_be_visible()
   page.check('#inverse_direction_confirmed')
   expect(page.locator('#inverse_entry_delay_seconds')).to_be_visible()
   page.fill('#inverse_entry_delay_seconds','60')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_inverse_intrabar'")
   assert writes[-1][1]['inverse_entry_delay_seconds']==60
   expect(page.locator('#sma_exit_enabled')).to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   page.select_option('#strategyTf','D');page.click('#saveSymbols');page.wait_for_function("() => lv.config.timeframe==='D'")
   page.select_option('#strategyLogic','sr_rsi_daily')
   expect(page.locator('#strategyTf')).to_have_value('D');expect(page.locator('#strategyTf')).to_be_disabled()
   expect(page.locator('#sma_exit_enabled')).to_be_checked();expect(page.locator('#sma_exit_enabled')).to_be_disabled()
   page.locator('#rsiSettings').evaluate('(el)=>el.open=true')
   for filter_id in ['sma_enabled','sma_7_50_filter_enabled','rsi_volume_filter','rsi_volume_for_static','rsi_vwap_filter','rsi_sma_filter','rsi_histogram_filter']:
    expect(page.locator('#'+filter_id)).not_to_be_checked();expect(page.locator('#'+filter_id)).to_be_disabled()
   expect(page.locator('#rsi_calculation_mode')).to_have_value('fixed');expect(page.locator('#rsi_calculation_mode')).to_be_disabled()
   expect(page.locator('#rsi_signal_rules')).to_have_value('eight_crossings')
   page.click('#saveSymbols');page.wait_for_function("() => lv.config.strategy==='sr_rsi_daily' && lv.config.timeframe==='D'")
   assert writes[-1][1]['rsi']['calculation_mode']=='fixed' and writes[-1][1]['rsi']['signal_rules']=='eight_crossings'
   expect(page.locator('#strategyDescription')).to_contain_text('Daily S/R breakout OR fixed RSI')
   page.select_option('#strategyLogic','rsi_hourly_intrabar')
   expect(page.locator('#strategyDescription')).to_contain_text('Selected RSI calculation version')
   expect(page.locator('#strategyTf')).to_be_enabled();page.select_option('#strategyTf','60')
   page.locator('#rsiSettings').evaluate('(el)=>el.open=true')
   expect(page.locator('#rsi_ml_signals')).to_be_checked();expect(page.locator('#rsi_static_signals')).to_be_checked()
   expect(page.locator('#rsi_calculation_mode')).to_have_value('fixed')
   expect(page.locator('#rsi_fixed_long')).to_have_value('56.68');expect(page.locator('#rsi_fixed_short')).to_have_value('42.38')
   page.fill('#rsi_fixed_long','58');page.fill('#rsi_fixed_short','40');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.rsi.calculation_mode==='fixed' && lv.config.rsi.fixed_long===58 && lv.config.rsi.fixed_short===40")
   page.fill('#rsi_fixed_long','56.68');page.fill('#rsi_fixed_short','42.38');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.rsi.fixed_long===56.68 && lv.config.rsi.fixed_short===42.38")
   for interval in ['1','5','60','240','D']:
    page.select_option('#strategyTf',interval);page.click('#saveSymbols')
    page.wait_for_function('(value) => lv.config.timeframe===value',arg=interval)
    assert writes[-1][1]['rsi']['calculation_mode']=='fixed' and writes[-1][1]['sma_exit_enabled']
   page.select_option('#strategyTf','60')
   page.select_option('#rsi_calculation_mode','corrected');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.rsi.calculation_mode==='corrected'")
   for filter_id in ['rsi_volume_filter','rsi_volume_for_static','rsi_vwap_filter','rsi_sma_filter','rsi_histogram_filter']:expect(page.locator('#'+filter_id)).not_to_be_checked()
   page.select_option('#rsi_signal_rules','eight_crossings')
   page.select_option('#rsi_calculation_mode','uploaded_pine')
   page.fill('#rsi_max_data','50');page.uncheck('#rsi_volume_filter');page.click('#saveSymbols')
   page.wait_for_function("() => lv.config.strategy==='rsi_hourly_intrabar' && lv.config.rsi.max_data===50")
   assert not writes[-1][1]['rsi']['volume_filter']
   assert writes[-1][1]['rsi']['calculation_mode']=='uploaded_pine'
   assert writes[-1][1]['rsi']['signal_rules']=='eight_crossings'
   expect(page.locator('#rsi_calculation_mode')).to_have_value('uploaded_pine')
   page.select_option('#strategyLogic','sma_cross')
   expect(page.locator('#strategyTf')).to_be_enabled();expect(page.locator('#sma_exit_enabled')).to_be_enabled()
   page.select_option('#strategyTf','1');page.click('#saveSymbols')
   expect(page.locator('#take_profit_enabled')).not_to_be_checked()
   page.check('#take_profit_enabled');page.fill('#take_profit_dollars','1.25');page.click('#saveSymbols')
   page.wait_for_function('() => lv.config.take_profit_enabled && lv.config.take_profit_dollars===1.25')
   assert writes[-1][1]['take_profit_enabled'] and writes[-1][1]['take_profit_dollars']==1.25
   page.uncheck('#take_profit_enabled');page.click('#saveSymbols')
   expect(page.locator('#lossStreakScope')).to_have_value('per_coin')
   expect(page.locator('#pnl_trailing_enabled')).not_to_be_checked()
   page.check('#pnl_trailing_enabled');page.fill('#pnl_trailing_activation_dollars','1');page.fill('#pnl_trailing_drop_dollars','0.10');page.click('#saveSymbols')
   page.wait_for_function('() => lv.config.pnl_trailing_enabled && lv.config.pnl_trailing_drop_dollars===0.1')
   assert writes[-1][1]['pnl_trailing_activation_dollars']==1
   payloads['live']['pnl_trailing']=dict(active=True,peak='1.50',threshold='1.40',current='1.45',triggered=False)
   page.evaluate('(data) => {lv.pnl_trailing=data;render();}',payloads['live']['pnl_trailing'])
   expect(page.locator('#pnlTrailingStatus')).to_have_attribute('data-phase','active')
   expect(page.locator('#pnlTrailingStatus')).to_contain_text('TRAILING ACTIVE')
   assert page.locator('#pnlTrailingStatus').evaluate("el=>[el,el.querySelector('strong'),el.querySelector('small')].every(node=>getComputedStyle(node).color===getComputedStyle(document.querySelector('#pnl')).color)&&getComputedStyle(el).borderTopColor===getComputedStyle(document.querySelector('#pnl')).color&&getComputedStyle(el).backgroundColor===getComputedStyle(document.querySelector('#pnl').closest('.card')).backgroundColor&&getComputedStyle(el.querySelector('.pnl-trailing-values strong')).fontWeight===getComputedStyle(document.querySelector('#pnl')).fontWeight")
   expect(page.locator('#pnlTrailingStatus')).to_contain_text('Peak $1.50')
   expect(page.locator('#pnlTrailingStatus')).to_contain_text('Close all at or below $1.40')
   mobile_stats=page.locator('.account-stats').evaluate("el=>{const rect=q=>{const r=el.querySelector(q).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width}};return {equity:rect('.account-equity'),loss:rect('.account-loss'),pnl:rect('.account-pnl'),peak:rect('.pnl-trailing-values>div:first-child'),threshold:rect('.pnl-trailing-values>div:last-child')}}")
   assert abs(mobile_stats['equity']['y']-mobile_stats['loss']['y'])<1
   assert mobile_stats['loss']['x']>mobile_stats['equity']['x']
   assert mobile_stats['pnl']['y']>mobile_stats['equity']['y']
   assert mobile_stats['pnl']['width']>mobile_stats['equity']['width']*1.9
   assert abs(mobile_stats['peak']['y']-mobile_stats['threshold']['y'])<1
   assert page.evaluate('() => document.documentElement.scrollWidth<=innerWidth')

   page.uncheck('#pnl_trailing_enabled');page.click('#saveSymbols')
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
   page.wait_for_function('() => !chartLoading');page.click('#loadOlderHistory');expect(page.locator('#historyStatus')).to_contain_text('2000 candles loaded',timeout=10000)
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
   page.click('[data-page-link=comparison]');expect(page.locator('#page-comparison')).to_be_visible()
   expect(page.locator('#comparisonCycles')).to_contain_text('11 positions')
   expect(page.locator('#comparisonCycles')).to_contain_text('Simulated gross result')
   expect(page.locator('#comparisonCycles')).to_contain_text('Actual confirmed realized P&L')
   expect(page.locator('#comparisonCycles')).to_contain_text('$1.25')
   comparison_live_writes=len(writes)
   page.fill('#observationMaximum','0.50');page.locator('#comparisonSettings button').click()
   expect(page.locator('#comparisonStatus')).to_contain_text('Dynamic observation enabled')
   assert comparison_writes[-1]['maximum']==.5
   assert len(writes)==comparison_live_writes
   assert page.evaluate('() => document.documentElement.scrollWidth<=innerWidth')
   page.reload();expect(page.locator('#page-comparison')).to_be_visible()
   page.click('[data-page-link=history]');expect(page.locator('#historyRows')).to_contain_text('ETHUSDT');expect(page.locator('#historyRows')).to_contain_text('SMA 7/200')
   expect(page.locator('#historyRows')).to_contain_text('S/R Breakout')
   expect(page.locator('#historyRows tr').first.locator('time').first).to_have_attribute('datetime','2026-10-18T05:05:00.123Z')
   expect(page.locator('#historyRows tr').first.locator('time').first).to_contain_text('EDT')
   expect(page.locator('#historyRows .reason-cell').filter(has_text='SMA 7/50 (EXIT)')).to_have_count(2)
   expect(page.locator('#historyRows .reason-cell').filter(has_text='Combined Trailing Stop')).to_have_count(1)
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
   page.click('[data-page-link=alerts]');expect(page.locator('#signalAlertStatus')).to_contain_text('No saved signal alerts')
   page.evaluate('floatingAlertNewest=0')
   alert_items.append(dict(id=1,time=1792300000,observed_at=1792300000,symbol='JCTUSDT',strategy='sr_rsi_daily',signal='Long',reason='queued: Long; awaiting entry checks',diagnostic={'rsi_events':['ml_long_up'],'sr_levels':[]}))
   page.evaluate('pollFloatingSignalAlerts()');expect(page.locator('.signal-toast')).to_have_count(1)
   expect(page.locator('.signal-toast')).to_contain_text('JCTUSDT · Bullish / Long')
   page.evaluate('pollFloatingSignalAlerts()');expect(page.locator('.signal-toast')).to_have_count(1)
   alert_items[0]['reason']='Position filled; Bybit stop loss verified'
   page.evaluate('pollFloatingSignalAlerts()');expect(page.locator('.signal-toast')).to_contain_text('stop loss verified')
   expect(page.locator('.signal-toast')).to_have_count(1)
   page.click('#refreshSignalAlerts');expect(page.locator('#signalAlertRows tr')).to_have_count(1)
   expect(page.locator('#signalAlertRows')).to_contain_text('RSI over Long')
   page.fill('#signalAlertSymbol','ETHUSDT');expect(page.locator('#signalAlertRows tr')).to_have_count(0)
   page.fill('#signalAlertSymbol','');page.click('#refreshSignalAlerts');expect(page.locator('#signalAlertRows tr')).to_have_count(1)
   page.get_by_role('button',name='Dismiss signal alert').click();expect(page.locator('.signal-toast')).to_have_count(0)
   alert_items.append(dict(id=2,time=1792300001,observed_at=1792300001,symbol='ETHUSDT',strategy='sr_volume_intrabar',signal='Short',reason='queued: Short',entry_sources=['htf:240:sr:long_support_major']))
   alert_items.append(dict(id=3,time=1792300002,observed_at=1792300002,symbol='BTCUSDT',strategy='sr_volume_intrabar',signal='Long',reason='queued: Long',entry_sources=['htf:D:sr:long_resistance_major']))
   page.evaluate('pollFloatingSignalAlerts()');expect(page.locator('.signal-toast')).to_have_count(2)
   expect(page.locator('#closeAllSignalAlerts')).to_be_visible()
   page.click('#closeAllSignalAlerts');expect(page.locator('.signal-toast')).to_have_count(0)
   expect(page.locator('#closeAllSignalAlerts')).to_be_hidden()
   page.evaluate('pollFloatingSignalAlerts()');expect(page.locator('.signal-toast')).to_have_count(0)
   page.click('#refreshSignalAlerts');expect(page.locator('#signalAlertRows tr')).to_have_count(3)
   alert_items.clear()
   page.click('[data-page-link=history]')
   expect(page.get_by_role('heading',name='THOTH Trading',exact=True)).to_have_count(0)
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
   expect(page.locator('#historyRows')).to_contain_text('S/R short-term minor support')
   expect(page.locator('#historyRows')).to_contain_text('Source not recorded')

   page.click('[data-page-link=analysis]');expect(page.locator('#analysisStatus')).to_contain_text('3 confirmed')
   entry_delay_items.append(dict(symbol='WLDUSDT',side='Buy',phase='closed',sources=['ob:bullish_formed'],details={'qualified_at':1793143000},timing={},measurements={'observation_gap_seconds':192.6,'volume_wait_seconds':120,'qualified_to_submit_seconds':5,'submit_to_fill_observed_seconds':7,'entry_move_pct':.2},realized_pnl=-.4))
   expect(page.locator('#watchlistRows')).to_contain_text('WLDUSDT');expect(page.locator('#watchlistRows')).to_contain_text('192.6s')
   page.evaluate("() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.watchlistCopied=text}})")
   page.click('#watchlistCopy');expect(page.locator('#watchlistStatus')).to_contain_text('copied')
   assert '110126' in page.evaluate('window.watchlistCopied')
   page.select_option('#watchlistScope','all');expect(page.locator('#watchlistStatus')).to_contain_text('All retained strategies')
   page.click('#refreshEntryDelay');expect(page.locator('#entryDelayRows')).to_contain_text('WLDUSDT / Long')
   expect(page.locator('#entryDelayRows')).to_contain_text('192.60s');expect(page.locator('#entryDelayRows')).to_contain_text('120.00s')
   page.evaluate("() => Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.entryDelayCopied=text}})")
   page.click('#copyEntryDelay');expect(page.locator('#entryDelayStatus')).to_contain_text('Timing report copied')
   assert 'WLDUSDT' in page.evaluate('window.entryDelayCopied')
   page.click('#refreshFastMovers');expect(page.locator('#fastMoversRows')).to_contain_text('WLDUSDT')
   expect(page.locator('#fastMoversRows')).to_contain_text('0.400')
   entry_delay_items.clear()
   expect(page.locator('#sourceAnalysis')).to_contain_text('S/R short-term minor support')
   expect(page.locator('#sourceTimingAnalysis')).to_contain_text('S/R short-term minor support')
   page.select_option('#analysisSource','sr:short_support_minor');expect(page.locator('#analysisStatus')).to_contain_text('1 confirmed')
   expect(page.locator('#sourceTimingAnalysis')).to_contain_text('limited sample')
   page.select_option('#analysisSource','');expect(page.locator('#analysisStatus')).to_contain_text('3 confirmed')
   expect(page.locator('#coinSourceAnalysis')).to_contain_text('BTCUSDT')
   timing_writes=len(writes)
   expect(page.locator('#timingCoverage')).to_contain_text('Opening time')
   expect(page.locator('#timingCoverage')).to_contain_text('2 trades included · 1 excluded')
   expect(page.locator('#timingHighlights article')).to_have_count(6)
   expect(page.locator('#timingHighlights')).to_contain_text('09:00–09:59')
   expect(page.locator('#timingHighlights')).to_contain_text('10:00–10:59')
   expect(page.locator('#timingHours tr')).to_have_count(24)
   expect(page.locator('#timingWeekdays tr')).to_have_count(7)
   expect(page.locator('#timingHighlights')).to_contain_text('small sample')
   page.select_option('#analysisTimeBasis','exit')
   expect(page.locator('#timingCoverage')).to_contain_text('Closing time')
   expect(page.locator('#timingCoverage')).to_contain_text('3 trades included · 0 excluded')
   expect(page.locator('#timingHighlights')).to_contain_text('11:00–11:59')
   page.select_option('#analysisTimeBasis','entry');expect(page.locator('#timingCoverage')).to_contain_text('Opening time')
   analysis_many.extend(dict(symbol='ETHUSDT',strategy='sr_breakout',pnl=.01,opened_at=1792501200-i*86400,closed_at=1792501200-i*86400+60) for i in range(123))
   page.click('#refreshAnalysis');expect(page.locator('#analysisStatus')).to_contain_text('123 confirmed')
   expect(page.locator('#timingDates tr')).to_have_count(50)
   page.click('#timingDatesNext');expect(page.locator('#timingDatesPage')).to_contain_text('51–100 of 123')
   page.click('#timingDatesNext');expect(page.locator('#timingDates tr')).to_have_count(23)
   expect(page.locator('#timingDatesPage')).to_contain_text('101–123 of 123')
   analysis_many.clear();page.click('#refreshAnalysis');expect(page.locator('#analysisStatus')).to_contain_text('3 confirmed')
   assert len(writes)==timing_writes
   assert page.evaluate('() => document.documentElement.scrollWidth<=innerWidth')

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
   page.reload();expect(page.locator('#savedSymbolList')).to_contain_text('ETHUSDT')
   assert page.evaluate('() => chartStyle.bull')=='#26a69a'
   assert page.evaluate('() => chartStyle.long_major.width')==4
   assert not errors,errors
   assert not activation,activation
   browser.close()
  print('PASS: fixed-dollar daily budget display, per-coin cooldown countdown, 30 scrollable activities, live confirmation validation, separate Chart/Positions/History/Settings pages, bookmark reload, SMA strategy and close-only settings saves, retained trade display, desktop/mobile watchlist, separate saved/draft/chart symbols, saves, active-entry lock, mouse dragging in time/price, wheel zoom/reset, right price-axis zoom, visible High/Low, synchronized current price/candle, fixed price-label geometry, second-by-second candle countdown, paginated history, one-shot mouse/touch price/time ruler, yellow current price, right spacing, volume MA20 and white SMA7, minute/day ticks, save-to-chart synchronization, maximization, chart search, S/R ray styles, appearance persistence/reset, live-only controls, no JS errors/overflow, no activation requests')
 finally:
  server.terminate();server.wait(timeout=15);log.close()
