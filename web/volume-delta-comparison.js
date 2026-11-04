let deltaTimingOffset=0,deltaTimingRequest=0,deltaTimingLoading=false;
function volumeDeltaTimingMarkup(){return `<details class="card" id="deltaTimingPanel"><summary>Volume Delta · :25 versus :30 · <span id="deltaTimingBadge">Checking…</span></summary>
<p>Read-only comparison alongside either Volume Delta 1h :30 strategy (with or without the separate closed-1m SMA7 exit). Uses the same saved SMA7/50 and signal-type settings. First snapshot per coin during :25–:26 is compared with the actual first :30–:31 source decision. No orders are sent by this panel; account/risk eligibility is not evaluated.</p>
<p>The :25 observation needs an additional bounded minute-data download per coin, only during :25–:26. Collection continues when this panel is closed and entries are paused, provided the selected strategy's market worker remains connected. A missed window stays unverified; filter changes are not paired.</p>
<p>Price advantage: positive means the :25 snapshot offered a better price for the same direction. Moves to the same hourly close are price measurements only, not simulated profit or a replay of stops, trailing, fees, funding or slippage.</p>
<label><input type="checkbox" id="deltaTimingEnabled" checked> Enable :25 observation only</label>
<div class="row"><button type="button" id="deltaTimingRefresh">Refresh comparison</button><button type="button" id="deltaTimingCopy">Copy all comparisons</button></div><p id="deltaTimingStatus" role="status"></p>
<div class="table-scroll"><table><thead><tr><th>Hour / coin</th><th>:25 snapshot</th><th>:30 source decision</th><th>Comparison</th><th>Move to same hourly close</th><th>Details</th></tr></thead><tbody id="deltaTimingRows"></tbody></table></div>
<div class="row"><button type="button" id="deltaTimingPrevious">← Newer</button><span id="deltaTimingPage"></span><button type="button" id="deltaTimingNext">Older →</button></div>
<textarea id="deltaTimingCopyFallback" hidden readonly aria-label="All Volume Delta comparisons for copying" style="width:100%;min-height:180px"></textarea></details>`}
function bindVolumeDeltaTiming(){
 $('deltaTimingRefresh').onclick=()=>{deltaTimingOffset=0;loadVolumeDeltaTiming()};
 $('deltaTimingPrevious').onclick=()=>{deltaTimingOffset=Math.max(0,deltaTimingOffset-50);loadVolumeDeltaTiming()};
 $('deltaTimingNext').onclick=()=>{deltaTimingOffset+=50;loadVolumeDeltaTiming()};
 $('deltaTimingPanel').ontoggle=()=>{if($('deltaTimingPanel').open)loadVolumeDeltaTiming({background:true})};
 $('deltaTimingEnabled').onchange=async()=>{const input=$('deltaTimingEnabled'),enabled=input.checked;input.disabled=true;try{await api('live/volume-delta-comparison/settings','PUT',{enabled});await loadVolumeDeltaTiming()}catch(e){input.checked=!enabled;$('deltaTimingStatus').textContent=e.message}finally{input.disabled=false}};
 $('deltaTimingCopy').onclick=async()=>{const button=$('deltaTimingCopy'),box=$('deltaTimingCopyFallback');button.disabled=true;box.hidden=true;try{const report=await api('live/volume-delta-comparison/export'),text=JSON.stringify(report,null,2);try{await navigator.clipboard.writeText(text);$('deltaTimingStatus').textContent='Copied all '+report.total+' retained comparisons. No orders or hypothetical profits.'}catch{box.value=text;box.hidden=false;box.focus();box.select();$('deltaTimingStatus').textContent='Select and copy the comparison report below.'}}catch(e){$('deltaTimingStatus').textContent='Copy unavailable: '+e.message}finally{button.disabled=false}};
}
async function loadVolumeDeltaTiming({background=false}={}){
 if(background&&deltaTimingLoading)return;
 deltaTimingLoading=true;const request=++deltaTimingRequest;
 try{
  const data=await api('live/volume-delta-comparison?limit=50&offset='+deltaTimingOffset);if(request!==deltaTimingRequest)return;
  $('deltaTimingEnabled').checked=data.enabled;$('deltaTimingBadge').textContent=data.enabled?'ON · observation only':'OFF';$('deltaTimingRows').replaceChildren();
  const pct=value=>value==null?'Unverified':(value>=0?'+':'')+Number(value).toFixed(3)+'%';
  const sample=value=>!value?'Not observed':(value.valid?(value.signal||'No qualifying signal'):'Unverified: '+value.reason)+' · '+(value.price==null?'price unavailable':marketPrice(value.price))+' · '+chartTime(value.observed_at);
  for(const item of data.items){
   const row=document.createElement('tr'),detail=document.createElement('details'),title=document.createElement('summary'),pre=document.createElement('pre');title.textContent='Show measurements';pre.textContent=JSON.stringify(item,null,2);pre.style.whiteSpace='pre-wrap';detail.append(title,pre);
   const c=item.comparison;
   const cells=[chartTime(item.bar)+' · '+item.symbol,sample(item.at25),sample(item.at30),c.status+(c.early_price_advantage_pct==null?'':' · :25 price advantage '+pct(c.early_price_advantage_pct)),':25 '+pct(c.move_to_hour_close_pct.at25)+' / :30 '+pct(c.move_to_hour_close_pct.at30),detail];
   for(const value of cells){const cell=document.createElement('td');if(value instanceof Node)cell.append(value);else cell.textContent=value;row.append(cell)}$('deltaTimingRows').append(row);
  }
  $('deltaTimingPage').textContent='Page '+(Math.floor(deltaTimingOffset/50)+1)+' of '+Math.max(1,Math.ceil(data.total/50));$('deltaTimingPrevious').disabled=deltaTimingOffset===0;$('deltaTimingNext').disabled=deltaTimingOffset+50>=data.total;
  $('deltaTimingStatus').textContent=(data.enabled?'ON':'OFF')+' · '+data.total+' retained coin/hour comparisons'+(data.collection_supported===false?' · Select Volume Delta · 1h · :30 Decision to collect':data.market_connected===false?' · Market worker not connected':'')+(data.latest_observed_at?' · Last sample '+chartTime(data.latest_observed_at):' · Waiting for future :25 and :30 samples')+(data.dropped?' · Dropped samples: '+data.dropped:'')+(data.error?' · Observation error: '+data.error:'')+' · No simulated P&L.';
 }catch(e){if(request===deltaTimingRequest)$('deltaTimingStatus').textContent='Comparison unavailable: '+e.message+' · Will retry while this panel is open.'}
 finally{if(request===deltaTimingRequest)deltaTimingLoading=false}
}
