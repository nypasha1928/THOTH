// Separate, bookmarkable PWA pages; navigation never changes trading settings.
const pageIds=['chart','positions','history','analysis','comparison','settings','security'];
let historyOffset=0,historyTotal=0,historyRequest=0;
function buildPages(){
 const grid=document.querySelector('.grid'),nav=document.createElement('nav');
 nav.className='page-nav';nav.setAttribute('aria-label','Trading desk pages');
 for(const [id,label] of [['chart','Chart'],['positions','Live positions'],['history','History'],['analysis','Analysis'],['comparison','Trailing comparison'],['settings','Settings'],['security','Security']]){
  const link=document.createElement('a');link.href='#'+id;link.textContent=label;link.dataset.pageLink=id;nav.append(link);
 }
 document.querySelector('main').prepend(nav);
 const pages=Object.fromEntries(pageIds.map(id=>{const page=document.createElement('section');page.id='page-'+id;page.dataset.page=id;page.hidden=true;return [id,page]}));
 const positions=$('positions').closest('section'),account=$('accountStatus').closest('section'),activity=$('events').closest('section'),settings=$('settings').closest('aside');
 pages.chart.append($('chartCard'),activity);
 pages.settings.append(settings,$('symbolPanel'));
 pages.positions.append(positions,account);
 pages.history.innerHTML='<section class="card"><h2>Closed trade history</h2><p class="note">Confirmed Bybit realized P&amp;L for THOTH-managed trades. History includes retained older records; previously pruned records are unavailable. Fees and funding follow Bybit’s closed-P&amp;L reporting. Opening times show recorded entry-order creation, not a separate fill timestamp.</p><label>Filter by symbol<input id="historySymbol" type="search" placeholder="ETHUSDT" aria-label="History symbol"></label><p id="tradeHistoryStatus" role="status"></p><div class="table-scroll"><table><thead><tr><th>Opened (New York)</th><th>Closed (New York)</th><th>Symbol</th><th>Side</th><th>Entry / quantity</th><th>Realized P&amp;L</th><th>Reason</th><th>Strategy</th></tr></thead><tbody id="historyRows"></tbody></table></div><div class="row"><button id="historyPrevious">← Newer</button><button id="historyRefresh">Refresh history</button><button id="historyNext">Older →</button></div><nav id="historyPageButtons" class="history-pages" aria-label="History pages"></nav><form id="historyJump" class="history-jump"><label>Page<input id="historyPageNumber" type="number" min="1" step="1" value="1" aria-label="History page number"></label><span id="historyPageCount"></span><button>Go</button><span class="note">50 trades per page</span></form></section>';
 pages.analysis.innerHTML='<section class="card"><h2>Trade analysis</h2><p class="note">Based only on THOTH’s stored, confirmed closed trades. Includes retained older S/R records; previously pruned records and open P&amp;L are excluded. Win rate includes breakeven trades in the total. P&amp;L follows Bybit closed-P&amp;L reporting.</p><div class="analysis-filters"><label>Coin<input id="analysisSymbol" type="search" placeholder="All coins or ETHUSDT" aria-label="Analysis symbol"></label><label>Strategy<select id="analysisStrategy"><option value="">All strategies</option><option value="sr_breakout">S/R Breakout</option><option value="sma_cross">SMA 7/200</option><option value="sma_7_50">SMA 7/50</option><option value="sr_sma_7_50">S/R + SMA 7/50</option><option value="sr_hourly_intrabar">S/R 1h → Exit 1m</option><option value="rsi_hourly_intrabar">RSI 1h → Exit 1m</option><option value="unknown">Unknown</option></select></label><button id="refreshAnalysis">Refresh analysis</button></div><p id="analysisStatus" role="status"></p><div id="analysisSummary" class="stats"></div><h3>Per coin</h3><div class="table-scroll"><table><thead><tr><th>Coin</th><th>Trades</th><th>Wins / losses / flat</th><th>Win rate</th><th>Realized P&amp;L</th><th>Average P&amp;L</th><th>Profit factor</th></tr></thead><tbody id="coinAnalysis"></tbody></table></div><h3>Strategy comparison per coin</h3><div class="table-scroll"><table><thead><tr><th>Coin</th><th>Strategy</th><th>Trades</th><th>Win rate</th><th>Realized P&amp;L</th><th>Profit factor</th></tr></thead><tbody id="strategyAnalysis"></tbody></table></div><p class="note">Profit factor = gross winning P&amp;L divided by absolute gross losing P&amp;L; shown as — when there are no losing trades. Small samples do not establish future performance.</p></section>';
 pages.comparison.innerHTML=comparisonPage();
 grid.classList.add('paged-desk');grid.replaceChildren(...pageIds.map(id=>pages[id]));
 bindComparison();
 $('historyPrevious').onclick=()=>{historyOffset=Math.max(0,historyOffset-50);loadHistory()};
 $('historyNext').onclick=()=>{historyOffset+=50;loadHistory()};
 $('historyRefresh').onclick=()=>{historyOffset=0;loadHistory()};
 $('historyJump').onsubmit=e=>{e.preventDefault();goHistoryPage(Number($('historyPageNumber').value))};
 let filterTimer;$('historySymbol').oninput=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{historyOffset=0;loadHistory()},300)};
 $('refreshAnalysis').onclick=loadAnalysis;$('analysisStrategy').onchange=loadAnalysis;
 let analysisTimer;$('analysisSymbol').oninput=()=>{clearTimeout(analysisTimer);analysisTimer=setTimeout(loadAnalysis,300)};
 showPage();
}
function showPage(){
 const requested=location.hash.slice(1),id=pageIds.includes(requested)?requested:'chart';
 for(const page of document.querySelectorAll('[data-page]'))page.hidden=page.dataset.page!==id;
 for(const link of document.querySelectorAll('[data-page-link]')){if(link.dataset.pageLink===id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current')}
 window.scrollTo({top:0});
 if(id==='history'&&state)loadHistory();
 if(id==='analysis'&&state)loadAnalysis();
 if(id==='comparison'&&state)loadComparison();
 if(id==='chart'&&state)draw();
 if(id==='security'&&typeof loadAccountSecurity==='function')loadAccountSecurity();
}
function goHistoryPage(page){const total=Math.max(1,Math.ceil(historyTotal/50));if(!Number.isInteger(page)||page<1||page>total)return;historyOffset=(page-1)*50;loadHistory();}
function renderHistoryPages(){
 const total=Math.max(1,Math.ceil(historyTotal/50)),current=Math.floor(historyOffset/50)+1,nav=$('historyPageButtons');nav.replaceChildren();
 const pages=new Set([1,total]);for(let page=Math.max(1,current-2);page<=Math.min(total,current+2);page++)pages.add(page);
 let last=0;for(const page of [...pages].sort((a,b)=>a-b)){
  if(last&&page>last+1){const gap=document.createElement('span');gap.textContent='…';nav.append(gap)}
  const button=document.createElement('button');button.type='button';button.textContent=page;button.setAttribute('aria-label','History page '+page);if(page===current)button.setAttribute('aria-current','page');button.onclick=()=>goHistoryPage(page);nav.append(button);last=page;
 }
 $('historyPageNumber').value=current;$('historyPageNumber').max=total;$('historyPageCount').textContent='of '+total;
}
async function loadHistory(){
 const request=++historyRequest,symbol=$('historySymbol').value.trim().toUpperCase();
 if(symbol&&!/^[A-Z0-9]{2,30}USDT$/.test(symbol)){$('tradeHistoryStatus').textContent='Enter the complete Bybit symbol, such as ETHUSDT.';$('historyRows').replaceChildren();return}
 $('tradeHistoryStatus').textContent='Loading confirmed closed trades…';
 try{
  const data=await api('live/history?limit=50&offset='+historyOffset+(symbol?'&symbol='+encodeURIComponent(symbol):''));if(request!==historyRequest)return;
  historyTotal=data.total;if(historyOffset>=historyTotal&&historyOffset>0){historyOffset=Math.max(0,(Math.ceil(historyTotal/50)-1)*50);loadHistory();return}$('historyRows').replaceChildren();
  for(const trade of data.items){const row=document.createElement('tr');row.classList.toggle('trade-loss',Number(trade.pnl)<0);row.classList.toggle('trade-win',Number(trade.pnl)>0);
   const symbolButton=document.createElement('button');symbolButton.type='button';symbolButton.textContent=trade.symbol;symbolButton.onclick=()=>openChartSymbol(trade.symbol);
   const reason=trade.reason==='Open P&L trailing stop'?'Combined Trailing Stop':/^SMA 7 crossed (below|above) SMA 50 \(intrabar exit\)$/.test(trade.reason||'')?'SMA 7/50 (EXIT)':trade.reason||'—';
   const cells=[tradeTimestamp(trade.opened_at),tradeTimestamp(trade.closed_at,false,false),symbolButton,trade.side||'—',trade.entry!=null?marketPrice(trade.entry)+' / '+trade.qty:'—',money(trade.pnl),reason,strategyName(trade.strategy)];
   for(const [index,value] of cells.entries()){const cell=document.createElement('td');if(index===6){cell.classList.add('reason-cell');cell.title=trade.reason||'';}if(index===5){if(Number(trade.pnl)<0)cell.classList.add('pnl-loss');else if(Number(trade.pnl)>0)cell.classList.add('pnl-win');}if(value instanceof Node)cell.append(value);else cell.textContent=value;row.append(cell)}$('historyRows').append(row);
  }
  $('tradeHistoryStatus').textContent=data.total?(historyOffset+1)+'–'+Math.min(historyOffset+data.items.length,data.total)+' of '+data.total+' closed trades':'No confirmed closed trades'+(symbol?' for '+symbol:'')+'.';
  renderHistoryPages();
  $('historyPrevious').disabled=historyOffset===0;$('historyNext').disabled=historyOffset+50>=data.total;
 }catch(e){if(request===historyRequest)$('tradeHistoryStatus').textContent='History unavailable: '+e.message}
}
window.addEventListener('hashchange',showPage);
buildPages();

let analysisRequest=0;
const strategyName=id=>id==='sma_7_50'?'SMA 7/50':id==='sr_sma_7_50'?'S/R + SMA 7/50':id==='rsi_hourly_intrabar'?'RSI 1h → Exit 1m':id==='sr_hourly_intrabar'?'S/R 1h → Exit 1m':id==='sma_cross'?'SMA 7/200':id==='sr_breakout'?'S/R Breakout':'Unknown';
async function loadAnalysis(){
 const request=++analysisRequest,symbol=$('analysisSymbol').value.trim().toUpperCase(),strategy=$('analysisStrategy').value;
 if(symbol&&!/^[A-Z0-9]{2,30}USDT$/.test(symbol)){$('analysisStatus').textContent='Enter a complete symbol such as ETHUSDT.';return}
 $('analysisStatus').textContent='Loading recorded trade analysis…';
 try{
  const params=new URLSearchParams();if(symbol)params.set('symbol',symbol);if(strategy)params.set('strategy',strategy);
  const data=await api('live/analysis?'+params);if(request!==analysisRequest)return;
  $('analysisSummary').replaceChildren();
  for(const [label,value] of [['Closed trades',data.totals.trades],['Realized P&L',money(data.totals.realized_pnl)],['Win rate',data.totals.win_rate==null?'—':data.totals.win_rate.toFixed(1)+'%']]){const card=document.createElement('div');card.className='card';const title=document.createElement('small'),number=document.createElement('div');title.textContent=label;number.textContent=value;number.className='value';if(label==='Realized P&L'){if(data.totals.realized_pnl<0)number.classList.add('pnl-loss');else if(data.totals.realized_pnl>0)number.classList.add('pnl-win');}card.append(title,number);$('analysisSummary').append(card)}
  const percent=value=>value==null?'—':value.toFixed(1)+'%',factor=value=>value==null?'—':value.toFixed(2);
  for(const [id,records,combined] of [['coinAnalysis',data.by_symbol,false],['strategyAnalysis',data.by_symbol_strategy,true]]){
   $(id).replaceChildren();for(const record of records){const row=document.createElement('tr');row.classList.toggle('trade-loss',record.realized_pnl<0);row.classList.toggle('trade-win',record.realized_pnl>0);
    const values=combined?[record.symbol,strategyName(record.strategy),record.trades,percent(record.win_rate),money(record.realized_pnl),factor(record.profit_factor)]:[record.symbol,record.trades,record.wins+' / '+record.losses+' / '+record.breakeven,percent(record.win_rate),money(record.realized_pnl),money(record.average_pnl),factor(record.profit_factor)];
    for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}$(id).append(row);
   }
  }
  $('analysisStatus').textContent=data.totals.trades?data.totals.trades+' confirmed closed trades in this selection':'No recorded closed trades in this selection.';
 }catch(e){if(request===analysisRequest)$('analysisStatus').textContent='Analysis unavailable: '+e.message}
}
