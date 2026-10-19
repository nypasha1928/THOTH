// Separate, bookmarkable PWA pages; navigation never changes trading settings.
const pageIds=['chart','positions','history','settings'];
let historyOffset=0,historyTotal=0,historyRequest=0;
function buildPages(){
 const grid=document.querySelector('.grid'),nav=document.createElement('nav');
 nav.className='page-nav';nav.setAttribute('aria-label','Trading desk pages');
 for(const [id,label] of [['chart','Chart'],['positions','Live positions'],['history','History'],['settings','Settings']]){
  const link=document.createElement('a');link.href='#'+id;link.textContent=label;link.dataset.pageLink=id;nav.append(link);
 }
 document.querySelector('main').prepend(nav);
 const pages=Object.fromEntries(pageIds.map(id=>{const page=document.createElement('section');page.id='page-'+id;page.dataset.page=id;page.hidden=true;return [id,page]}));
 const positions=$('positions').closest('section'),account=$('accountStatus').closest('section'),activity=$('events').closest('section'),settings=$('settings').closest('aside');
 pages.chart.append($('chartCard'),activity);
 pages.settings.append(settings,$('symbolPanel'));
 pages.positions.append(positions,account);
 pages.history.innerHTML='<section class="card"><h2>Closed trade history</h2><p class="note">Confirmed Bybit realized P&amp;L for THOTH-managed trades. History includes retained older records; previously pruned records are unavailable. Fees and funding follow Bybit’s closed-P&amp;L reporting.</p><label>Filter by symbol<input id="historySymbol" type="search" placeholder="ETHUSDT" aria-label="History symbol"></label><p id="tradeHistoryStatus" role="status"></p><div class="table-scroll"><table><thead><tr><th>Close confirmed</th><th>Symbol</th><th>Side</th><th>Entry / quantity</th><th>Realized P&amp;L</th><th>Reason</th><th>Strategy</th></tr></thead><tbody id="historyRows"></tbody></table></div><div class="row"><button id="historyPrevious">← Newer</button><button id="historyRefresh">Refresh history</button><button id="historyNext">Older →</button></div></section>';
 grid.classList.add('paged-desk');grid.replaceChildren(...pageIds.map(id=>pages[id]));
 $('historyPrevious').onclick=()=>{historyOffset=Math.max(0,historyOffset-50);loadHistory()};
 $('historyNext').onclick=()=>{historyOffset+=50;loadHistory()};
 $('historyRefresh').onclick=()=>{historyOffset=0;loadHistory()};
 let filterTimer;$('historySymbol').oninput=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{historyOffset=0;loadHistory()},300)};
 showPage();
}
function showPage(){
 const requested=location.hash.slice(1),id=pageIds.includes(requested)?requested:'chart';
 for(const page of document.querySelectorAll('[data-page]'))page.hidden=page.dataset.page!==id;
 for(const link of document.querySelectorAll('[data-page-link]')){if(link.dataset.pageLink===id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current')}
 window.scrollTo({top:0});
 if(id==='history'&&state)loadHistory();
 if(id==='chart'&&state)draw();
}
async function loadHistory(){
 const request=++historyRequest,symbol=$('historySymbol').value.trim().toUpperCase();
 if(symbol&&!/^[A-Z0-9]{2,30}USDT$/.test(symbol)){$('tradeHistoryStatus').textContent='Enter the complete Bybit symbol, such as ETHUSDT.';$('historyRows').replaceChildren();return}
 $('tradeHistoryStatus').textContent='Loading confirmed closed trades…';
 try{
  const data=await api('live/history?limit=50&offset='+historyOffset+(symbol?'&symbol='+encodeURIComponent(symbol):''));if(request!==historyRequest)return;
  historyTotal=data.total;$('historyRows').replaceChildren();
  for(const trade of data.items){const row=document.createElement('tr');row.classList.toggle('trade-loss',Number(trade.pnl)<0);
   const symbolButton=document.createElement('button');symbolButton.type='button';symbolButton.textContent=trade.symbol;symbolButton.onclick=()=>openChartSymbol(trade.symbol);
   const cells=[new Date(trade.closed_at*1000).toLocaleString(),symbolButton,trade.side||'—',trade.entry!=null?marketPrice(trade.entry)+' / '+trade.qty:'—',money(trade.pnl),trade.reason||'—',trade.strategy==='sma_cross'?'SMA 7/200':trade.strategy==='sr_breakout'?'S/R Breakout':'Legacy'];
   for(const [index,value] of cells.entries()){const cell=document.createElement('td');if(index===4&&Number(trade.pnl)<0)cell.classList.add('pnl-loss');if(value instanceof Node)cell.append(value);else cell.textContent=value;row.append(cell)}$('historyRows').append(row);
  }
  $('tradeHistoryStatus').textContent=data.total?(historyOffset+1)+'–'+Math.min(historyOffset+data.items.length,data.total)+' of '+data.total+' closed trades':'No confirmed closed trades'+(symbol?' for '+symbol:'')+'.';
  $('historyPrevious').disabled=historyOffset===0;$('historyNext').disabled=historyOffset+50>=data.total;
 }catch(e){if(request===historyRequest)$('tradeHistoryStatus').textContent='History unavailable: '+e.message}
}
window.addEventListener('hashchange',showPage);
buildPages();
