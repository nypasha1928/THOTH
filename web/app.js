const $=id=>document.getElementById(id);
let state,lv,watchlist=[],chartRows=[],smaRows=[],analysis=[],polling=false,visibleBars=70,chartOffset=0;
const accountReadings={};
let riskClockOffset=0;
let currentQuote=null,quotePending=null,marketClockOffset=0;
const marketNow=()=>Date.now()+marketClockOffset;
const chartIntervalSeconds=interval=>({'1':60,'5':300,'60':3600,'240':14400,'D':86400})[interval];
async function refreshQuote(){if(!state||!$('symbol').value)return;const symbol=$('symbol').value,environment=mode,key=environment+':'+symbol;if(quotePending===key)return;quotePending=key;const clockStarted=Date.now();try{const data=await api('quote/'+symbol+'?environment='+environment);if(mode!==environment||$('symbol').value!==symbol)return;if(Number.isFinite(data.server_time)){const exchangeOffset=Number.isFinite(data.exchange_clock_offset)?data.exchange_clock_offset:0;marketClockOffset=(data.server_time+exchangeOffset)*1000-(clockStarted+Date.now())/2;}currentQuote=data;$('quoteStatus').textContent='Current price: '+marketPrice(data.price)+' USDT · Last trade quote at '+new Date(data.observed_at*1000).toLocaleTimeString()+' · refreshes every 5 seconds';draw();const last=chartRows.at(-1);if(last&&!chartLoading&&Date.now()/1000>=last.time+chartIntervalSeconds($('tf').value))loadChart(true)}catch{if(mode===environment&&$('symbol').value===symbol){currentQuote=null;$('quoteStatus').textContent='Current price unavailable · historical candles remain visible';draw()}}finally{if(quotePending===key)quotePending=null}}
let chartLookback=1000,chartContext=null,historyExhausted=false,historyError='';
const mode='live';
let dirty=false,formDraft=null,chartRequest=0,chartLoading=false;
const marketPrice=n=>Number(n).toLocaleString('en-US',{maximumFractionDigits:Math.abs(Number(n))<1?Math.min(12,Math.max(6,2-Math.floor(Math.log10(Math.abs(Number(n))||1)))):Math.abs(Number(n))<10?4:2});
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(n||0));
const fieldIds=['sma_7_50_filter_enabled','sma_exit_enabled','trailing_enabled','trailing_activation_pct','trailing_distance_pct','position_value','leverage','stop_dollars','daily_loss_pct','max_positions','consecutive_losses','pause_minutes','pivot_long','pivot_short','sma_length','major','minor','sma_enabled','portfolio_loss_enabled','portfolio_loss_pct','daily_resume_automatic'];
async function api(path,method='GET',body){
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),45000);
 try{const response=await fetch('/api/'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:controller.signal});
 if(response.status===401){location.href='/login';throw Error('Sign in required')}
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
 }finally{clearTimeout(timeout)}
}
function message(text){$('message').textContent=text}
function selectedAccount(){return lv}
function updateAccount(data){state=lv=data;if(Number.isFinite(data.server_time))riskClockOffset=data.server_time*1000-Date.now()}
function current(){
 const tn=selectedAccount();
 return {closing:tn.closing,settings:tn.config,equity:tn.equity,status:tn.status,reason:tn.reason,
 daily_loss_pct:tn.risk.loss_pct,streak:tn.risk.streak,
 positions:tn.positions.map(p=>({...p,entry:Number(p.entry),qty:Number(p.qty),mark:Number(p.mark),value:Number(p.qty)*Number(p.entry),leverage:tn.config.leverage,stop_dollars:Number(p.stop_dollars??tn.config.stop_dollars)})),
 trades:tn.closures.map(t=>({...t,closed_at:t.closed_at})),events:tn.events.map(e=>({...e,time:e.time*1000}))};
}
function field(label,id,value,type='number',attributes=''){
 const wrapper=document.createElement('label');wrapper.htmlFor=id;wrapper.append(document.createTextNode(label));
 if(id==='sma_exit_enabled')wrapper.classList.add('exit-setting');
 const input=document.createElement('input');input.id=id;input.type=type;
 if(type==='checkbox')input.checked=Boolean(value);else{input.value=value;input.required=true;input.min='1';input.step='1'}
 if(attributes){for(const [key,value]of Object.entries(attributes))input.setAttribute(key,value)}
 wrapper.append(input);return wrapper;
}
function renderSettings(){
 const tn=selectedAccount();const c=current().settings;$('fields').replaceChildren();
 for(const [id,label,attrs]of [
 ['position_value','Position value (USDT)',{min:'.01',step:'.01'}],['leverage','Leverage (×)',{max:'100'}],
 ['stop_dollars','Stop per position (USDT)',{min:'.01',step:'.01'}],['trailing_activation_pct','Trailing activation (%)',{min:'0',max:'10000',step:'.1'}],['trailing_distance_pct','Trailing stop execute (%)',{min:'.01',max:'99.99',step:'.01'}],['daily_loss_pct','Daily account loss (%)',{min:'.1',max:'100',step:'.1'}],
 ['portfolio_loss_pct','Overall position loss (%)',{min:'.1',max:'100',step:'.1'}],['max_positions','Maximum positions',{max:'100'}],['consecutive_losses','Consecutive losses',{max:'100'}],
 ['pause_minutes','Pause duration (minutes)',{max:'10080'}],['pivot_long','Long pivot period',{max:'100'}],['pivot_short','Short pivot period',{max:'100'}],['sma_length','SMA length',{max:'500'}]])
 $('fields').append(field(label,id,c[id],'number',attrs));
 for(const [id,label]of [['sma_exit_enabled','SMA 7/50 intrabar exits (both strategies)'],['trailing_enabled','Enable trailing stop for new positions'],['major','Major levels'],['minor','Minor levels'],['sma_enabled','S/R entry filter: price vs SMA (default 200)'],['sma_7_50_filter_enabled','S/R entry filter: SMA 7/50'],['portfolio_loss_enabled','Enable overall position loss stop'],['daily_resume_automatic','Resume after next UTC day']])$('fields').append(field(label,id,c[id],'checkbox'));
 $('strategyLogic').value=c.strategy;$('oppositeAction').value=c.opposite_action;renderStrategyDescription();
 $('lossStreakScope').value=c.loss_streak_scope;
 $('trailingBasis').value=c.trailing_distance_basis;$('trailingActivationBasis').value=c.trailing_activation_basis;renderTrailingDescription();
 $('strategyTf').value=c.timeframe;$('portfolioBasis').value=c.portfolio_loss_basis;$('marginMode').value=selectedAccount().config.margin_mode;$('dedicated').checked=selectedAccount().config.dedicated_account;
 $('settingsTitle').textContent='LIVE strategy & risk';
 $('settingsGroup').disabled=tn.entries;
 dirty=false;formDraft={...c,symbols:[...c.symbols]};renderWatchlist();
}
function symbolsLocked(){return selectedAccount().entries}
function matchesSearch(x){return x.selectable&&x.original.toUpperCase().includes($('search').value.trim().toUpperCase())}
function setDraftSymbols(symbols){if(symbolsLocked())return;formDraft.symbols=[...new Set(symbols)];dirty=true;renderWatchlist();renderScope()}
function renderScope(){
 const saved=current().settings.symbols;
 $('savedSymbols').textContent=mode.toUpperCase()+' · Saved trading symbols ('+saved.length+'): '+(saved.join(', ')||'none');
 const viewed=$('symbol').value;
 $('chartScope').textContent='Chart: '+viewed+' · '+(saved.includes(viewed)?'Included in saved trading list':'View only — not enabled for trading')+(dirty?' · Unsaved edits do not affect the bot':'');
}
function syncSymbolLock(){
 const locked=symbolsLocked();
 for(const input of $('coins').querySelectorAll('input'))input.disabled=locked;
 for(const b of $('selectedSymbols').querySelectorAll('button'))b.disabled=locked;
 for(const id of ['clearSymbols','saveSymbols'])$(id).disabled=locked;
 $('selectVisible').disabled=locked||!watchlist.some(matchesSearch);
 $('onlyChart').disabled=locked||!watchlist.some(x=>x.selectable&&x.symbol===$('symbol').value);
 $('watchlistLock').textContent=locked?'Pause entries before editing trading symbols. Existing positions remain managed.':'Select symbols, then save. Removing a symbol with an open managed position is blocked by the server.';
}
function renderWatchlist(){
 const locked=symbolsLocked();$('coins').replaceChildren();
 const filtered=watchlist.filter(x=>matchesSearch(x)&&(!$('selectedOnly').checked||formDraft.symbols.includes(x.symbol)));
 for(const x of filtered){
 const label=document.createElement('div'),input=document.createElement('input');input.type='checkbox';input.value=x.symbol;input.setAttribute('aria-label','Allow '+x.symbol+' to trade');
 input.disabled=locked;input.checked=formDraft.symbols.includes(x.symbol);
 input.onchange=()=>setDraftSymbols(input.checked?[...formDraft.symbols,x.symbol]:formDraft.symbols.filter(s=>s!==x.symbol));
 const chartButton=document.createElement('button');chartButton.type='button';chartButton.textContent=x.symbol;chartButton.setAttribute('aria-label','View '+x.symbol+' chart');chartButton.onclick=()=>openChartSymbol(x.symbol);label.append(input,chartButton);label.className=input.checked?'symbol-choice selected':'symbol-choice';$('coins').append(label);
 }
 if(!filtered.length){const p=document.createElement('p');p.textContent='No matching symbols';$('coins').append(p)}
 $('selection').textContent=formDraft.symbols.length+' selected'+(dirty?' · Unsaved changes':' · Saved');
 $('selectedSymbols').replaceChildren();for(const symbol of formDraft.symbols){const b=document.createElement('button');b.textContent=symbol+' ×';b.disabled=locked;b.setAttribute('aria-label','Remove '+symbol+' from trading symbols');b.onclick=()=>setDraftSymbols(formDraft.symbols.filter(s=>s!==symbol));$('selectedSymbols').append(b)}
 syncSymbolLock();
 renderScope();
}

function render(){
 const tn=selectedAccount();const s=current();const reading=accountReadings[mode];const recent=reading?.verified&&reading.balance&&Date.now()/1000-reading.balance.checked_at<300;const equity=s.equity??(recent?reading.balance.equity:null);$('equity').textContent=equity===null?'—':money(equity);$('pnl').textContent=money(s.positions.reduce((sum,p)=>sum+Number(p.pnl),0));
 $('limit').textContent=s.settings.daily_loss_pct;$('count').textContent=s.positions.length+' / '+s.settings.max_positions;
 $('status').textContent=s.closing?'Closing managed positions; waiting for exchange reconciliation':s.status.replaceAll('_',' ')+' · '+(s.reason||'Waiting for confirmed breakouts');
 $('lossProgress').textContent=Number(s.daily_loss_pct).toFixed(2)+'% of starting equity used below the daily peak · '+(s.settings.loss_streak_scope==='per_coin'?'Loss streaks tracked per coin':s.streak+' consecutive account losses');renderRiskOverview();
 $('equityLabel').textContent='LIVE USDT equity'+(s.equity===null&&recent?' · read-only snapshot':'');$('pnlLabel').textContent='Open LIVE P&L';
 $('fundsBadge').textContent='LIVE · REAL FUNDS';$('positionsTitle').textContent='LIVE positions';
 $('start').textContent='Enable LIVE entries';$('close').textContent='Close all LIVE positions';
 $('start').disabled=!tn.enabled_on_server||tn.entries||tn.closing;$('start').title=!tn.enabled_on_server?'Server-side live activation is disabled':'';
 $('fundsBadge').classList.add('red');$('liveAccountState').textContent=lv.management?'Production execution: REAL funds management active or recovering':'Production execution: inactive';
 $('liveModeNote').textContent=tn.enabled_on_server?'Live server enabled. Account checks, risk controls and manual activation apply.':'Live execution is disabled on the server.';
 $('positionNote').textContent='Bot-managed LIVE positions. Dollar loss limits can be exceeded by fees, slippage or outages. Exchange stop verification is reported in Activity.';
 $('positions').replaceChildren();
 for(const p of s.positions){const row=document.createElement('tr');for(const value of [p.symbol,p.side,money(p.value)+' / '+p.leverage+'×',money(p.pnl),p.trailing?.enabled?(p.trailing.active?'Active · stop '+marketPrice(p.stop):'Waiting · activation '+marketPrice(p.trailing.activation_price)):'Off']){const td=document.createElement('td');td.textContent=value;row.append(td)}row.onclick=()=>{location.hash='#chart';showPage();$('symbol').value=p.symbol;renderWatchlist();loadChart()};$('positions').append(row)}
 $('events').replaceChildren();for(const event of [...s.events].reverse().slice(0,30)){const li=document.createElement('li');li.textContent=new Date(event.time).toLocaleTimeString()+' · '+event.message;$('events').append(li)}
 const smaBlocks=Object.keys(tn.sma_exit_blocks||{});$('smaDataStatus').textContent=smaBlocks.length?'SMA exit data: new entries blocked only for '+smaBlocks.join(', ')+'. Other coins remain subject to their own signals and risk checks. Existing Bybit stops stay managed.':'';
 $('execution').replaceChildren();for(const record of tn.execution.filter(r=>!['closed','cancelled','rejected'].includes(r.phase))){const li=document.createElement('li');li.textContent=record.symbol+' · '+record.phase+(record.reason?' · '+record.reason:'');$('execution').append(li)}
 $('baseline').disabled=!(tn.risk.baseline_required&&tn.management);
 $('settingsGroup').disabled=tn.entries;
 renderScope();
 syncSymbolLock();
 draw();
}
function collect(){const cfg={...formDraft,strategy:$('strategyLogic').value,opposite_action:$('oppositeAction').value,timeframe:$('strategyTf').value,portfolio_loss_basis:$('portfolioBasis').value};for(const id of fieldIds){const input=$(id);cfg[id]=input.type==='checkbox'?input.checked:Number(input.value)}cfg.automatic_paper=false;cfg.loss_streak_scope=$('lossStreakScope').value;cfg.trailing_distance_basis=$('trailingBasis').value;cfg.trailing_activation_basis=$('trailingActivationBasis').value;
 cfg.margin_mode=$('marginMode').value;cfg.dedicated_account=$('dedicated').checked;return cfg}
function renderStrategyDescription(){const cross=$('strategyLogic').value==='sma_cross';$('strategyDescription').textContent=cross?'Confirmed candle: SMA 7 crossing above SMA 200 opens Long; crossing below opens Short. S/R levels and its optional entry filters do not affect this strategy.':'Confirmed resistance breakout opens Long; confirmed support break opens Short. Optional filters: price above/below the chosen SMA (default 200), and SMA 7 above/below SMA 50. If both are selected, both must pass; SMA 7/50 alignment does not require a fresh crossover. Exits are configured separately.';for(const id of ['pivot_long','pivot_short','major','minor'])if($(id))$(id).closest('label').hidden=cross;}
function renderTrailingDescription(){const enabled=$('trailing_enabled').checked;for(const id of ['trailing_activation_pct','trailing_distance_pct','trailingBasis','trailingActivationBasis'])$(id).closest('label').hidden=!enabled;$('trailingDescription').textContent=enabled?'Activation uses '+($('trailingActivationBasis').value==='price'?'a favorable mark-price move from entry':'mark-price profit divided by the trade’s original margin')+', excluding fees. Distance uses '+($('trailingBasis').value==='price'?'a percentage of the best observed mark price':'a percentage of the original margin, converted to a fixed price distance')+'. Activation does not guarantee a profitable exit. Applies to new positions. The server samples prices and tightens a verified Bybit stop; during an outage the last confirmed stop stays on Bybit, but does not keep trailing.':'Trailing is off for new positions. Existing positions keep their original trailing settings and dollar stop.';}
$('settings').oninput=()=>{dirty=true;renderStrategyDescription();renderTrailingDescription()};
async function saveSettings(){try{const cfg=collect();updateAccount(await api('live/settings','PUT',cfg));if(cfg.symbols.length===1&&watchlist.some(x=>x.selectable&&x.symbol===cfg.symbols[0]))$('symbol').value=cfg.symbols[0];renderSettings();render();message('Settings saved. Existing positions keep their original size and dollar stop.');$('symbolSaveMessage').textContent='Saved trading symbols: '+current().settings.symbols.join(', ');loadChart()}catch(e){message(e.message);$('symbolSaveMessage').textContent=e.message}}
$('settings').onsubmit=e=>{e.preventDefault();saveSettings()};
$('saveSymbols').onclick=saveSettings;
$('search').oninput=renderWatchlist;
$('selectedOnly').onchange=renderWatchlist;
$('selectVisible').onclick=()=>setDraftSymbols([...formDraft.symbols,...watchlist.filter(matchesSearch).map(x=>x.symbol)]);
$('clearSymbols').onclick=()=>setDraftSymbols([]);
$('onlyChart').onclick=()=>setDraftSymbols([$('symbol').value]);
$('editSymbols').onclick=()=>{location.hash='#settings';showPage();$('symbolPanel').scrollIntoView({behavior:'smooth',block:'start'});$('search').focus({preventScroll:true})};
$('start').onclick=async()=>{try{if(dirty){$('symbolSaveMessage').textContent='Save your strategy and trading symbols before activation.';location.hash='#settings';showPage();$('symbolPanel').scrollIntoView({behavior:'smooth'});return;}$('activationSymbols').textContent='Saved trading symbols: '+current().settings.symbols.join(', ')+' · '+current().settings.timeframe+' timeframe · '+money(current().settings.position_value)+' position value · '+current().settings.leverage+'× leverage · '+(current().settings.strategy==='sma_cross'?'SMA 7/200':'S/R Breakout')+' · '+(current().settings.opposite_action==='close_only'?'Close only on opposite signal':'Reversal');$('livePhrase').value='';$('activationError').textContent=lv.risk.daily_stop?'Daily-stop review: the account must be flat and reconciled, with equity inside the saved allowance. Restart preserves the day’s starting equity and peak. Increasing the daily percentage increases permitted risk.':'';$('liveDialog').showModal()}catch(e){message(e.message)}};
$('pause').onclick=async()=>{try{updateAccount(await api('live/pause','POST'));render()}catch(e){message(e.message)}};
$('close').onclick=async()=>{if(!confirm('Close all LIVE REAL-FUNDS managed positions and block new entries?'))return;try{updateAccount(await api('live/close-all','POST'));render()}catch(e){message(e.message)}};
$('activateLive').onclick=async()=>{try{const phrase=$('livePhrase').value.trim();if(phrase!=='ENABLE LIVE ORDERS'){$('activationError').textContent='Type ENABLE LIVE ORDERS exactly to enable LIVE trading.';$('livePhrase').focus();return;}updateAccount(await api('live/activate','POST',{confirmation:phrase}));$('liveDialog').close();$('livePhrase').value='';render();showReadiness();message('Account checks completed. Watch live account and execution status.')}catch(e){$('activationError').textContent=e.message}};
$('cancelActivation').onclick=()=>$('liveDialog').close();
$('baseline').onclick=async()=>{if(!confirm('Use current '+mode.toUpperCase()+' equity as the new UTC day baseline? The account must be flat. A current-day loss lock cannot be reset.'))return;try{updateAccount(await api(mode+'/baseline','POST'));render()}catch(e){message(e.message)}};
async function loadChart(background=false){const context=mode+':'+$('symbol').value+':'+$('tf').value;if(context!==chartContext){chartContext=context;chartLookback=1000;historyExhausted=false;historyError='';chartMeasurement=null;rulerPointer=null;chartTouches.clear();chartPinch=null;chartDrag=null;}if(!background){currentQuote=null;chartLoading=true;chartRows=[];smaRows=[];analysis=[];draw();refreshQuote();}if(background&&(chartLookback>1000||chartLoading||chartDrag||chartPinch||chartTouches.size||rulerPointer||chartOffset>0||chartPriceShift!==0||chartPriceZoom!==1))return;chartLoading=true;const request=++chartRequest;$('chartStatus').textContent='Loading '+'LIVE'+' candles…';try{const data=await api('chart/'+$('symbol').value+'?interval='+$('tf').value+'&environment='+mode+'&lookback='+chartLookback);if(request!==chartRequest||(background&&(chartDrag||chartPinch||chartTouches.size||rulerPointer||chartOffset>0||chartPriceShift!==0||chartPriceZoom!==1)))return;if(!background){chartPriceShift=0;chartPriceZoom=1;}chartRows=data.candles;smaRows=data.sma;analysis=data.analysis||[];historyExhausted=Boolean(data.history_exhausted);chartOffset=0;$('chartStatus').textContent=data.source+' · forming candle may update · signals use closed candles';draw()}catch(e){if(request!==chartRequest)return;chartRows=[];smaRows=[];analysis=[];draw();$('chartStatus').textContent=e.message}finally{if(request===chartRequest){chartLoading=false;renderHistoryStatus()}}}
function renderHistoryStatus(){
 const first=chartRows[0],last=chartRows.at(-1);$('historyStatus').textContent=chartRows.length+' candles loaded'+(first?' · '+new Date(first.time*1000).toISOString().slice(0,16).replace('T',' ')+' → '+new Date(last.time*1000).toISOString().slice(0,16).replace('T',' ')+' UTC':'')+(chartLookback>1000?' · Historical replay snapshot; refresh manually. Latest candle returns to the 1,000-candle live view. Early signals may change as warm-up expands':'')+(historyExhausted?' · End of available exchange history':'');
 if(historyError)$('historyStatus').textContent=historyError;
 $('loadOlderHistory').disabled=chartLoading||historyExhausted||chartLookback>=5000||!chartRows.length;
 $('loadOlderHistory').textContent=chartLookback>=5000?'5,000-candle limit reached':chartLoading?'Loading history…':'Load 1,000 older candles';
}
async function loadOlderHistory(){
 if(chartLoading||historyExhausted||chartLookback>=5000||!chartRows.length)return;
 const context=chartContext,requested=Math.min(5000,chartLookback+1000),endTime=chartRows[chartRows.length-chartOffset-1]?.time,request=++chartRequest;
 chartLoading=true;historyError='';renderHistoryStatus();
 try{const data=await api('chart/'+$('symbol').value+'?interval='+$('tf').value+'&environment='+mode+'&lookback='+requested);if(context!==chartContext||request!==chartRequest)return;const anchor=chartRows[chartRows.length-chartOffset-1]?.time??endTime;chartLookback=requested;chartRows=data.candles;smaRows=data.sma;analysis=data.analysis||[];historyExhausted=Boolean(data.history_exhausted)||chartRows.length<requested;const at=chartRows.findIndex(r=>r.time===anchor);chartOffset=at>=0?chartRows.length-at-1:0;draw()}
 catch(e){if(context===chartContext&&request===chartRequest){historyError='Older history unavailable: '+e.message+' · Existing chart preserved';$('historyStatus').textContent=historyError;}}
 finally{if(request===chartRequest){chartLoading=false;$('loadOlderHistory').disabled=historyExhausted||chartLookback>=5000;$('loadOlderHistory').textContent=chartLookback>=5000?'5,000-candle limit reached':'Load 1,000 older candles'}}
}
$('loadOlderHistory').onclick=loadOlderHistory;
$('symbol').onchange=()=>{renderWatchlist();loadChart()};$('tf').onchange=()=>loadChart();
async function connectionStatus(verify=false){try{const environment='live';const status=await api((verify?'connection/verify':'connection')+'?environment='+environment,verify?'POST':'GET');accountReadings[environment]=status;$('accountBalance').textContent=status.balance?'USDT wallet: '+money(status.balance.wallet_balance)+' · USDT equity: '+money(status.balance.equity)+' · Account unrealized P&L: '+money(status.balance.unrealised_pnl)+' · Snapshot '+new Date(status.balance.checked_at*1000).toLocaleTimeString()+' · '+status.balance_reason:status.balance_reason;render();$('accountStatus').textContent=environment.toUpperCase()+' read access: '+(status.verified?'verified':status.configured?'not verified':'not configured')+' · '+status.reason}catch(e){$('accountStatus').textContent='Connection check unavailable: '+e.message}}
$('verifyAccount').onclick=()=>connectionStatus(true);
$('logout').onclick=async()=>{try{await api('logout','POST');location.href='/login'}catch(e){message(e.message)}};
async function init(){try{[lv,watchlist]=await Promise.all([api('live'),api('watchlist')]);updateAccount(lv);for(const item of watchlist.filter(x=>x.selectable)){const option=document.createElement('option');option.value=item.symbol;option.textContent=item.symbol;$('symbol').append(option)}renderSettings();render();loadChart();connectionStatus();showReadiness();showPage();
 setInterval(async()=>{if(polling)return;polling=true;try{updateAccount(await api('live'));const health=lv.feed_health;$('runnerHealth').textContent='Market worker: '+(health?.status||'idle');render()}catch{$('status').textContent='Connection lost · last displayed data may be stale'}finally{polling=false}},5000);
 }catch(e){message(e.message)}}
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
window.addEventListener('load',init);
setInterval(()=>{if(state&&$('autoChart').checked)loadChart(true)},15000);

async function showReadiness(){try{const r=await api('readiness');$('readinessMessage').textContent=(r.live_ready?'Live entries active':'Live entries paused or inactive')+' · '+(r.live_reference.verified?'Strategy reference comparison verified':'Strategy comparison remains unverified')}catch{$('readinessMessage').textContent='Readiness status unavailable'}}
$('checkReadiness').onclick=showReadiness;

setInterval(refreshQuote,5000);

function countdown(until){const seconds=Math.max(0,Math.ceil(until-(Date.now()+riskClockOffset)/1000)),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;return (hours?String(hours).padStart(2,'0')+':':'')+String(minutes).padStart(2,'0')+':'+String(rest).padStart(2,'0')}
function riskTimestamp(ms){return Number.isFinite(ms)?new Date(ms).toISOString().replace('T',' ').replace(/\.\d{3}Z$/,' UTC'):'not recorded on older versions'}
function renderRiskOverview(){
 if(!lv)return;const risk=lv.risk,now=(Date.now()+riskClockOffset)/1000;
 $('dailyCountdown').textContent='Time until next UTC day: '+countdown((Math.floor(now/86400)+1)*86400)+' · resets at 00:00 UTC; restart follows your saved resume setting';
 if(risk.baseline_required){$('dailyBudget').classList.add('budget-exhausted');$('dailyBudget').textContent='New daily allowance pending baseline review — entries blocked';$('dailyBudgetDetails').textContent='Previous risk day: '+risk.day+' UTC. Yesterday’s remaining allowance is not today’s budget. Review the new baseline while flat.';}
 else if(risk.loss_allowance==null){$('dailyBudget').textContent='Waiting for a verified risk baseline…';$('dailyBudgetDetails').textContent='';}
 else{$('dailyBudget').classList.toggle('budget-exhausted',risk.daily_stop||risk.remaining<=0);$('dailyBudget').textContent='Daily loss allowance remaining: '+money(risk.remaining)+' ('+Number(risk.remaining_pct).toFixed(2)+'% of starting equity)';$('dailyBudgetDetails').textContent='Risk day: '+risk.day+' UTC · UTC starting equity: '+money(risk.baseline)+' · Baseline recorded: '+riskTimestamp(risk.baseline_ms)+' · Tracked transfer-adjusted peak: '+money(risk.peak)+' · Peak recorded: '+riskTimestamp(risk.peak_ms)+' · Allowance: '+money(risk.loss_allowance)+' · Used below peak: '+money(risk.loss_used)+' · Current account stop threshold: '+money(risk.stop_equity)+(risk.daily_stop?' · Daily stop latched; explicit review required':'');}
 $('coinCooldowns').replaceChildren();
 if(lv.config.loss_streak_scope==='account'){$('cooldownSummary').textContent=risk.pause_until&&risk.pause_until>now?'Whole-account consecutive-loss cooldown · '+countdown(risk.pause_until)+' remaining':risk.pause_until?'Cooldown elapsed; awaiting server review':'No account loss-streak cooldown';}
 else{$('cooldownSummary').textContent='Per-coin consecutive-loss control';for(const [symbol,tracker] of Object.entries(risk.symbols||{}).sort()){
  if(!tracker.streak&&!tracker.pause_until)continue;const item=document.createElement('li');item.textContent=symbol+' · '+tracker.streak+' consecutive losses'+(tracker.pause_until&&tracker.pause_until>now?' · Paused '+countdown(tracker.pause_until)+' remaining':tracker.pause_until?' · Cooldown elapsed; awaiting server review':'');$('coinCooldowns').append(item);
 }if(!$('coinCooldowns').children.length)$('cooldownSummary').textContent+=' · No paused coins or loss streaks';}
}
setInterval(renderRiskOverview,1000);
