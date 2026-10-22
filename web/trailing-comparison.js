// Observation-only page: endpoints cannot activate execution or change live risk.
let comparisonOffset=0,comparisonDirty=false,comparisonRequest=0;
function comparisonPage(){return `<section class="card"><h2>Trailing comparison</h2><p class="banner">FIXED controls real exits · DYNAMIC is observation only. No simulated result sends an order.</p><p class="note">All positions are observed together. Each cycle freezes the position basket when fixed trailing is first observed active. After actual closure, public mark prices follow a virtual copy for up to 24 hours. Basket changes and missing prices are flagged. A simulation cannot prove you should have waited.</p><form id="comparisonSettings"><fieldset><legend>Dynamic observation settings — future cycles only</legend><label>Observe dynamic trailing<input id="observationEnabled" type="checkbox"></label><label>Minimum distance (USDT)<input id="observationMinimum" type="number" min="0.01" max="100000" step="0.01" required></label><label>Maximum distance (USDT)<input id="observationMaximum" type="number" min="0.01" max="100000" step="0.01" required></label><label>Volatility multiplier<input id="observationMultiplier" type="number" min="0.01" max="20" step="0.01" required></label><label>Assumed taker fee (basis points per side)<input id="observationFee" type="number" min="0" max="100" step="0.1" required></label><button>Save observation settings</button></fieldset></form><p class="note">Distance uses volatility of recent combined dollar P&amp;L changes, normalized to 5 seconds, over up to 60 observations. The first 8 returns use maximum distance while warming up. Once activated, the proposed closing threshold can only rise. Fee assumption is editable and may differ from your account. Funding is not modeled: estimates after fees are NOT net profit. The frozen-basket continuation compares trailing rules only; it does not model individual stops, SMA exits, liquidation or slippage. Realized P&amp;L uses Bybit’s confirmed closed-trade reporting.</p><p id="comparisonStatus" role="status"></p><div id="comparisonCycles"></div><div class="row"><button id="comparisonPrevious">← Newer</button><button id="comparisonRefresh">Refresh comparison</button><button id="comparisonNext">Older →</button></div></section>`}
function bindComparison(){
 $('comparisonSettings').oninput=()=>comparisonDirty=true;
 $('comparisonSettings').onsubmit=async e=>{e.preventDefault();try{
  const body={enabled:$('observationEnabled').checked,minimum:Number($('observationMinimum').value),maximum:Number($('observationMaximum').value),multiplier:Number($('observationMultiplier').value),fee_bps:Number($('observationFee').value)};
  await api('live/trailing-comparison/settings','PUT',body);comparisonDirty=false;await loadComparison();
 }catch(error){$('comparisonStatus').textContent='Observation settings not saved: '+error.message}};
 $('comparisonRefresh').onclick=loadComparison;
 $('comparisonPrevious').onclick=()=>{comparisonOffset=Math.max(0,comparisonOffset-50);loadComparison()};
 $('comparisonNext').onclick=()=>{comparisonOffset+=50;loadComparison()};
}
async function loadComparison(){
 const request=++comparisonRequest;
 try{const data=await api('live/trailing-comparison?limit=50&offset='+comparisonOffset);if(request!==comparisonRequest)return;
  if(!comparisonDirty){$('observationEnabled').checked=data.settings.enabled;for(const [id,key] of [['observationMinimum','minimum'],['observationMaximum','maximum'],['observationMultiplier','multiplier'],['observationFee','fee_bps']])$(id).value=data.settings[key]}
  $('comparisonStatus').textContent=(data.settings.enabled?'Dynamic observation enabled. ':'Observation paused. ')+(data.total?data.total+' recorded cycles. ':'Waiting for combined FIXED trailing activation; no historical results are invented. ')+(data.error||'')+(data.dropped?' · Dropped snapshots: '+data.dropped+'; comparison coverage may be incomplete.':'');
  $('comparisonCycles').replaceChildren();
  for(const cycle of data.items){const card=document.createElement('article');card.className='card comparison-cycle';const heading=document.createElement('h3');heading.textContent='Cycle '+cycle.id+' · '+cycle.position_count+' positions';card.append(heading,tradeTimestamp(cycle.started));
   const symbols=document.createElement('p');symbols.className='note';symbols.textContent=cycle.symbols.join(', ');card.append(symbols);
   const details=document.createElement('dl');const line=(label,value)=>{const title=document.createElement('dt'),content=document.createElement('dd');title.textContent=label;if(value instanceof Node)content.append(value);else content.textContent=value;details.append(title,content)};
   line('Actual fixed close requested',cycle.fixed_triggered?tradeTimestamp(cycle.fixed_triggered):'Not observed');
   line('Fixed trigger P&L (gross quote)',cycle.fixed_gross==null?'—':money(cycle.fixed_gross));
   line('Actual confirmed realized P&L',cycle.realized==null?'Pending / unavailable':money(cycle.realized));
   if(cycle.actual_closed_at)line('Actual positions settled',tradeTimestamp(cycle.actual_closed_at));
   const d=cycle.dynamic;
   line('Dynamic simulation',d.triggered?'Simulated exit reached':cycle.expired?'Expired after 24 hours':d.active?'Active virtual trailing': 'Waiting for simulated activation');
   line('Proposed distance',d.distance==null?'—':money(d.distance)+(d.warming_up?' · warming up':''));
   line('Virtual peak / protected threshold',d.threshold==null?'—':money(d.peak)+' / '+money(d.threshold));
   if(d.triggered){line('Simulated exit time',tradeTimestamp(d.triggered_at));line('Simulated gross result',money(d.result_gross));line('Assumed entry + exit fees',money(cycle.assumed_fees));line('Estimate after assumed fees, before funding',money(cycle.estimate_after_assumed_fees_before_funding));}
   line('Dynamic minus fixed trigger gross quote',cycle.gross_quote_difference==null?'Unavailable / incomplete':money(cycle.gross_quote_difference)+' · quote comparison, not actual net advantage');
   line('Last virtual price observation',cycle.last_sample?tradeTimestamp(cycle.last_sample):'—');card.append(details);
   const note=document.createElement('p');note.className='note';note.textContent=(cycle.warnings||[]).join(' · ')||'Sampled simulation only; no slippage or funding model. Matching gross quotes does not establish executable profit.';card.append(note);$('comparisonCycles').append(card);
  }
  $('comparisonPrevious').disabled=comparisonOffset===0;$('comparisonNext').disabled=comparisonOffset+50>=data.total;
 }catch(error){if(request===comparisonRequest)$('comparisonStatus').textContent='Comparison unavailable: '+error.message}
}
setInterval(()=>{if(location.hash==='#comparison'&&document.visibilityState==='visible')loadComparison()},15000);
