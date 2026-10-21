// Indicator/entry calculations are saved separately from device-local visibility.
const rsiControls=[
 ['source','RSI calculation source','select','close',['close','open','high','low','hlc3','ohlc4']],
 ['rsi_length','RSI length','number',14,2,100],['smooth','Smooth RSI','checkbox',true],
 ['ma_type','RSI smoothing type','select','Ema',['SMA','Hull','Ema','Wma','Dema','RMA','LINREG','TEMA','ALMA','T3']],
 ['smooth_period','RSI smoothing period','number',4,1,100],['alma_sigma','ALMA sigma','number',6,1,100],
 ['max_data','ML history points (plus current)','number',3000,50,3000],['max_iterations','Maximum ML clustering steps','number',1000,1,1000],
 ['static_signals','Trade static RSI 30/70 signals','checkbox',true],['ml_signals','Trade corrected ML RSI signals','checkbox',true],
 ['volume_filter','RSI volume filter','checkbox',true],['volume_period','RSI volume MA period','number',20,1,500],['volume_multiplier','RSI volume multiplier','number',1,.5,5,.1],['volume_for_static','Apply RSI volume filter to static signals','checkbox',true],
 ['vwap_filter','RSI VWAP filter','checkbox',true],['vwap_source','RSI VWAP source','select','hlc3',['close','open','high','low','hlc3','ohlc4']],['vwap_anchor','RSI VWAP anchor (UTC)','select','D',['1','3','5','15','30','60','240','D','W','M']],
 ['sma_filter','RSI SMA alignment filter','checkbox',true],['sma_fast','RSI fast SMA','number',7,1,500],['sma_slow','RSI slow SMA','number',50,1,500],
 ['histogram_filter','RSI MACD histogram filter','checkbox',false],['macd_fast','RSI MACD fast length','number',12,1,500],['macd_slow','RSI MACD slow length','number',26,1,500],['macd_signal','RSI MACD signal length','number',9,1,500],['bull_hist_positive','RSI bullish: positive MACD histogram','checkbox',true],['bear_hist_negative','RSI bearish: negative MACD histogram','checkbox',true]
];
function renderRSISettings(config){
 let section=$('rsiSettings');if(!section){section=document.createElement('details');section.id='rsiSettings';$('settingsGroup').insertBefore(section,$('settingsGroup').lastElementChild)}
 section.replaceChildren();const title=document.createElement('summary');title.textContent='Corrected ML RSI settings';section.append(title);
 const note=document.createElement('p');note.className='note';note.textContent='Corrected arrays, rolling ML history, ordered thresholds and retained empty-cluster centers. Defaults match the supplied RSI/EMA/filter inputs; TradingView parity remains unverified. Display controls are in chart Appearance and never change trading. These filters apply only to RSI entries.';section.append(note);
 for(const [id,label,type,fallback,min,max,step] of rsiControls){const row=document.createElement('label'),input=document.createElement(type==='select'?'select':'input');row.append(document.createTextNode(label));input.id='rsi_'+id;
  if(type==='select'){for(const value of min){const option=document.createElement('option');option.value=value;option.textContent=value;input.append(option)}}else input.type=type;
  if(type==='checkbox')input.checked=config[id]??fallback;else input.value=config[id]??fallback;
  if(type==='number'){input.min=min;input.max=max;input.step=step||1}row.append(input);section.append(row);
 }
 section.open=$('strategyLogic').value==='rsi_hourly_intrabar';
}
function collectRSISettings(){return Object.fromEntries(rsiControls.map(([id,,type])=>[id,type==='checkbox'?$('rsi_'+id).checked:type==='number'?Number($('rsi_'+id).value):$('rsi_'+id).value]))}
