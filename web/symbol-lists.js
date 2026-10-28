// Named saved selections share one account runner. Loading is draft-only until Save.
function inActiveSymbolCatalogue(item){if(!formDraft)return true;return formDraft.active_symbol_list==='tradfi'?item.asset_group==='tradfi':item.asset_group!=='tradfi'||(formDraft.saved_symbol_lists.main||[]).includes(item.symbol)}
function renderSymbolLists(){
 let box=$('namedSymbolLists');
 if(!box){
  box=document.createElement('section');box.id='namedSymbolLists';
  box.innerHTML='<label>Trading symbol list<select id="activeSymbolList"><option value="main">Main · existing selection</option><option value="tradfi">TradFi · USDT perpetuals</option></select></label><p class="note">Each list keeps its own saved selection. Loading changes the draft only; save strategy &amp; trading symbols to apply. One list and one strategy trade at a time. For TradFi select Order Block + S/R · 1h · Volume MA. Exchange availability, verification, leverage and order size are checked separately; inclusion is not a trading guarantee.</p>';
  $('symbolPanel').prepend(box);
  $('activeSymbolList').onchange=()=>{
   if(symbolsLocked())return;
   const target=$('activeSymbolList').value;
   formDraft.saved_symbol_lists[formDraft.active_symbol_list]=[...formDraft.symbols];
   formDraft.active_symbol_list=target;formDraft.symbols=[...(formDraft.saved_symbol_lists[target]||[])];
   dirty=true;$('search').value='';renderWatchlist();renderScope();
  };
 }
 $('activeSymbolList').value=formDraft.active_symbol_list;syncSymbolListLock();
}
function syncSymbolListLock(){if($('activeSymbolList'))$('activeSymbolList').disabled=symbolsLocked()}
