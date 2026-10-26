// Indicator/entry calculations are saved separately from device-local visibility.
const rsiControls=[
 ['signal_rules','RSI entry crossing rules','select','original_six',['original_six','eight_crossings']],
 ['calculation_mode','RSI calculation version','select','fixed',['fixed','uploaded_pine','corrected']],
 ['fixed_long','Fixed Long RSI level','number',56.68,.01,99.99,.01],
 ['fixed_short','Fixed Short RSI level','number',42.38,.01,99.99,.01],
 ['source','RSI calculation source','select','close',['close','open','high','low','hlc3','ohlc4']],
 ['rsi_length','RSI length','number',14,2,100],['smooth','Smooth RSI','checkbox',true],
 ['ma_type','RSI smoothing type','select','Ema',['SMA','Hull','Ema','Wma','Dema','RMA','LINREG','TEMA','ALMA','T3']],
 ['smooth_period','RSI smoothing period','number',4,1,100],['alma_sigma','ALMA sigma','number',6,1,100],
 ['max_data','ML history points (plus current)','number',3000,50,3000],['max_iterations','Maximum ML clustering steps','number',1000,1,1000],
 ['static_signals','Trade static RSI 30/70 signals','checkbox',true],['ml_signals','Trade Long/Short threshold signals (fixed or ML)','checkbox',true],
 ['volume_filter','RSI volume filter','checkbox',false],['volume_period','RSI volume MA period','number',20,1,500],['volume_multiplier','RSI volume multiplier','number',1,.5,5,.1],['volume_for_static','Apply RSI volume filter to static signals','checkbox',false],
 ['vwap_filter','RSI VWAP filter','checkbox',false],['vwap_source','RSI VWAP source','select','hlc3',['close','open','high','low','hlc3','ohlc4']],['vwap_anchor','RSI VWAP anchor (UTC)','select','D',['1','3','5','15','30','60','240','D','W','M']],
 ['sma_filter','RSI SMA alignment filter','checkbox',false],['sma_fast','RSI fast SMA','number',7,1,500],['sma_slow','RSI slow SMA','number',50,1,500],
 ['histogram_filter','RSI MACD histogram filter','checkbox',false],['macd_fast','RSI MACD fast length','number',12,1,500],['macd_slow','RSI MACD slow length','number',26,1,500],['macd_signal','RSI MACD signal length','number',9,1,500],['bull_hist_positive','RSI bullish: positive MACD histogram','checkbox',true],['bear_hist_negative','RSI bearish: negative MACD histogram','checkbox',true]
];
function renderRSISettings(config){
 let section=$('rsiSettings');if(!section){section=document.createElement('details');section.id='rsiSettings';$('settingsGroup').insertBefore(section,$('settingsGroup').lastElementChild)}
 section.replaceChildren();const title=document.createElement('summary');title.textContent='RSI calculation settings';section.append(title);
 const note=document.createElement('p');note.className='note';note.textContent='Uploaded Pine preserves its empty ML threshold slots: ML arrows/entries are unavailable, while static 30/70 signals retain the source filters. Corrected ML remains a separate version. Changing the calculation version affects both the chart and RSI entries after Save settings. This update selects Fixed Long 56.68 / Short 42.38 once. Both levels are editable and stay constant until changed. Uploaded Pine and Corrected ML remain available. Fixed levels require RSI warm-up but no ML clustering/history window. Appearance, smoothing and crossing controls are retained. long_S is the upper ML threshold and short_S is the lower ML threshold. Choose All eight crossings to include bearish below 30 and bullish above 70 in RSI entries. RSI filters start OFF; enable them only if wanted. Any selected Volume/VWAP/SMA/MACD filters and static/ML entry switches apply to trading. Existing entry rules are retained until you choose the new rule and save. Display styles are in Appearance.';section.append(note);
 for(const [id,label,type,fallback,min,max,step] of rsiControls){const row=document.createElement('label'),input=document.createElement(type==='select'?'select':'input');row.append(document.createTextNode(label));input.id='rsi_'+id;
  if(type==='select'){for(const value of min){const option=document.createElement('option');option.value=value;option.textContent=id==='signal_rules'?(value==='eight_crossings'?'All eight crossings (30 / 70 / ML, both directions)':'Previous six crossing rules'):id==='calculation_mode'?(value==='fixed'?'Fixed Long/Short thresholds':value==='uploaded_pine'?'Uploaded Pine (original array behavior)':'Corrected ML (preserved alternative)'):value;input.append(option)}}else input.type=type;
  if(type==='checkbox')input.checked=config[id]??fallback;else input.value=config[id]??fallback;
  if(type==='number'){input.min=min;input.max=max;input.step=step||1}row.append(input);section.append(row);
 }
 section.hidden=['sr_order_block_gate','sr_volume_intrabar'].includes($('strategyLogic').value);section.open=['rsi_hourly_intrabar','sr_rsi_daily','sr_rsi_volume_gate'].includes($('strategyLogic').value);syncDailyRSIControls();
}
function collectRSISettings(){return Object.fromEntries(rsiControls.map(([id,,type])=>[id,type==='checkbox'?$('rsi_'+id).checked:type==='number'?Number($('rsi_'+id).value):$('rsi_'+id).value]))}

function syncDailyRSIControls(){
 const daily=['sr_rsi_daily','sr_rsi_volume_gate'].includes($('strategyLogic').value);
 for(const id of ['sma_enabled','sma_7_50_filter_enabled','rsi_volume_filter','rsi_volume_for_static','rsi_vwap_filter','rsi_sma_filter','rsi_histogram_filter'])if($(id)){$(id).disabled=daily;if(daily)$(id).checked=false;}
 for(const [id,value]of [['rsi_calculation_mode','fixed'],['rsi_signal_rules','eight_crossings']])if($(id)){$(id).disabled=daily;if(daily)$(id).value=value;}
 for(const id of ['rsi_static_signals','rsi_ml_signals'])if($(id)){$(id).disabled=daily;if(daily)$(id).checked=true;}
}
