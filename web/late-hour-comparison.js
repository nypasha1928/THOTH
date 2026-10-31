// Separate observational endpoints only; never calls trading settings or orders.
let lateHourOffset=0,lateHourReport=null,lateHourRequest=0,lateHourLoading=false;
function lateHourMarkup(){return `<details class="card" id="lateHourPanel"><summary>Last 5 Minutes · Comparison · <span id="lateHourBadge">ON by default</span></summary>
<p>Runs alongside the selected standalone 1h Bar Close or Early :55–:00 strategy, on its saved coins. Evaluates forming hourly candles at :55–:00 using existing downloads. No orders, simulated fills or hypothetical P&amp;L. Account limits, positions and cooldowns are not evaluated.</p>
<p>Bar Close comparison defaults: opposing wick ≤30%; boundary clearance 0.05–0.50 ATR14. The Early profile uses its saved qualification limits; OB thickness filter OFF. Order Block formations/breaks OR Major S/R breakouts. Conflicting directions skipped. First passing decision per coin/hour retained; actual fill before :00 is not guaranteed.</p>
<label><input type="checkbox" id="lateHourEnabled" checked> Enable signal comparison only</label>
<div class="row"><button type="button" id="lateHourRefresh">Refresh comparison</button><button type="button" id="lateHourCopy">Copy comparison</button></div><p id="lateHourStatus" role="status"></p>
<div class="table-scroll"><table><thead><tr><th>Hourly candle starts (New York)</th><th>Coin</th><th>Early decision / observed</th><th>After hour close</th><th>Linked LIVE order</th><th>Measurements / reasons</th></tr></thead><tbody id="lateHourRows"></tbody></table></div>
<div class="row"><button type="button" id="lateHourPrevious">← Newer</button><span id="lateHourPage"></span><button type="button" id="lateHourNext">Older →</button></div>
<textarea id="lateHourCopyFallback" readonly hidden aria-label="Comparison report to copy"></textarea></details>`}
function bindLateHourComparison(){
 $('lateHourRefresh').onclick=()=>{lateHourOffset=0;loadLateHourComparison()};
 $('lateHourPrevious').onclick=()=>{lateHourOffset=Math.max(0,lateHourOffset-50);loadLateHourComparison()};
 $('lateHourNext').onclick=()=>{lateHourOffset+=50;loadLateHourComparison()};
 $('lateHourEnabled').onchange=async()=>{const enabled=$('lateHourEnabled').checked;lateHourRequest++;$('lateHourEnabled').disabled=true;try{await api('live/late-hour-comparison/settings','PUT',{enabled});await loadLateHourComparison()}catch(error){$('lateHourEnabled').checked=!enabled;$('lateHourStatus').textContent=error.message}finally{$('lateHourEnabled').disabled=false;lateHourLoading=false}};
 $('lateHourCopy').onclick=async()=>{const text=JSON.stringify(lateHourReport,null,2);try{await navigator.clipboard.writeText(text);$('lateHourStatus').textContent='Comparison copied. No trading settings changed.'}catch{const box=$('lateHourCopyFallback');box.value=text;box.hidden=false;box.focus();box.select()}};
 $('lateHourPanel').ontoggle=()=>{if($('lateHourPanel').open)loadLateHourComparison({background:true})};
}
async function loadLateHourComparison({background=false}={}){
 if(background&&lateHourLoading)return;
 lateHourLoading=true;
 const request=++lateHourRequest;
 try{
  const data=await api('live/late-hour-comparison?limit=50&offset='+lateHourOffset);if(request!==lateHourRequest)return;lateHourReport=data;
  $('lateHourEnabled').checked=data.enabled;$('lateHourBadge').textContent=data.enabled?'ON · signal only':'OFF';$('lateHourRows').replaceChildren();
  for(const item of data.items){
   const row=document.createElement('tr'),chosen=item.first_pass||item.latest;
   const confirmation=item.closed?(item.closed.conflict?'Conflicting closed signals':item.closed.signal?'Raw closed signal: '+item.closed.signal+(item.first_pass?(item.closed.same_direction?' · same direction':' · changed direction'):''):'No confirmed signal'):'Not yet verified';
   const order=(item.live_orders||[]).map(o=>(o.side==='Buy'?'Long':'Short')+' · '+o.phase).join('; ')||(data.matching_error||'No linked order recorded');
   const detail=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('p');summary.textContent='Show comparison details';text.style.whiteSpace='pre-line';
   const measurements=chosen.metrics?['Observed price: '+chosen.price,'ATR14: '+chosen.metrics.atr14,'Opposing wick: '+chosen.metrics.opposing_wick_pct.toFixed(2)+'%']:[];
   text.textContent=['Strategy: '+strategyName(item.strategy||'order_block_sr_1h_close'),...measurements,...((chosen.source_checks||[]).map(c=>c.label+' · '+c.distance_atr.toFixed(3)+' ATR · '+(c.passed?'passes':c.reasons.join('; ')))),
    'Latest observation: '+item.latest.reason,'Same source types at close: '+(item.closed?.same_sources?.join(', ')||'not verified / none')].join('\n');detail.append(summary,text);
   const start=document.createElement('span');start.append(tradeTimestamp(item.bar));const observed=document.createElement('div');observed.append(document.createTextNode(item.decision+' · '),tradeTimestamp(item.observed_at));
   for(const value of [start,item.symbol,observed,confirmation,order,detail]){const cell=document.createElement('td');if(value instanceof Node)cell.append(value);else cell.textContent=value;row.append(cell)}$('lateHourRows').append(row);
  }
  const nextWindow=data.window?.next_window_at?new Date(data.window.next_window_at*1000).toLocaleTimeString('en-US',{timeZone:'America/New_York',timeZoneName:'short'}):null;
  const collectionText=data.collection_supported===false?' · Collection paused: select standalone 1h Bar Close or Early :55–:00':data.market_connected===false?' · Collection paused: market feed disconnected':data.feed_status==='degraded'?' · Market feed degraded; some coins may be missing':'';
  const lastSample=data.latest_observed_at?' · Last observation '+chartTime(data.latest_observed_at):'';
  const windowText=!data.window?'':data.window.state==='entry_window_open'?' · :55–:00 observation window OPEN':data.window.state==='closed_hour_matching'?' · Checking closed-hour matches; next observation window '+nextWindow:' · Waiting for :55; next observation window '+nextWindow;
  $('lateHourStatus').textContent=(data.enabled?'ON':'OFF')+' · '+data.total+' retained coin/hour comparisons'+(data.selected_strategy?' · Selected: '+strategyName(data.selected_strategy):'')+collectionText+lastSample+windowText+' · '+(data.items.length?'No orders placed by this panel.':'Awaiting :55–:00 samples while standalone 1h Bar Close or Early :55–:00 is selected and the market feed is connected.')+(data.dropped?' · '+data.dropped+' samples dropped':'')+(data.error?' · Comparison error: '+data.error:'');
  $('lateHourPage').textContent='Page '+(Math.floor(lateHourOffset/50)+1)+' of '+Math.max(1,Math.ceil(data.total/50));$('lateHourPrevious').disabled=lateHourOffset===0;$('lateHourNext').disabled=lateHourOffset+50>=data.total;
 }catch(error){if(request===lateHourRequest)$('lateHourStatus').textContent='Comparison unavailable: '+error.message+' · Will retry automatically while this panel is open.'}
 finally{if(request===lateHourRequest)lateHourLoading=false}
}
