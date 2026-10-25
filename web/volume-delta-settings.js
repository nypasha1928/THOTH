function renderVolumeDeltaSettings(config){
 const section=document.createElement('details');section.id='volumeDeltaSettings';const title=document.createElement('summary');title.textContent='Volume Delta signal settings';section.append(title);
 const note=document.createElement('p');note.className='note';note.textContent='Uploaded Pine Volume Delta family only. Default 1-minute lower-timeframe candle-volume proxy; not aggressor trade delta. No VWAP divergence, standalone SMA crossover entry or SMA exit. Entry timeframe is selected above.';section.append(note);
 for(const [id,label,fallback] of [['sma_filter','Use SMA 7/50 entry alignment filter',true],['bullish','Enable bullish Volume Delta signals',true],['bearish','Enable bearish Volume Delta signals',true]])section.append(field(label,'vd_'+id,config[id]??fallback,'checkbox'));
 for(const [id,label] of [['bull_type','Bullish candle type'],['bear_type','Bearish candle type']]){
  const row=document.createElement('label');row.textContent=label;const select=document.createElement('select');select.id='vd_'+id;
  for(const [value,text] of [['strong','Strong (original default)'],['weak','Weak'],['all','All']])select.append(new Option(text,value));select.value=config[id]||'strong';row.append(select);section.append(row);
 }
 $('fields').append(section);syncVolumeDeltaSettings();
}
function syncVolumeDeltaSettings(){const section=$('volumeDeltaSettings');if(section){const active=$('strategyLogic').value==='volume_delta_intrabar';section.hidden=!active;section.open=active}}
function collectVolumeDeltaSettings(){return {sma_filter:$('vd_sma_filter').checked,bullish:$('vd_bullish').checked,bearish:$('vd_bearish').checked,bull_type:$('vd_bull_type').value,bear_type:$('vd_bear_type').value}}
