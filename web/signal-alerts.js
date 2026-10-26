// Saved signal-status updates. Alerts are observations, not order/fill receipts.
let signalAlertOffset=0,signalAlertTotal=0,signalAlertRequest=0,signalAlertBusy=false;
let signalAlertNewest=null,signalAlertAudio=null;
function signalAlertsMarkup(){return `<section class="card"><h2>Signal alerts</h2>
<p class="note">Saved signal-status updates for your selected trading strategy and coins, including skipped signals during managed pauses. Linked signals show their latest known outcome; queued does not mean filled. Historical chart arrows are not backfilled. Latest 5,000 updates retained.</p>
<label>Coin<input id="signalAlertSymbol" type="search" placeholder="All coins or JCTUSDT" aria-label="Signal alert coin"></label>
<div class="row"><button id="refreshSignalAlerts" type="button">Refresh alerts</button><label><input id="signalAlertSound" type="checkbox"> Sound for new updates</label></div>
<p class="note">Sound works while THOTH is open and visible and the browser permits audio. This is not phone push notification delivery.</p>
<p id="signalAlertStatus" role="status" aria-live="polite"></p>
<div class="table-scroll"><table><thead><tr><th>Observed (New York)</th><th>Coin</th><th>Direction</th><th>Source / crossing</th><th>Strategy</th><th>Status</th></tr></thead><tbody id="signalAlertRows"></tbody></table></div>
<div class="row"><button id="signalAlertsPrevious" type="button">← Newer</button><span id="signalAlertsPage"></span><button id="signalAlertsNext" type="button">Older →</button></div></section>`}
function bindSignalAlerts(){
 $('refreshSignalAlerts').onclick=()=>{signalAlertOffset=0;loadSignalAlerts()};
 $('signalAlertsPrevious').onclick=()=>{signalAlertOffset=Math.max(0,signalAlertOffset-50);loadSignalAlerts()};
 $('signalAlertsNext').onclick=()=>{signalAlertOffset+=50;loadSignalAlerts()};
 let timer;$('signalAlertSymbol').oninput=()=>{clearTimeout(timer);signalAlertNewest=null;signalAlertOffset=0;timer=setTimeout(loadSignalAlerts,300)};
 $('signalAlertSound').onchange=async()=>{if(!$('signalAlertSound').checked)return;try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)throw Error('Audio unavailable');signalAlertAudio=signalAlertAudio||new Audio();await signalAlertAudio.resume()}catch{$('signalAlertSound').checked=false;$('signalAlertStatus').textContent='Sound unavailable in this browser; saved alerts remain available.'}};
 setInterval(()=>{if(location.hash==='#alerts'&&!document.hidden&&signalAlertOffset===0&&!signalAlertBusy&&state)loadSignalAlerts()},5000);
}
function signalAlertSources(item){
 const d=item.diagnostic||{},names={static30_up:'RSI over 30',static30_down:'RSI below 30',static70_up:'RSI over 70',static70_down:'RSI below 70',ml_long_up:'RSI over Long',ml_long_down:'RSI below Long',ml_short_up:'RSI over Short',ml_short_down:'RSI below Short'};
 const recorded=(item.entry_sources||[]).map(source=>{
  if(source.startsWith('htf:')){const [,frame,...parts]=source.split(':');return (frame==='240'?'4-hour':'Daily')+' '+signalAlertSources({entry_sources:[parts.join(':')]});}
  if(source.startsWith('ob:'))return ({bullish_formed:'Bullish Order Block formed',bearish_formed:'Bearish Order Block formed',supply_breakout:'Breakout Above Supply',demand_breakdown:'Breakdown Below Demand'})[source.slice(3)]||source;
  if(source.startsWith('volume_delta:'))return ({bullish_strong:'Volume Delta Strong Bullish (Up)',bullish_weak:'Volume Delta Weak Bullish (Up-)',bullish_all:'Volume Delta All Bullish',bearish_strong:'Volume Delta Strong Bearish (Down)',bearish_weak:'Volume Delta Weak Bearish (Down+)',bearish_all:'Volume Delta All Bearish'})[source.split(':')[1]]||source;
  if(source.startsWith('rsi:'))return names[source.slice(4)]||source;
  if(source.startsWith('sma:'))return 'SMA '+source.slice(4).replace('_','/')+' crossover';
  const [prefix,key]=source.split(':');if(['sr','inverse_sr'].includes(prefix)&&key){const [term,side,grade]=key.split('_');return (prefix==='inverse_sr'?'Inverse S/R ':'S/R ')+(term==='long'?'long-term':'short-term')+' '+grade+' '+side+(prefix==='inverse_sr'&&item.source_signal?' · '+item.source_signal+' signal → '+item.signal+' position':'');}
  return source;
 });
 const parts=recorded.length?recorded:[...(d.rsi_events||[]).map(k=>names[k]||k),...(d.sr_levels||[]).map(k=>'S/R '+k.replaceAll('_',' '))];
 return parts.join(' · ')||(item.strategy?.startsWith('sr_')?'S/R / strategy signal':item.strategy?.startsWith('sma')?'SMA crossover':'RSI / strategy signal');
}
function signalAlertDirection(item){
 if(item.signal)return item.signal==='Long'?'Bullish / Long':item.signal==='Short'?'Bearish / Short':item.signal;
 const d=item.diagnostic||{},sides=new Set();
 for(const key of d.rsi_events||[])sides.add(key.endsWith('_up')?'Long':'Short');
 for(const key of d.sr_levels||[])sides.add(key.includes('resistance')?'Long':'Short');
 return sides.size===1?(sides.has('Long')?'Bullish / Long':'Bearish / Short'):sides.size?'Mixed / conflicting':'—';
}
async function loadSignalAlerts(){
 const request=++signalAlertRequest,symbol=$('signalAlertSymbol').value.trim().toUpperCase(),offset=signalAlertOffset;
 if(symbol&&!/^[A-Z0-9]{1,30}USDT$/.test(symbol)){$('signalAlertStatus').textContent='Enter a complete symbol, for example JCTUSDT.';return}
 signalAlertBusy=true;
 try{
  const data=await api('live/signal-alerts?limit=50&offset='+offset+(symbol?'&symbol='+encodeURIComponent(symbol):''));
  if(request!==signalAlertRequest)return;
  signalAlertTotal=data.total;
  const newest=data.items[0]?.id;
  if(offset===0&&newest!=null){
   signalAlertNewest=newest;
  }
  $('signalAlertRows').replaceChildren();
  for(const item of data.items){
   const row=document.createElement('tr'),time=document.createElement('td');time.append(tradeTimestamp(item.observed_at??item.time));row.append(time);
   for(const value of [item.symbol,signalAlertDirection(item),signalAlertSources(item),strategyName(item.strategy),item.reason]){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}
   $('signalAlertRows').append(row);
  }
  $('signalAlertStatus').textContent=data.total?data.total+' saved signal alerts · latest updates first · refreshes every 5 seconds on the latest page':'No saved signal alerts yet. New detections and decisions will appear here.';
  $('signalAlertsPage').textContent='Page '+(Math.floor(offset/50)+1)+' of '+Math.max(1,Math.ceil(data.total/50));
  $('signalAlertsPrevious').disabled=offset===0;$('signalAlertsNext').disabled=offset+50>=data.total;
 }catch(e){if(request===signalAlertRequest)$('signalAlertStatus').textContent='Signal alerts unavailable: '+e.message}
 finally{if(request===signalAlertRequest)signalAlertBusy=false}
}

let floatingAlertNewest=null,floatingAlertBusy=false,floatingAlertWatchOffset=0;
const dismissedSignalAlerts=new Set();
function setupFloatingSignalAlerts(){
 const stack=document.createElement('aside');stack.id='floatingSignalAlerts';stack.className='floating-signal-alerts';stack.setAttribute('aria-label','New signal alerts');stack.setAttribute('aria-live','polite');document.body.append(stack);
 const closeAll=document.createElement('button');closeAll.id='closeAllSignalAlerts';closeAll.type='button';closeAll.textContent='Close all alerts';closeAll.hidden=true;
 closeAll.onclick=()=>{for(const box of [...stack.children]){dismissedSignalAlerts.add(box.dataset.alertId);box.remove()}closeAll.hidden=true};
 stack.before(closeAll);new MutationObserver(()=>{closeAll.hidden=!stack.children.length}).observe(stack,{childList:true});
 setInterval(pollFloatingSignalAlerts,5000);pollFloatingSignalAlerts();
}
async function pollFloatingSignalAlerts(){
 if(!state||document.hidden||floatingAlertBusy)return;
 floatingAlertBusy=true;
 try{
  const cards=[...$('floatingSignalAlerts').children],ids=cards.map(box=>box.dataset.alertId);
  if(floatingAlertWatchOffset>=ids.length)floatingAlertWatchOffset=0;
  const watched=ids.slice(floatingAlertWatchOffset,floatingAlertWatchOffset+200);floatingAlertWatchOffset+=200;
  const data=await api('live/signal-alerts?limit=50&offset=0'+(watched.length?'&watch_ids='+watched.join(','):''));
  const newest=data.items.length?Math.max(...data.items.map(item=>item.update_id??item.id)):null;
  for(const item of [...data.items,...(data.updates||[])]){
   const box=cards.find(box=>box.dataset.alertId===String(item.id));
   if(box)box.querySelector('p').textContent=signalAlertSources(item)+' · '+item.reason;
  }
  if(floatingAlertNewest===null){floatingAlertNewest=newest??0;return}
  const fresh=data.items.filter(item=>(item.update_id??item.id)>floatingAlertNewest);
  floatingAlertNewest=Math.max(floatingAlertNewest,newest??0);
  if(!fresh.length)return;
  if($('signalAlertSound').checked&&signalAlertAudio?.state==='running'){
   const tone=signalAlertAudio.createOscillator(),gain=signalAlertAudio.createGain();tone.connect(gain);gain.connect(signalAlertAudio.destination);gain.gain.value=.04;tone.frequency.value=660;tone.start();tone.stop(signalAlertAudio.currentTime+.15);
  }
  const stack=$('floatingSignalAlerts');
  for(const item of fresh.slice().reverse()){
   if(dismissedSignalAlerts.has(String(item.id))||[...stack.children].some(box=>box.dataset.alertId===String(item.id)))continue;
   const box=document.createElement('div');box.className='signal-toast';box.dataset.alertId=String(item.id);
   const dismiss=()=>{dismissedSignalAlerts.add(String(item.id));box.remove()};
   const title=document.createElement('strong');title.textContent=item.symbol+' · '+signalAlertDirection(item);
   const text=document.createElement('p');text.textContent=signalAlertSources(item)+' · '+item.reason;
   const link=document.createElement('a');link.href='#alerts';link.textContent='View signal alerts';link.onclick=dismiss;
   const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Dismiss signal alert');close.onclick=dismiss;
   box.append(title,close,text,link);stack.append(box);
  }
 }catch{/* Read failures never change trading state; Alerts page shows fetch errors. */}
 finally{floatingAlertBusy=false}
}
