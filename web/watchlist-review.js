// Read-only report: never saves settings or submits orders.
function watchlistReviewMarkup(){return '<section class="card"><h2>Watchlist review</h2><p>Review every saved trading coin before choosing a smaller list. Uses retained records, not a fixed two-week window. Current strategy separates today’s logic from earlier experiments. No automatic exclusions or settings changes.</p><label>Results scope<select id="watchlistScope"><option value="current">Current strategy</option><option value="all">All retained strategies</option></select></label><button type="button" id="watchlistRefresh">Refresh review</button> <button type="button" id="watchlistCopy" disabled>Copy review report</button><p id="watchlistStatus" role="status"></p><p id="watchlistLimits" class="note"></p><div class="table-scroll" style="max-height:420px;overflow:auto"><table><thead><tr><th>Coin</th><th>Closed trades</th><th>Wins / losses</th><th>Realized P&amp;L</th><th>Signal events / skipped</th><th>Bybit rejects: orders / events</th><th>Entry check failures</th><th>Median check gap (timed entries)</th><th>Latest price-check gap</th><th>Median qualified → submit</th><th>Latest speed (%/min)</th><th>Review</th><th>Details</th></tr></thead><tbody id="watchlistRows"></tbody></table></div><textarea id="watchlistFallback" hidden readonly aria-label="Watchlist review report for manual copying" style="width:100%;min-height:180px"></textarea></section>'}
let watchlistReport=null,watchlistRequest=0;
function bindWatchlistReview(){
 $('watchlistRefresh').onclick=loadWatchlistReview;$('watchlistScope').onchange=loadWatchlistReview;
 $('watchlistCopy').onclick=async()=>{
  if(!watchlistReport)return;
  const report=JSON.stringify({report:'THOTH Watchlist review',...watchlistReport},null,2);
  try{await navigator.clipboard.writeText(report);$('watchlistStatus').textContent='Review report copied. Paste it into the chat.'}
  catch{const box=$('watchlistFallback');box.value=report;box.hidden=false;box.focus();box.select();$('watchlistStatus').textContent='Clipboard unavailable. Copy the selected report below.'}
 };
}
async function loadWatchlistReview(){
 const request=++watchlistRequest;watchlistReport=null;$('watchlistCopy').disabled=true;$('watchlistRows').replaceChildren();$('watchlistFallback').hidden=true;$('watchlistFallback').value='';$('watchlistStatus').textContent='Loading saved watchlist review…';
 try{
  const data=await api('live/watchlist-review?scope='+$('watchlistScope').value);if(request!==watchlistRequest)return;
  watchlistReport=data;
  const seconds=v=>Number.isFinite(v)?v.toFixed(1)+'s':'Not recorded';
  for(const item of data.items){
   const row=document.createElement('tr'),details=document.createElement('details'),heading=document.createElement('summary'),pre=document.createElement('pre');heading.textContent='Reasons / dates';pre.textContent=JSON.stringify(item,null,2);pre.style.whiteSpace='pre-wrap';details.append(heading,pre);
   const values=[item.symbol,item.trades,item.wins+' / '+item.losses,money(item.realized_pnl),item.signal_events+' / '+item.skipped_events,item.rejected_orders+' / '+item.rejection_events,item.entry_check_failures,seconds(item.median_check_gap_seconds),item.latest_speed?seconds(item.latest_speed.gap_seconds):'Not available',seconds(item.median_qualified_to_submit_seconds),item.latest_speed?item.latest_speed.speed_pct_per_minute.toFixed(3):'Not available',item.review_note,details];
   for(const value of values){const cell=document.createElement('td');if(value instanceof Node)cell.append(value);else cell.textContent=value;row.append(cell)}$('watchlistRows').append(row);
  }
  $('watchlistLimits').textContent=data.limitations;
  $('watchlistStatus').textContent=data.selected+' saved coins · '+data.traded+' traded · '+data.closed_trades+' closed trades · Realized P&L '+money(data.realized_pnl)+' · '+(data.scope==='all'?'All retained strategies':strategyName(data.strategy));$('watchlistCopy').disabled=false;
 }catch(error){if(request===watchlistRequest)$('watchlistStatus').textContent='Review unavailable: '+error.message}
}
