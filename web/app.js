const $=id=>document.getElementById(id);
let state,lv,watchlist=[],chartRows=[],smaRows=[],analysis=[],polling=false,visibleBars=70,chartOffset=0;
const accountReadings={};
let riskClockOffset=0;
let currentQuote=null,quotePending=null,marketClockOffset=0;
const marketNow=()=>Date.now()+marketClockOffset;
const chartIntervalSeconds=interval=>({'1':60,'5':300,'60':3600,'240':14400,'D':86400})[interval];
async function refreshQuote(){if(!state||!$('symbol').value)return;const symbol=$('symbol').value,environment=mode,key=environment+':'+symbol;if(quotePending===key)return;quotePending=key;const clockStarted=Date.now();try{const data=await api('quote/'+symbol+'?environment='+environment);if(mode!==environment||$('symbol').value!==symbol)return;if(Number.isFinite(data.server_time)){const exchangeOffset=Number.isFinite(data.exchange_clock_offset)?data.exchange_clock_offset:0;marketClockOffset=(data.server_time+exchangeOffset)*1000-(clockStarted+Date.now())/2;}currentQuote=data;$('quoteStatus').textContent='Current price: '+marketPrice(data.price)+' USDT · Last trade quote at '+chartTime(data.observed_at,{date:false,showSeconds:true})+' · refreshes every 5 seconds';draw();const last=chartRows.at(-1);if(last&&$('autoChart').checked&&!chartLoading&&Date.now()/1000>=last.time+chartIntervalSeconds($('tf').value))loadChart(true)}catch{if(mode===environment&&$('symbol').value===symbol){currentQuote=null;$('quoteStatus').textContent='Current price unavailable · historical candles remain visible';draw()}}finally{if(quotePending===key)quotePending=null}}
let chartLookback=1000,chartContext=null,historyExhausted=false,historyError='';
const mode='live';
let rsiRows=[],srAnalysis=[],referenceSignalStrategy='sr_breakout';
let dirty=false,formDraft=null,chartRequest=0,chartLoading=false;
const marketPrice=n=>Number(n).toLocaleString('en-US',{maximumFractionDigits:Math.abs(Number(n))<1?Math.min(12,Math.max(6,2-Math.floor(Math.log10(Math.abs(Number(n))||1)))):Math.abs(Number(n))<10?4:2});
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(n||0));
const fieldIds=['coin_pnl_trailing_enabled','coin_pnl_trailing_activation_dollars','coin_pnl_trailing_drop_dollars','pnl_trailing_enabled','pnl_trailing_activation_dollars','pnl_trailing_drop_dollars','take_profit_enabled','take_profit_dollars','sma_7_50_filter_enabled','sma_exit_enabled','trailing_enabled','trailing_activation_pct','trailing_distance_pct','position_value','leverage','stop_dollars','daily_loss_pct','max_positions','consecutive_losses','pause_minutes','pivot_long','pivot_short','sma_length','major','minor','sma_enabled','portfolio_loss_enabled','portfolio_loss_pct','daily_resume_automatic'];
async function api(path,method='GET',body){
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),45000);
 try{const response=await fetch('/api/'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:controller.signal});
 if(response.status===401){location.href='/login';throw Error('Sign in required')}
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
 }finally{clearTimeout(timeout)}
}
const chartNYFormatter=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZoneName:'short'});
function chartTime(seconds,{date=true,time=true,zone=true,showSeconds=false}={}){
 const value=new Date(seconds*1000);if(!Number.isFinite(value.getTime()))return '—';
 const p=Object.fromEntries(chartNYFormatter.formatToParts(value).map(part=>[part.type,part.value]));
 return [date?p.year+'-'+p.month+'-'+p.day:'',time?p.hour+':'+p.minute+(showSeconds?':'+p.second:''):'',zone?p.timeZoneName:''].filter(Boolean).join(' ');
}
function tradeTimestamp(value,milliseconds=false,showDate=true){
 const stamp=Number(value)*(milliseconds?1:1000);
 if(value==null||!Number.isFinite(stamp)||stamp<=0)return '—';
 const date=new Date(stamp);if(!Number.isFinite(date.getTime()))return '—';
 const node=document.createElement('time');node.dateTime=date.toISOString();
 node.textContent=date.toLocaleString('en-US',{timeZone:'America/New_York',...(showDate?{year:'numeric',month:'2-digit',day:'2-digit'}:{}),hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'});
 node.title=date.toISOString();return node;
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
 ['stop_dollars','Stop per position (USDT)',{min:'.01',step:'.01'}],['take_profit_dollars','Take profit per new position (USDT, before fees)',{min:'.01',step:'.01'}],['trailing_activation_pct','Trailing activation (%)',{min:'0',max:'10000',step:'.1'}],['trailing_distance_pct','Trailing stop execute (%)',{min:'.01',max:'99.99',step:'.01'}],['daily_loss_pct','Daily account loss (%)',{min:'.1',max:'100',step:'.1'}],
 ['portfolio_loss_pct','Overall position loss (%)',{min:'.1',max:'100',step:'.1'}],['max_positions','Maximum positions',{max:'100'}],['consecutive_losses','Consecutive losses',{max:'100'}],
 ['pause_minutes','Pause duration (minutes)',{max:'10080'}],['pivot_long','Long pivot period',{max:'100'}],['pivot_short','Short pivot period',{max:'100'}],['sma_length','SMA length',{max:'500'}]])
 $('fields').append(field(label,id,c[id],'number',attrs));
 for(const [id,label]of [['take_profit_enabled','Enable take profit for new positions'],['sma_exit_enabled','SMA 7/50 intrabar exits (all strategies)'],['trailing_enabled','Per-position trailing stop (previous option)'],['major','Major levels'],['minor','Minor levels'],['sma_enabled','S/R entry filter: price vs SMA (default 200)'],['sma_7_50_filter_enabled','S/R entry filter: SMA 7/50'],['portfolio_loss_enabled','Enable overall position loss stop'],['daily_resume_automatic','Resume after next UTC day']])$('fields').append(field(label,id,c[id],'checkbox'));
 const pnlGroup=document.createElement('fieldset'),pnlTitle=document.createElement('legend');pnlTitle.textContent='Combined Open LIVE P&L trailing stop';pnlGroup.append(pnlTitle);
 pnlGroup.append(field('Enable combined Open P&L trailing stop','pnl_trailing_enabled',c.pnl_trailing_enabled,'checkbox'));
 pnlGroup.append(field('Open P&L activation (USDT)','pnl_trailing_activation_dollars',c.pnl_trailing_activation_dollars,'number',{min:'.01',step:'.01'}));
 pnlGroup.append(field('Drop from highest Open P&L (USDT)','pnl_trailing_drop_dollars',c.pnl_trailing_drop_dollars,'number',{min:'.01',step:'.01'}));
 const pnlNote=document.createElement('p');pnlNote.className='note';pnlNote.textContent='Combined unrealized P&L of all managed open positions, including losses. Close ALL after the drop from the highest P&L since activation. Entry permission stays enabled; new orders wait only while close-all settles, then normal signals continue subject to risk checks. Server-monitored; fees/slippage can change the realized result. Applies to current open positions when enabled. Changing these trailing settings restarts an untriggered cycle.';pnlGroup.append(pnlNote);$('fields').prepend(pnlGroup);
 const coinGroup=document.createElement('fieldset'),coinTitle=document.createElement('legend');coinTitle.textContent='Per-coin Open LIVE P&L trailing stop';coinGroup.append(coinTitle);
 coinGroup.append(field('Enable per-coin P&L trailing stop','coin_pnl_trailing_enabled',c.coin_pnl_trailing_enabled,'checkbox'));
 coinGroup.append(field('Trigger P&L per coin (USDT)','coin_pnl_trailing_activation_dollars',c.coin_pnl_trailing_activation_dollars,'number',{min:'.001',step:'.001'}));
 coinGroup.append(field('Trailing drop per coin (USDT)','coin_pnl_trailing_drop_dollars',c.coin_pnl_trailing_drop_dollars,'number',{min:'.001',step:'.001'}));
 const coinNote=document.createElement('p');coinNote.className='note';coinNote.textContent='Same fixed-dollar P&L rules as combined trailing, tracked independently for each coin. Trigger is profit in dollars; trailing drop is a fixed dollar drop from the highest profit since activation. Closes only that position and retains entry permission. These amounts apply to every managed position, including current ones when enabled. Combined trailing remains separate; whichever exit triggers first applies. Changing these settings resets untriggered per-coin peaks. Server sampled; realized profit after fees/slippage is not guaranteed.';coinGroup.append(coinNote);$('fields').prepend(coinGroup);
 $('strategyLogic').value=c.strategy;$('oppositeAction').value=c.opposite_action;renderStrategyDescription();
 $('lossStreakScope').value=c.loss_streak_scope;
 $('trailingBasis').value=c.trailing_distance_basis;$('trailingActivationBasis').value=c.trailing_activation_basis;renderTrailingDescription();
 $('strategyTf').value=c.timeframe;renderStrategyDescription();$('portfolioBasis').value=c.portfolio_loss_basis;$('marginMode').value=selectedAccount().config.margin_mode;$('dedicated').checked=selectedAccount().config.dedicated_account;
 renderRSISettings(c.rsi||{});
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
 $('selectAllCoins').disabled=locked||!watchlist.some(x=>x.selectable);
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
 $('status').textContent=s.closing?'Closing managed positions; waiting for exchange reconciliation':s.status.replaceAll('_',' ')+' · '+(s.reason||(s.settings.strategy==='rsi_hourly_intrabar'?'Waiting for intrabar RSI signals':s.settings.strategy==='sr_hourly_intrabar'?'Waiting for live intrabar breakouts':'Waiting for confirmed signals'));
 $('lossProgress').textContent=Number(s.daily_loss_pct).toFixed(2)+'% of starting equity used below the daily peak · '+(s.settings.loss_streak_scope==='per_coin'?'Loss streaks tracked per coin':s.streak+' consecutive account losses');renderRiskOverview();
 $('equityLabel').textContent='LIVE USDT equity'+(s.equity===null&&recent?' · read-only snapshot':'');$('pnlLabel').textContent='Open LIVE P&L';
 renderPnLTrailingStatus(tn,s.settings);
 $('fundsBadge').textContent='LIVE · REAL FUNDS';$('positionsTitle').textContent='LIVE positions';
 $('start').textContent='Enable LIVE entries';$('close').textContent='Close all LIVE positions';
 $('start').disabled=!tn.enabled_on_server||tn.entries||tn.closing||tn.status==='rate_limited';$('start').title=tn.status==='rate_limited'?'Waiting for verified account reads; Pause entries cancels automatic recovery':!tn.enabled_on_server?'Server-side live activation is disabled':'';
 $('fundsBadge').classList.add('red');$('liveAccountState').textContent=lv.management?'Production execution: REAL funds management active or recovering':'Production execution: inactive';
 $('liveModeNote').textContent=tn.enabled_on_server?'Live server enabled. Account checks, risk controls and manual activation apply.':'Live execution is disabled on the server.';
 $('positionNote').textContent='Bot-managed LIVE positions. Dollar loss limits can be exceeded by fees, slippage or outages. Exchange stop verification is reported in Activity. Opening times show the recorded entry-order creation time.';
 $('positions').replaceChildren();
 for(const p of s.positions){const coin=p.coin_pnl_trailing;const coinStatus=coin?.triggered?'Per-coin trailing: closing':coin?.active?'Per-coin trailing · peak '+money(coin.peak)+' · close at '+money(coin.threshold):current().settings.coin_pnl_trailing_enabled?'Per-coin trailing: waiting':'Per-coin trailing: off';const row=document.createElement('tr');for(const value of [p.symbol,p.side,tradeTimestamp(p.opened_at,true),money(p.value)+' / '+p.leverage+'×',money(p.pnl),p.take_profit?.enabled?(money(p.take_profit.dollars)+' · '+(p.take_profit.verified?'Bybit verified':p.take_profit.error?'Retrying verification':'Awaiting verification')):'Off',p.trailing?.enabled?(p.trailing.active?'Active · stop '+marketPrice(p.stop):'Waiting · activation '+marketPrice(p.trailing.activation_price)):'Off',coinStatus]){const td=document.createElement('td');if(value instanceof Node)td.append(value);else td.textContent=value;row.append(td)}row.onclick=()=>{location.hash='#chart';showPage();$('symbol').value=p.symbol;renderWatchlist();loadChart()};$('positions').append(row)}
 $('events').replaceChildren();for(const event of [...s.events].reverse().slice(0,30)){const li=document.createElement('li');li.textContent=new Date(event.time).toLocaleTimeString()+' · '+event.message;$('events').append(li)}
 const smaBlocks=Object.keys(tn.sma_exit_blocks||{});$('smaDataStatus').textContent=smaBlocks.length?'SMA exit data: new entries blocked only for '+smaBlocks.join(', ')+'. Other coins remain subject to their own signals and risk checks. Existing Bybit stops stay managed.':'';
 $('execution').replaceChildren();for(const record of tn.execution.filter(r=>!['closed','cancelled','rejected'].includes(r.phase))){const li=document.createElement('li');li.textContent=record.symbol+' · '+record.phase+(record.reason?' · '+record.reason:'');$('execution').append(li)}
 $('baseline').disabled=!(tn.risk.baseline_required&&tn.management);
 $('settingsGroup').disabled=tn.entries;
 renderScope();
 syncSymbolLock();
 draw();
}
function collect(){const cfg={...formDraft,strategy:$('strategyLogic').value,opposite_action:$('oppositeAction').value,timeframe:$('strategyTf').value,portfolio_loss_basis:$('portfolioBasis').value};for(const id of fieldIds){const input=$(id);cfg[id]=input.type==='checkbox'?input.checked:Number(input.value)}cfg.rsi=collectRSISettings();cfg.automatic_paper=false;cfg.loss_streak_scope=$('lossStreakScope').value;cfg.trailing_distance_basis=$('trailingBasis').value;cfg.trailing_activation_basis=$('trailingActivationBasis').value;
 cfg.margin_mode=$('marginMode').value;cfg.dedicated_account=$('dedicated').checked;return cfg}
function renderStrategyDescription(){const rsi=$('strategyLogic').value==='rsi_hourly_intrabar',hourly=['sr_hourly_intrabar','rsi_hourly_intrabar'].includes($('strategyLogic').value);$('strategyTf').disabled=false;if($('sma_exit_enabled')){$('sma_exit_enabled').disabled=hourly;if(hourly)$('sma_exit_enabled').checked=true;}const cross=['sma_cross','sma_7_50'].includes($('strategyLogic').value),combined=$('strategyLogic').value==='sr_sma_7_50',slow=$('strategyLogic').value==='sma_7_50'?50:200;$('strategyDescription').textContent=rsi?'Selected RSI calculation version and signal families on the forming selected-timeframe candle; no close wait. Own RSI filters below, independent of S/R filters. Long exits when 1-minute SMA 7 crosses below SMA 50; Short exits above. Polling is sampled, about every 5 seconds. Fixed Long/Short levels stay constant and do not need ML clustering. Corrected ML entries require the full history window; Uploaded Pine preserves the original array behavior: its ML thresholds are unavailable, so only eligible static crossings can trigger. Display toggles never change entry permission.':hourly?'Live selected-timeframe S/R breakout entries using confirmed Major/Minor levels; no candle-close wait. Mandatory 1-minute SMA exits: Long exits below SMA 50; Short exits above SMA 50 (SMA 7 cross). Sampled about every 5 seconds, not every tick. One trigger per level per entry candle; fresh observations required after a gap. Optional S/R filters still apply. Chart timeframe is independent.':combined?'Independent S/R breakouts OR SMA 7/50 crossovers can open positions after the selected-timeframe candle closes. Agreeing signals create one entry; opposite signals on the same candle are skipped. S/R filters apply only to the S/R branch. Existing exits and risk controls apply.':cross?'Confirmed selected-timeframe candle: SMA 7 crossing above SMA '+slow+' opens Long; crossing below opens Short. S/R levels and its optional entry filters do not affect this strategy.':'Confirmed resistance breakout opens Long; confirmed support break opens Short. Optional filters: price above/below the chosen SMA (default 200), and SMA 7 above/below SMA 50. If both are selected, both must pass; SMA 7/50 alignment does not require a fresh crossover. Exits are configured separately.';for(const id of ['pivot_long','pivot_short','major','minor'])if($(id))$(id).closest('label').hidden=cross||rsi;}
function renderPnLTrailingStatus(account,settings){
 let panel=$('pnlTrailingStatus');if(!panel){panel=document.createElement('div');panel.id='pnlTrailingStatus';panel.setAttribute('role','status');panel.setAttribute('aria-atomic','true');$('pnl').after(panel)}
 const trailing=account.pnl_trailing,phase=trailing?.triggered?'closing':!settings.pnl_trailing_enabled?'off':trailing?.active?'active':'waiting';
 const title={active:'TRAILING ACTIVE',waiting:'WAITING FOR TRIGGER',closing:'CLOSING ALL POSITIONS',off:'COMBINED TRAILING OFF'}[phase];
 const amounts=phase==='active'?[['Peak',trailing.peak],['Close all at or below',trailing.threshold]]:phase==='waiting'?[['Trigger P&L',settings.pnl_trailing_activation_dollars],['Trailing drop',settings.pnl_trailing_drop_dollars]]:[];
 const note=phase==='closing'?'Entry permission retained; new orders wait for flat confirmation.':phase==='active'?'Combined Open LIVE P&L · server-monitored exit':phase==='waiting'?'Combined Open LIVE P&L · waiting for activation':'Enable in Settings to monitor combined profit.';
 const key=JSON.stringify([phase,amounts,note]);if(panel.dataset.content===key)return;panel.dataset.content=key;panel.dataset.phase=phase;panel.className='pnl-trailing-panel pnl-trailing-'+phase;panel.replaceChildren();
 const heading=document.createElement('strong');heading.className='pnl-trailing-heading';heading.textContent=title;panel.append(heading);
 if(amounts.length){const values=document.createElement('div');values.className='pnl-trailing-values';for(const [label,value]of amounts){const item=document.createElement('div'),caption=document.createElement('span'),number=document.createElement('strong');caption.textContent=label+' ';number.textContent=value!=null&&Number.isFinite(Number(value))?money(value):'—';item.append(caption,number);values.append(item)}panel.append(values)}
 const description=document.createElement('small');description.textContent=note;panel.append(description);
}
function renderTrailingDescription(){const enabled=$('trailing_enabled').checked;for(const id of ['trailing_activation_pct','trailing_distance_pct','trailingBasis','trailingActivationBasis'])$(id).closest('label').hidden=!enabled;$('trailingDescription').textContent=enabled?'Activation uses '+($('trailingActivationBasis').value==='price'?'a favorable mark-price move from entry':'mark-price profit divided by the trade’s original margin')+', excluding fees. Distance uses '+($('trailingBasis').value==='price'?'a percentage of the best observed mark price':'a percentage of the original margin, converted to a fixed price distance')+'. Activation does not guarantee a profitable exit. Applies to new positions. The server samples prices and tightens a verified Bybit stop; during an outage the last confirmed stop stays on Bybit, but does not keep trailing.':'Trailing is off for new positions. Existing positions keep their original trailing settings and dollar stop.';}
$('settings').oninput=()=>{dirty=true;renderStrategyDescription();renderTrailingDescription()};
async function saveSettings(){try{const cfg=collect();updateAccount(await api('live/settings','PUT',cfg));if(cfg.symbols.length===1&&watchlist.some(x=>x.selectable&&x.symbol===cfg.symbols[0]))$('symbol').value=cfg.symbols[0];renderSettings();render();message('Settings saved. Existing positions keep their original size and dollar stop.');$('symbolSaveMessage').textContent='Saved trading symbols: '+current().settings.symbols.join(', ');loadChart()}catch(e){message(e.message);$('symbolSaveMessage').textContent=e.message}}
$('settings').onsubmit=e=>{e.preventDefault();saveSettings()};
$('saveSymbols').onclick=saveSettings;
$('search').oninput=renderWatchlist;
$('selectedOnly').onchange=renderWatchlist;
$('selectAllCoins').onclick=()=>setDraftSymbols(watchlist.filter(x=>x.selectable).map(x=>x.symbol));
$('selectVisible').onclick=()=>setDraftSymbols([...formDraft.symbols,...watchlist.filter(matchesSearch).map(x=>x.symbol)]);
$('clearSymbols').onclick=()=>setDraftSymbols([]);
$('onlyChart').onclick=()=>setDraftSymbols([$('symbol').value]);
$('editSymbols').onclick=()=>{location.hash='#settings';showPage();$('symbolPanel').scrollIntoView({behavior:'smooth',block:'start'});$('search').focus({preventScroll:true})};
$('start').onclick=async()=>{try{if(dirty){$('symbolSaveMessage').textContent='Save your strategy and trading symbols before activation.';location.hash='#settings';showPage();$('symbolPanel').scrollIntoView({behavior:'smooth'});return;}$('activationSymbols').textContent='Saved trading symbols: '+current().settings.symbols.join(', ')+' · '+current().settings.timeframe+' timeframe · '+money(current().settings.position_value)+' position value · '+current().settings.leverage+'× leverage · '+strategyName(current().settings.strategy)+' · '+(current().settings.opposite_action==='close_only'?'Close only on opposite signal':'Reversal');$('livePhrase').value='';$('activationError').textContent=lv.risk.daily_stop?'Daily-stop review: the account must be flat and reconciled, with equity inside the saved allowance. Restart preserves the day’s starting equity and peak. Increasing the daily percentage increases permitted risk.':'';$('liveDialog').showModal()}catch(e){message(e.message)}};
$('pause').onclick=async()=>{try{updateAccount(await api('live/pause','POST'));render()}catch(e){message(e.message)}};
$('close').onclick=async()=>{if(!confirm('Close all LIVE REAL-FUNDS managed positions and block new entries?'))return;try{updateAccount(await api('live/close-all','POST'));render()}catch(e){message(e.message)}};
$('activateLive').onclick=async()=>{try{const phrase=$('livePhrase').value.trim();if(phrase!=='ENABLE LIVE ORDERS'){$('activationError').textContent='Type ENABLE LIVE ORDERS exactly to enable LIVE trading.';$('livePhrase').focus();return;}updateAccount(await api('live/activate','POST',{confirmation:phrase}));$('liveDialog').close();$('livePhrase').value='';render();showReadiness();message('Account checks completed. Watch live account and execution status.')}catch(e){$('activationError').textContent=e.message}};
$('cancelActivation').onclick=()=>$('liveDialog').close();
$('baseline').onclick=async()=>{if(!confirm('Use current '+mode.toUpperCase()+' equity as the new UTC day baseline? The account must be flat. A current-day loss lock cannot be reset.'))return;try{updateAccount(await api(mode+'/baseline','POST'));render()}catch(e){message(e.message)}};
async function loadChart(background=false){const context=mode+':'+$('symbol').value+':'+$('tf').value;if(context!==chartContext){chartContext=context;chartLookback=1000;historyExhausted=false;historyError='';rsiRows=[];chartMeasurement=null;rulerPointer=null;chartTouches.clear();chartPinch=null;chartDrag=null;}if(!background){currentQuote=null;chartLoading=true;chartRows=[];smaRows=[];analysis=[];rsiRows=[];srAnalysis=[];draw();refreshQuote();}if(background&&(chartLookback>1000||chartLoading||chartDrag||chartPinch||chartTouches.size||rulerPointer||chartOffset>0))return;chartLoading=true;const request=++chartRequest;$('chartStatus').textContent='Loading '+'LIVE'+' candles…';try{const data=await api('chart/'+$('symbol').value+'?interval='+$('tf').value+'&environment='+mode+'&lookback='+chartLookback+'&include_rsi=true');if(request!==chartRequest||(background&&(chartDrag||chartPinch||chartTouches.size||rulerPointer||chartOffset>0)))return;if(!background){chartPriceShift=0;chartPriceZoom=1;}chartRows=data.candles;smaRows=data.sma;analysis=data.analysis||[];rsiRows=data.rsi_analysis||[];srAnalysis=data.sr_analysis||data.analysis||[];referenceSignalStrategy=data.chart_signal_strategy||'sr_breakout';historyExhausted=Boolean(data.history_exhausted);chartOffset=0;$('chartStatus').textContent=data.source+' · forming candle may update · '+(data.indicator_status||'Indicator arrows use closed candles; E/X marks recorded trades')+(chartStyle.showRSI?' · '+(data.rsi_status||'RSI data unavailable'):'');draw()}catch(e){if(request!==chartRequest)return;chartRows=[];smaRows=[];analysis=[];rsiRows=[];srAnalysis=[];draw();$('chartStatus').textContent=e.message}finally{if(request===chartRequest){chartLoading=false;renderHistoryStatus()}}}
function renderHistoryStatus(){
 let notice=$('chartFreshness');if(!notice){notice=document.createElement('p');notice.id='chartFreshness';notice.className='note';notice.setAttribute('role','status');$('chartStatus').after(notice)}
 const lastStamp=chartRows.at(-1)?.time,now=(Date.now()+marketClockOffset)/1000,seconds=chartIntervalSeconds($('tf').value);
 const historical=chartLookback>1000||chartOffset>0,behind=Number.isFinite(lastStamp)?Math.max(0,Math.floor(now/seconds)*seconds-lastStamp):0;
 notice.textContent=historical?'Historical view: candle/RSI auto refresh is paused; the separate price quote may still be live. Use Latest candle to compare current signals.':Number.isFinite(lastStamp)&&behind>=seconds&&now-lastStamp-seconds>10?'Candle/RSI data is behind the live market by '+Math.floor(behind/60)+' minutes. Use Latest candle or Refresh chart; a live price quote does not refresh RSI by itself.':!$('autoChart').checked?'Candle/RSI auto refresh is off. Use Refresh chart for a current comparison.':'';
 notice.classList.toggle('red',Boolean(behind>=seconds&&!historical));
 const first=chartRows[0],last=chartRows.at(-1);$('historyStatus').textContent=chartRows.length+' candles loaded'+(first?' · '+chartTime(first.time)+' → '+chartTime(last.time)+' · New York':'')+(chartLookback>1000?' · Historical replay snapshot; refresh manually. Latest candle returns to the 1,000-candle live view. Early signals may change as warm-up expands':'')+(historyExhausted?' · End of available exchange history':'');
 if(historyError)$('historyStatus').textContent=historyError;
 $('loadOlderHistory').disabled=chartLoading||historyExhausted||chartLookback>=5000||!chartRows.length;
 $('loadOlderHistory').textContent=chartLookback>=5000?'5,000-candle limit reached':chartLoading?'Loading history…':'Load 1,000 older candles';
}
async function loadOlderHistory(){
 if(chartLoading||historyExhausted||chartLookback>=5000||!chartRows.length)return;
 const context=chartContext,requested=Math.min(5000,chartLookback+1000),endTime=chartRows[chartRows.length-chartOffset-1]?.time,request=++chartRequest;
 chartLoading=true;historyError='';renderHistoryStatus();
 try{const data=await api('chart/'+$('symbol').value+'?interval='+$('tf').value+'&environment='+mode+'&lookback='+requested+'&include_rsi=true');if(context!==chartContext||request!==chartRequest)return;const anchor=chartRows[chartRows.length-chartOffset-1]?.time??endTime;chartLookback=requested;chartRows=data.candles;smaRows=data.sma;analysis=data.analysis||[];rsiRows=data.rsi_analysis||[];srAnalysis=data.sr_analysis||data.analysis||[];referenceSignalStrategy=data.chart_signal_strategy||'sr_breakout';historyExhausted=Boolean(data.history_exhausted)||chartRows.length<requested;const at=chartRows.findIndex(r=>r.time===anchor);chartOffset=at>=0?chartRows.length-at-1:0;draw()}
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
