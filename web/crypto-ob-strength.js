// A separate strategy and saved candidate list. Selection is draft-only until Save.
const cryptoStrengthStrategy='crypto_ob_strength_1h_close';
const cryptoStrengthList='crypto_ob_candidates';
const cryptoStrengthOff=['sma_enabled','sma_7_50_filter_enabled','sma_exit_enabled','sma_ob_exit_enabled','trailing_enabled','coin_breakeven_enabled','take_profit_enabled'];
let cryptoStrengthData=null,cryptoStrengthLoading=false;
function resetCryptoStrengthLocks(){for(const input of document.querySelectorAll('[data-crypto-strength-lock]')){input.disabled=false;delete input.dataset.cryptoStrengthLock;}}
function syncCryptoStrengthSettings(){
 const active=$('strategyLogic').value===cryptoStrengthStrategy;
 let section=$('cryptoStrengthSettings');
 if(!section){section=document.createElement('details');section.id='cryptoStrengthSettings';section.innerHTML='<summary>Crypto Order Block · Per-coin strengths</summary><p class="note">Research candidates, not proven optimal settings. CLOSED 1h formations only; breaks, S/R, RSI, SMA and volume filters are not used. Opposite qualifying formation closes, reconciles, then reverses subject to risk checks. Fixed $0.75 gross hard stop; fees/slippage can increase loss. Combined Open P&amp;L trailing and per-coin P&amp;L trailing are optional settings below. Neither is enabled automatically. Breakeven, price trailing, take profit and SMA exits remain off. Saved position size, leverage, Maximum Positions, account protections, schedules and cooldowns remain active. Study used $50 per position. One strategy/list runs at a time.</p><div id="cryptoStrengthValues"></div>';$('fields').append(section);}
 section.hidden=!active;
 if(!active)return;
 section.open=true;
 const lock=(id,value,checkbox=false)=>{const input=$(id);if(!input)return;if(checkbox)input.checked=value;else input.value=value;input.disabled=true;input.dataset.cryptoStrengthLock='1';};
 lock('strategyTf','60');lock('oppositeAction','reverse');lock('stop_dollars',.75);
 for(const id of cryptoStrengthOff)lock(id,false,true);
 if($('rsiSettings'))$('rsiSettings').hidden=true;
 $('strategyDescription').textContent='Crypto Order Block · CLOSED 1h · per-coin raw strength · $0.75 stop · opposite formation reversal. Select Crypto · OB Strength Candidates 15 (or a subset). No RSI, S/R or breaks. Optional combined and per-coin P&L trailing are available below. Account limits remain active; this does not enable orders.';
 if(cryptoStrengthData){
  const box=$('cryptoStrengthValues');box.replaceChildren();
  const table=document.createElement('table');const caption=document.createElement('caption');caption.textContent='Minimum block high − low, in raw quote-price units';table.append(caption);
  for(const [symbol,width]of Object.entries(cryptoStrengthData)){const row=document.createElement('tr');for(const value of [symbol,String(width)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);}box.append(table);
 }else if(!cryptoStrengthLoading){
  cryptoStrengthLoading=true;$('cryptoStrengthValues').textContent='Loading per-coin thresholds…';
  api('live/market-leaders').then(data=>{cryptoStrengthData=data.crypto_ob_strengths;cryptoStrengthLoading=false;if(cryptoStrengthData)syncCryptoStrengthSettings();else $('cryptoStrengthValues').textContent='Threshold catalogue unavailable; refresh after deploying the matching server update.';}).catch(error=>{cryptoStrengthLoading=false;$('cryptoStrengthValues').textContent='Could not load thresholds: '+error.message;});
 }
}
$('strategyLogic').addEventListener('change',()=>{
 if($('strategyLogic').value!==cryptoStrengthStrategy||symbolsLocked()||!formDraft)return;
 formDraft.saved_symbol_lists[formDraft.active_symbol_list]=[...formDraft.symbols];
 formDraft.active_symbol_list=cryptoStrengthList;
 formDraft.symbols=[...(formDraft.saved_symbol_lists[cryptoStrengthList]||[])];
 dirty=true;renderSymbolLists();renderWatchlist();renderScope();renderStrategyDescription();
});
