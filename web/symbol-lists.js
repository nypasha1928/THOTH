// Named saved selections share one account runner. Loading is draft-only until Save.
function inActiveSymbolCatalogue(item){if(!formDraft)return true;const group=formDraft.active_symbol_list;if(group.endsWith('_leaders'))return formDraft.symbols.includes(item.symbol)||(formDraft.saved_symbol_lists[group]||[]).includes(item.symbol);return group==='tradfi'?item.asset_group==='tradfi':item.asset_group!=='tradfi'||(formDraft.saved_symbol_lists.main||[]).includes(item.symbol)}
function renderSymbolLists(){
 let box=$('namedSymbolLists');
 if(!box){
  box=document.createElement('section');box.id='namedSymbolLists';
  box.innerHTML='<label>Trading symbol list<select id="activeSymbolList"><option value="main">Main · existing selection</option><option value="tradfi">TradFi · USDT perpetuals</option><option value="main_leaders">Main · Market Leaders 50</option><option value="tradfi_leaders">TradFi · Market Leaders 50</option></select></label><p class="note">Each list keeps its own saved selection. Loading changes the draft only; save strategy &amp; trading symbols to apply. One list and one strategy trade at a time. For TradFi select Order Block + S/R · 1h · Volume MA. Exchange availability, verification, leverage and order size are checked separately; inclusion is not a trading guarantee.</p><p class="note">Market Leaders are curated starter selections, not verified live market-cap or volume rankings. TradFi starter: 35 stocks, 8 ETFs, 4 commodities, 3 forex. No automatic rebalancing.</p><button type="button" id="loadMarketLeaders" hidden>Load starter 50 into draft</button><p id="marketLeadersStatus" role="status"></p>';
  $('symbolPanel').prepend(box);
  $('activeSymbolList').onchange=()=>{
   if(symbolsLocked())return;
   const target=$('activeSymbolList').value;
   formDraft.saved_symbol_lists[formDraft.active_symbol_list]=[...formDraft.symbols];
   formDraft.active_symbol_list=target;formDraft.symbols=[...(formDraft.saved_symbol_lists[target]||[])];
   dirty=true;$('search').value='';renderWatchlist();renderScope();syncSymbolListLock();
  };
  $('loadMarketLeaders').onclick=loadMarketLeaderDraft;
 }
 $('activeSymbolList').value=formDraft.active_symbol_list;syncSymbolListLock();
}
function syncSymbolListLock(){if($('activeSymbolList'))$('activeSymbolList').disabled=symbolsLocked();if($('loadMarketLeaders')){$('loadMarketLeaders').hidden=!formDraft.active_symbol_list.endsWith('_leaders');$('loadMarketLeaders').disabled=symbolsLocked()}}

async function loadMarketLeaderDraft(){
 if(symbolsLocked())return;
 const group=formDraft.active_symbol_list;
 if(!group.endsWith('_leaders'))return;
 $('marketLeadersStatus').textContent='Loading curated starter selection…';
 try{
  const data=await api('live/market-leaders');if(symbolsLocked()||formDraft.active_symbol_list!==group)return;
  formDraft.symbols=[...data[group]];formDraft.saved_symbol_lists[group]=[...formDraft.symbols];
  dirty=true;renderWatchlist();renderScope();$('marketLeadersStatus').textContent='Loaded '+formDraft.symbols.length+' into this draft. Save to apply. '+data.basis;
 }catch(error){$('marketLeadersStatus').textContent='Starter list unavailable: '+error.message}
}
