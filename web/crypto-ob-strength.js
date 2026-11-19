// A separate strategy and saved candidate list. Selection is draft-only until Save.
const cryptoStrengthStrategy='crypto_ob_strength_1h_close';
const cryptoSMAStrategy='crypto_ob_strength_sma200_closeonly';
const cryptoHoursStrategy='crypto_ob_strength_sma200_hours';
const cryptoHoursList='crypto_ob_hours_candidates';
const cryptoPresetFlag=()=> $('strategyLogic').value===cryptoHoursStrategy?'crypto_ob_hours_preset_applied':$('strategyLogic').value===cryptoSMAStrategy?'crypto_ob_sma200_preset_applied':'crypto_ob_exit_preset_applied';
const isCryptoStrength=()=>[cryptoStrengthStrategy,cryptoSMAStrategy,cryptoHoursStrategy].includes($('strategyLogic').value);
let cryptoExitStrengthData=null,cryptoHoursData=null;
const formatCryptoExitStrength=value=>Number(Number(value).toPrecision(3)).toLocaleString('en-US',{useGrouping:false,maximumFractionDigits:20});
const cryptoStrengthList='crypto_ob_candidates';
const cryptoStrengthOff=['sma_enabled','sma_7_50_filter_enabled','sma_exit_enabled','sma_ob_exit_enabled','trailing_enabled','coin_breakeven_enabled','take_profit_enabled'];
let cryptoStrengthData=null,cryptoStrengthLoading=false;
function resetCryptoStrengthLocks(){for(const input of document.querySelectorAll('[data-crypto-strength-lock]')){input.disabled=false;delete input.dataset.cryptoStrengthLock;}}
function syncCryptoStrengthSettings(){
 const active=isCryptoStrength(),hours=$('strategyLogic').value===cryptoHoursStrategy,relaxed=hours||$('strategyLogic').value===cryptoSMAStrategy;
 let section=$('cryptoStrengthSettings');
 if(!section){section=document.createElement('details');section.id='cryptoStrengthSettings';section.innerHTML='<summary>Crypto Order Block · Per-coin strengths</summary><p class="note">Research candidates, not proven optimal settings. CLOSED 1h formations only; breaks, S/R, RSI, SMA and volume filters are not used. Opposite qualifying formation closes, reconciles, then reverses subject to risk checks. Fixed $0.50 gross hard stop; fees/slippage can increase loss. Agreed preset: per-coin P&amp;L trailing ON at $5 profit, with a fixed $0.10 drop from its peak; combined trailing OFF. Save settings to apply. Trailing controls remain editable. Breakeven, price trailing, take profit and SMA exits remain off. Saved position size, leverage, Maximum Positions, account protections, schedules and cooldowns remain active. Study used $50 per position. One strategy/list runs at a time.</p><button type="button" id="applyCryptoExitPreset">Apply agreed stop / trailing preset</button><p id="cryptoExitPresetStatus" class="note" role="status"></p><div id="cryptoStrengthValues"></div>';$('fields').append(section);}
 if($('applyCryptoExitPreset')){$('applyCryptoExitPreset').onclick=()=>applyCryptoStrengthExitPresetDraft();$('applyCryptoExitPreset').disabled=symbolsLocked();}
 section.hidden=!active;
 if(!active)return;
 section.open=true;
 section.querySelector('summary').textContent=relaxed?'Crypto OB · SMA200 · Entry / exit strengths':'Crypto Order Block · Per-coin strengths';
 if(!section.dataset.originalNote)section.dataset.originalNote=section.querySelector('p').textContent;
 section.querySelector('p').textContent=relaxed?'CLOSED 1h formed Order Blocks only. Entry needs the original per-coin strength and close above SMA200 for Long or below for Short. Equal price or fewer than 200 closed candles: no entry. Opposite formation closes only at 32/33 of entry strength (3.0303% lower); SMA200 does not block exits. No reversal from the exit signal. Price crossing SMA200 alone does not exit. GRT: entry 0.00033 / exit 0.00032. Fixed $0.50 hard stop; preset per-coin trailing $5 trigger / $0.10 fixed drop, combined OFF. Per-coin breakeven is optional and editable; its saved trigger and action apply when enabled. Saved sizing and account protections remain active. Pause entries before saving. Switching from the original Crypto OB Strength profile keeps protected open positions and their hard stops; pending entries or closes must settle first. Fresh opposite hourly formations use the lower exit strength. These thresholds are research settings, not a profit guarantee.':section.dataset.originalNote;
 if(hours)section.querySelector('p').textContent='Separate 50-coin research profile. CLOSED 1h OB + SMA200 entry direction; opposite CLOSED 1h formation at 32/33 strength closes only. New entries blocked every day 05–06, 08–09, 10–11, noon–13, 21–22, 23–midnight New York, independently of the general trading-window switch. Exits, stops and trailing always remain active. Preset: $50 position value, maximum 10 positions, $0.50 gross stop, per-coin trailing $2.50 trigger / $0.10 fixed drop; breakeven and combined OFF. Saved leverage and account protections remain active. Two research minima are zero: no strength restriction for those coins. These hours were selected retrospectively; future profitability is unproven. Save settings to apply; this does not enable orders. Switching from the current SMA200 OB profile can keep protected positions and their original stops; pending entries/closes must settle. Unrelated strategy changes require a flat account.';
 const lock=(id,value,checkbox=false)=>{const input=$(id);if(!input)return;if(checkbox)input.checked=value;else input.value=value;input.disabled=true;input.dataset.cryptoStrengthLock='1';};
 lock('strategyTf','60');lock('oppositeAction',relaxed?'close_only':'reverse');lock('stop_dollars',.5);
 for(const id of cryptoStrengthOff){if(relaxed&&!hours&&id==='coin_breakeven_enabled')continue;lock(id,false,true);}
 if($('rsiSettings'))$('rsiSettings').hidden=true;
 $('strategyDescription').textContent=relaxed?'Crypto OB · CLOSED 1h · SMA200 entry filter · Opposite exit minimum 3.03% lower (32/33), close only. Settings table lists each coin’s entry and exit strengths. $0.50 stop; preset per-coin trailing $5 trigger / $0.10 drop, combined OFF. Save to apply; does not enable orders.':'Crypto Order Block · CLOSED 1h · per-coin raw strength · $0.50 stop · opposite formation reversal. Select Crypto · OB Strength Candidates 15 (or a subset). No RSI, S/R or breaks. Agreed per-coin trailing: trigger $5 / fixed drop $0.10; combined OFF. Save to apply; trailing controls remain editable. Account limits remain active; this does not enable orders.';
 if(hours)$('strategyDescription').textContent='CLOSED 1h OB / SMA200 · 50 research candidates · Six fixed New York entry-hour exclusions: 05, 08, 10, 12, 21, 23. Exits stay active. Preset: $0.50 stop; per-coin trailing $2.50 / $0.10; breakeven and combined OFF. Save to apply; does not enable orders.';
 const thresholds=hours?cryptoHoursData?.entry_thresholds:cryptoStrengthData;
 if(thresholds){
  const box=$('cryptoStrengthValues');box.replaceChildren();
  const table=document.createElement('table');const caption=document.createElement('caption');caption.textContent='Minimum block high − low, in raw quote-price units'+(relaxed?'. Exit values rounded for display only; trading uses full precision.':'');table.append(caption);
  const header=document.createElement('tr');for(const title of (relaxed?['Coin','Entry minimum','Opposite exit minimum · 3.03% lower']:['Coin','Entry minimum'])){const cell=document.createElement('th');cell.scope='col';cell.textContent=title;header.append(cell);}table.append(header);
  for(const [symbol,width]of Object.entries(thresholds)){
   const row=document.createElement('tr'),exit=(hours?cryptoHoursData?.exit_thresholds:cryptoExitStrengthData)?.[symbol];
   const values=relaxed?[symbol,String(width),exit!=null?formatCryptoExitStrength(exit):'Unavailable']:[symbol,String(width)];
   values.forEach((value,index)=>{const cell=document.createElement('td');cell.textContent=value;if(relaxed&&index===2&&exit!=null){cell.title='Exact exit minimum: '+String(exit);if(Number(value)!==exit)cell.textContent='≈ '+value;}row.append(cell);});
   table.append(row);
  }box.append(table);
 }else if(!cryptoStrengthLoading){
  cryptoStrengthLoading=true;$('cryptoStrengthValues').textContent='Loading per-coin thresholds…';
  api('live/market-leaders').then(data=>{cryptoStrengthData=data.crypto_ob_strengths;cryptoHoursData=data.crypto_ob_hours;cryptoExitStrengthData=data.crypto_ob_sma200?.exit_thresholds;cryptoStrengthLoading=false;if(cryptoStrengthData)syncCryptoStrengthSettings();else $('cryptoStrengthValues').textContent='Threshold catalogue unavailable; refresh after deploying the matching server update.';}).catch(error=>{cryptoStrengthLoading=false;$('cryptoStrengthValues').textContent='Could not load thresholds: '+error.message;});
 }
}
$('strategyLogic').addEventListener('change',()=>{
 if(!isCryptoStrength()||symbolsLocked()||!formDraft)return;
 formDraft.saved_symbol_lists[formDraft.active_symbol_list]=[...formDraft.symbols];
 const target=$('strategyLogic').value===cryptoHoursStrategy?cryptoHoursList:cryptoStrengthList;
 formDraft.active_symbol_list=target;
 formDraft.symbols=[...(formDraft.saved_symbol_lists[target]||[])];
 dirty=true;renderSymbolLists();renderWatchlist();renderScope();renderStrategyDescription();applyCryptoStrengthExitPresetDraft();
});

function applyCryptoStrengthExitPresetDraft(){
 if(symbolsLocked()||!formDraft||!isCryptoStrength())return;
 $('coin_pnl_trailing_enabled').checked=true;
 const hours=$('strategyLogic').value===cryptoHoursStrategy;
 $('coin_pnl_trailing_activation_dollars').value=hours?2.5:5;
 if(hours){$('position_value').value=50;$('max_positions').value=10;}
 $('coin_pnl_trailing_drop_dollars').value=.1;
 $('pnl_trailing_enabled').checked=false;
 if($('strategyLogic').value!==cryptoSMAStrategy)$('coin_breakeven_enabled').checked=false;
 formDraft[cryptoPresetFlag()]=true;dirty=true;
 $('cryptoExitPresetStatus').textContent=hours?'Draft: $50 position value; maximum 10; $0.50 stop; per-coin trailing $2.50 / $0.10; breakeven and combined OFF. Save to apply.':'Draft: $0.50 stop; per-coin trailing ON · trigger $5.00 · fixed drop $0.10; combined OFF. Save settings to apply.';
}
function stageCryptoStrengthExitPreset(){
 if(formDraft&&isCryptoStrength()&&!formDraft[cryptoPresetFlag()]){
  if(symbolsLocked()){$('cryptoExitPresetStatus').textContent='Pause entries, then save settings to apply the selected stop / trailing preset.';return;}
  applyCryptoStrengthExitPresetDraft();
 }
}
