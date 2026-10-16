const $=id=>document.getElementById(id);
let state,tn,lv,watchlist=[],chartRows=[],smaRows=[],analysis=[],polling=false,visibleBars=70,chartOffset=0;
let mode='paper',dirty=false,formDraft=null,chartRequest=0,chartLoading=false;
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(n||0));
const fieldIds=['position_value','leverage','stop_dollars','daily_loss_pct','max_positions','consecutive_losses','pause_minutes','pivot_long','pivot_short','sma_length','major','minor','sma_enabled','portfolio_loss_enabled','portfolio_loss_pct','daily_resume_automatic'];
async function api(path,method='GET',body){
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),45000);
 try{const response=await fetch('/api/'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:controller.signal});
 if(response.status===401){location.href='/login';throw Error('Sign in required')}
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
 }finally{clearTimeout(timeout)}
}
function message(text){$('message').textContent=text}
function selectedAccount(){return mode==='live'?lv:tn}
function updateAccount(data){if(mode==='live')lv=data;else tn=data}
function current(){
 if(mode==='paper')return state;
 const tn=selectedAccount();
 return {closing:tn.closing,settings:tn.config,equity:tn.equity,status:tn.status,reason:tn.reason,
 daily_loss_pct:tn.risk.loss_pct,streak:tn.risk.streak,
 positions:tn.positions.map(p=>({...p,entry:Number(p.entry),qty:Number(p.qty),mark:Number(p.mark),value:Number(p.qty)*Number(p.entry),leverage:tn.config.leverage,stop_dollars:Number(p.stop_dollars??tn.config.stop_dollars)})),
 trades:tn.closures.map(t=>({...t,closed_at:t.closed_at})),events:tn.events.map(e=>({...e,time:e.time*1000}))};
}
function field(label,id,value,type='number',attributes=''){
 const wrapper=document.createElement('label');wrapper.htmlFor=id;wrapper.append(document.createTextNode(label));
 const input=document.createElement('input');input.id=id;input.type=type;
 if(type==='checkbox')input.checked=Boolean(value);else{input.value=value;input.required=true;input.min='1';input.step='1'}
 if(attributes){for(const [key,value]of Object.entries(attributes))input.setAttribute(key,value)}
 wrapper.append(input);return wrapper;
}
function renderSettings(){
 const tn=selectedAccount();const c=current().settings;$('fields').replaceChildren();
 $('fields').append(field('Automatic PAPER mode','automatic_paper',c.automatic_paper,'checkbox'));
 $('automatic_paper').parentElement.hidden=mode!=='paper';
 for(const [id,label,attrs]of [
 ['position_value','Position value (USDT)',{min:'.01',step:'.01'}],['leverage','Leverage (×)',{max:'100'}],
 ['stop_dollars','Stop per position (USDT)',{min:'.01',step:'.01'}],['daily_loss_pct','Daily account loss (%)',{min:'.1',max:'100',step:'.1'}],
 ['portfolio_loss_pct','Overall position loss (%)',{min:'.1',max:'100',step:'.1'}],['max_positions','Maximum positions',{max:'100'}],['consecutive_losses','Consecutive losses',{max:'100'}],
 ['pause_minutes','Pause duration (minutes)',{max:'10080'}],['pivot_long','Long pivot period',{max:'100'}],['pivot_short','Short pivot period',{max:'100'}],['sma_length','SMA length',{max:'500'}]])
 $('fields').append(field(label,id,c[id],'number',attrs));
 for(const [id,label]of [['major','Major levels'],['minor','Minor levels'],['sma_enabled','SMA filter'],['portfolio_loss_enabled','Enable overall position loss stop'],['daily_resume_automatic','Resume after next UTC day']])$('fields').append(field(label,id,c[id],'checkbox'));
 $('strategyTf').value=c.timeframe;$('portfolioBasis').value=c.portfolio_loss_basis;$('marginMode').value=selectedAccount().config.margin_mode;$('dedicated').checked=selectedAccount().config.dedicated_account;
 $('settingsTitle').textContent=mode==='paper'?'Paper strategy & risk':(mode==='live'?'LIVE strategy & risk':'Testnet strategy & risk');
 $('settingsGroup').disabled=mode!=='paper'&&tn.entries;
 dirty=false;formDraft={...c,symbols:[...c.symbols]};renderWatchlist();
}
function renderWatchlist(){
 const q=$('search').value.toUpperCase();$('coins').replaceChildren();
 for(const x of watchlist.filter(x=>x.original.includes(q))){
 const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=x.symbol;
 input.disabled=!x.selectable||(mode!=='paper'&&selectedAccount().entries);input.checked=x.selectable&&formDraft.symbols.includes(x.symbol);
 input.onchange=()=>{const set=new Set(formDraft.symbols);input.checked?set.add(x.symbol):set.delete(x.symbol);formDraft.symbols=[...set];dirty=true;$('selection').textContent=set.size+' selected · Unsaved changes'};
 label.append(input,document.createTextNode(x.original.replace('BYBIT:','')));label.title=x.status;$('coins').append(label);
 }$('selection').textContent=formDraft.symbols.length+' selected'+(dirty?' · Unsaved changes':'');
}
function render(){
 const tn=selectedAccount();const s=current();$('equity').textContent=s.equity===null?'—':money(s.equity);$('pnl').textContent=money(s.positions.reduce((sum,p)=>sum+Number(p.pnl),0));
 $('limit').textContent=s.settings.daily_loss_pct;$('count').textContent=s.positions.length+' / '+s.settings.max_positions;
 $('status').textContent=s.closing?'Closing managed positions; waiting for exchange reconciliation':s.status.replaceAll('_',' ')+' · '+(s.reason||'Waiting for confirmed breakouts');
 $('lossProgress').textContent=Number(s.daily_loss_pct).toFixed(2)+'% daily drawdown · '+s.streak+' consecutive losses';
 $('equityLabel').textContent=mode==='paper'?'Paper equity':(mode==='live'?'LIVE USDT equity':'Testnet USDT equity');$('pnlLabel').textContent=mode==='paper'?'Open paper P&L':(mode==='live'?'Open LIVE P&L':'Open testnet P&L');
 $('fundsBadge').textContent=mode==='paper'?'PAPER FUNDS':(mode==='live'?'LIVE · REAL FUNDS':'TESTNET · NO REAL MONEY');$('positionsTitle').textContent=mode==='paper'?'Paper positions':(mode==='live'?'LIVE positions':'Testnet positions');
 $('start').textContent=mode==='paper'?'Start paper mode':(mode==='live'?'Enable LIVE entries':'Enable testnet entries');$('close').textContent=mode==='paper'?'Close all paper positions':(mode==='live'?'Close all LIVE positions':'Close all testnet positions');
 $('start').disabled=mode!=='paper'&&(!tn.enabled_on_server||tn.entries||tn.closing);$('start').title=mode!=='paper'&&!tn.enabled_on_server?'Server-side '+mode+' activation is disabled':'';
 $('paperPanel').hidden=mode!=='paper';$('fundsBadge').classList.toggle('red',mode==='live');$('liveAccountState').textContent=lv.management?'Production execution: REAL funds management active or recovering':'Production execution: inactive';$('testnetControls').hidden=mode==='paper';
 $('testnetModeNote').textContent=tn.enabled_on_server?'Server '+mode+' gate enabled. Account and reference checks still apply.':'Server gate disabled. '+(mode==='live'?'Live also requires a source comparison and real testnet acceptance.':'Configure and validate the account before enabling testnet orders.');
 $('positions').replaceChildren();
 for(const p of s.positions){const row=document.createElement('tr');for(const value of [p.symbol,p.side,money(p.value)+' / '+p.leverage+'×',money(p.pnl)]){const td=document.createElement('td');td.textContent=value;row.append(td)}row.onclick=()=>{$('symbol').value=p.symbol;loadChart()};$('positions').append(row)}
 $('events').replaceChildren();for(const event of [...s.events].reverse().slice(0,12)){const li=document.createElement('li');li.textContent=new Date(event.time).toLocaleTimeString()+' · '+event.message;$('events').append(li)}
 $('execution').replaceChildren();for(const record of tn.execution.filter(r=>!['closed','cancelled','rejected'].includes(r.phase))){const li=document.createElement('li');li.textContent=record.symbol+' · '+record.phase+(record.reason?' · '+record.reason:'');$('execution').append(li)}
 $('baseline').disabled=!(tn.risk.baseline_required&&tn.management);
 $('settingsGroup').disabled=mode!=='paper'&&tn.entries;
 draw();
}
function collect(){const cfg={...formDraft,timeframe:$('strategyTf').value,portfolio_loss_basis:$('portfolioBasis').value};for(const id of fieldIds){const input=$(id);cfg[id]=input.type==='checkbox'?input.checked:Number(input.value)}cfg.automatic_paper=mode==='paper'&&$('automatic_paper').checked;
 if(mode!=='paper'){cfg.margin_mode=$('marginMode').value;cfg.dedicated_account=$('dedicated').checked}return cfg}
$('settings').oninput=()=>{dirty=true};
$('settings').onsubmit=async e=>{e.preventDefault();try{const cfg=collect();if(mode==='paper')state=await api('settings','PUT',cfg);else updateAccount(await api(mode+'/settings','PUT',cfg));renderSettings();render();message('Settings saved. Existing positions keep their original size and dollar stop.');loadChart()}catch(e){message(e.message)}};
$('viewMode').onchange=()=>{if(dirty&&!confirm('Discard unsaved settings and switch funds view?')){$('viewMode').value=mode;return}mode=$('viewMode').value;renderSettings();render();loadChart();connectionStatus();showReadiness();showReadiness()};
$('search').oninput=renderWatchlist;
$('start').onclick=async()=>{try{if(mode==='paper'){state=await api('control/start','POST');render()}else {const isLive=mode==='live';$('activationHeading').textContent=isLive?'Enable LIVE orders — REAL FUNDS':'Enable Bybit TESTNET orders';$('activationPhraseLabel').textContent=isLive?'ENABLE LIVE ORDERS':'ENABLE TESTNET ORDERS';$('activationDescription').textContent=isLive?'This can trade REAL funds. Reference comparison, actual testnet acceptance and an IP-bound dedicated-account key are required.':'This uses testnet funds, not real funds. Dedicated account and matching margin mode are required.';$('testnetPhrase').value='';$('activationError').textContent='';$('testnetDialog').showModal()}}catch(e){message(e.message)}};
$('pause').onclick=async()=>{try{if(mode==='paper')state=await api('control/pause','POST');else updateAccount(await api(mode+'/pause','POST'));render()}catch(e){message(e.message)}};
$('close').onclick=async()=>{if(!confirm('Close all '+(mode==='paper'?'PAPER':mode==='live'?'LIVE REAL-FUNDS':'TESTNET')+' managed positions and block new entries?'))return;try{if(mode==='paper')state=await api('control/close_all','POST');else updateAccount(await api(mode+'/close-all','POST'));render()}catch(e){message(e.message)}};
$('activateTestnet').onclick=async()=>{try{updateAccount(await api(mode+'/activate','POST',{confirmation:$('testnetPhrase').value}));$('testnetDialog').close();$('testnetPhrase').value='';render();message('Account checks completed. Watch '+mode+' account and execution status.')}catch(e){$('activationError').textContent=e.message}};
$('cancelActivation').onclick=()=>$('testnetDialog').close();
$('baseline').onclick=async()=>{if(!confirm('Use current '+mode.toUpperCase()+' equity as the new UTC day baseline? The account must be flat. A current-day loss lock cannot be reset.'))return;try{updateAccount(await api(mode+'/baseline','POST'));render()}catch(e){message(e.message)}};
$('paperOrder').onsubmit=async e=>{e.preventDefault();try{state=await api('paper/orders','POST',{symbol:$('symbol').value,side:$('side').value,price:Number($('price').value),signal_id:crypto.randomUUID()});render();message('Manual paper order recorded.')}catch(e){message(e.message)}};
$('mark').onclick=async()=>{try{const price=Number($('price').value);if(!(price>0))throw Error('Enter a positive paper price');state=await api('paper/marks','POST',{prices:{[$('symbol').value]:price}});render()}catch(e){message(e.message)}};
async function loadChart(background=false){if(background&&(chartLoading||chartOffset>0))return;chartLoading=true;const request=++chartRequest;$('chartStatus').textContent='Loading '+(mode==='testnet'?'TESTNET':mode==='live'?'LIVE':'Bybit')+' candles…';try{const data=await api('chart/'+$('symbol').value+'?interval='+$('tf').value+'&environment='+mode);if(request!==chartRequest)return;chartRows=data.candles;smaRows=data.sma;analysis=data.analysis||[];chartOffset=0;$('chartStatus').textContent=data.source+' · forming candle excluded · see strategy reference status';draw()}catch(e){if(request!==chartRequest)return;chartRows=[];smaRows=[];analysis=[];draw();$('chartStatus').textContent=e.message}finally{if(request===chartRequest)chartLoading=false}}
$('symbol').onchange=()=>loadChart();$('tf').onchange=()=>loadChart();
async function connectionStatus(verify=false){try{const environment=mode==='live'?'live':'testnet';const status=await api((verify?'connection/verify':'connection')+'?environment='+environment,verify?'POST':'GET');$('accountStatus').textContent=environment.toUpperCase()+' read access: '+(status.verified?'verified':status.configured?'not verified':'not configured')+' · '+status.reason}catch(e){$('accountStatus').textContent='Connection check unavailable: '+e.message}}
$('verifyAccount').onclick=()=>connectionStatus(true);
$('logout').onclick=async()=>{try{await api('logout','POST');location.href='/login'}catch(e){message(e.message)}};
async function init(){try{[state,tn,lv,watchlist]=await Promise.all([api('state'),api('testnet'),api('live'),api('watchlist')]);for(const item of watchlist.filter(x=>x.selectable)){const option=document.createElement('option');option.value=item.symbol;option.textContent=item.symbol;$('symbol').append(option)}renderSettings();render();loadChart();connectionStatus();showReadiness();showReadiness();
 setInterval(async()=>{if(polling)return;polling=true;try{const updated=await Promise.all([api('state'),api('testnet'),api('live'),api('runner')]);[state,tn,lv]=updated;const health=mode!=='paper'?selectedAccount().feed_health:updated[3];$('runnerHealth').textContent='Market worker: '+(health?.status||'idle');render()}catch{$('status').textContent='Connection lost · last displayed data may be stale'}finally{polling=false}},5000);
 }catch(e){message(e.message)}}
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
window.addEventListener('load',init);
setInterval(()=>{if(state&&$('autoChart').checked)loadChart(true)},15000);

async function showReadiness(){try{const r=await api('readiness');const reference=mode==='live'?r.live_reference:mode==='testnet'?r.testnet_reference:r.paper_reference;$('readinessMessage').textContent=(mode==='paper'?'Paper simulation available':mode==='live'?(r.live_ready?'Live entries active':'Live orders locked or inactive'):'Testnet activation requires account checks')+' · '+(reference.verified?'Reference dataset matched':reference.reason)}catch{$('readinessMessage').textContent='Readiness status unavailable'}}
$('checkReadiness').onclick=showReadiness;
