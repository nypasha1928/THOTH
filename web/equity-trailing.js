const equityTrailingPeriods=[['daily','Daily'],['three_day','3-Day'],['weekly','Weekly']];
const equityTrailingFieldIds=['equity_trailing_resume_automatic',...equityTrailingPeriods.flatMap(([key])=>[key+'_equity_trailing_enabled',key+'_equity_trailing_drop_dollars'])];
function renderEquityTrailingSettings(config){
 const group=document.createElement('fieldset');group.id='equityTrailingSettings';const title=document.createElement('legend');title.textContent='Daily / 3-Day / Weekly account equity trailing';group.append(title);
 const note=document.createElement('p');note.className='note';note.textContent='Tracks total USDT account equity, including realized and open P&L. Each enabled control closes ALL managed positions after its fixed USDT drop from the highest verified equity, then blocks entries until that UTC period ends. Daily resets 00:00 UTC; Weekly Monday 00:00 UTC; 3-Day uses fixed UTC 72-hour calendar blocks; its exact window appears below when enabled. Deposits/withdrawals are excluded from gains/losses. Tracking begins at the first verified sample after enabling; earlier peaks are not reconstructed. After every triggered period ends, the resume option below can restore previously enabled entries on NEW signals if other risk checks pass. An operator pause or server restart cancels automatic resume. A triggered pause remains until period end even if its checkbox is turned off. These controls are separate from the existing daily loss limit and combined open P&L trailing.';group.append(note);
 for(const [key,label]of equityTrailingPeriods){group.append(field('Enable '+label+' equity trailing',key+'_equity_trailing_enabled',config[key+'_equity_trailing_enabled']??false,'checkbox'));group.append(field(label+' equity drop from peak (USDT)',key+'_equity_trailing_drop_dollars',config[key+'_equity_trailing_drop_dollars']??1,'number',{min:'.01',max:'1000000',step:'.01'}));}
 group.append(field('Resume previously enabled entries after all triggered equity periods end','equity_trailing_resume_automatic',config.equity_trailing_resume_automatic??true,'checkbox'));
 $('fields').append(group);
}
function renderEquityTrailingStatus(){
 const box=$('equityTrailingStatus');if(!box||!lv)return;
 const now=(Date.now()+riskClockOffset)/1000;
 const states=lv.equity_trailing||{};box.replaceChildren();
 for(const [key,label]of equityTrailingPeriods){
  const data=states[key]||{},enabled=lv.config[key+'_equity_trailing_enabled'],locked=data.triggered&&now<data.period_end;
  if(!enabled&&!locked)continue;
  const card=document.createElement('section');card.className='equity-period-trailing'+(locked?' equity-period-paused':'');card.dataset.equityPeriod=key;
  const title=document.createElement('strong');title.textContent=label+' equity trailing · '+(locked?'PAUSED UNTIL PERIOD END':data.period_end&&now>=data.period_end?'Awaiting verified period reset':data.peak!=null?'TRACKING':'Awaiting verified equity');card.append(title);
  const figures=document.createElement('p');figures.textContent=data.peak!=null?'Transfer-adjusted peak '+money(data.peak)+' · Equity close threshold '+money(data.stop_equity)+' · Remaining '+money(data.remaining)+' of '+money(data.drop)+' drop':'Configured equity drop: '+money(lv.config[key+'_equity_trailing_drop_dollars']);card.append(figures);
  if(data.period_end){const timing=document.createElement('p');timing.className='note';timing.textContent='UTC window: '+riskTimestamp(data.period_start*1000)+' → '+riskTimestamp(data.period_end*1000)+' · '+countdown(data.period_end)+' remaining';card.append(timing);}
  box.append(card);
 }
 box.hidden=!box.children.length;
}
