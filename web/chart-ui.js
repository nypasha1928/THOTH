// Appearance is device-local display state; never included in trading settings.
const chartStyleDefaults={srDisplayVersion:2,...rsiVisualDefaults,background:'#111d19',showLastPrice:true,bull:'#26a69a',bear:'#ef5350',longSignal:'#4caf50',shortSignal:'#f23645',sma:'#2962ff',sma7:'#ffffff',sma50:'#81d4fa',signalSize:18,rightGap:24,volumeCollapsed:false,rsiCollapsed:false,showVolume:true,showRSI:true,showRSISignals:true,showRSIPriceSignals:true,showRSIPaneSignals:true,showRSIStatic:true,showRSIML:true,showRSIVWAP:false,showSRArrows:true,showSMAArrows:true,showVolumeDeltaArrows:true,showOrderBlockZones:true,obBullish:'#2157f3',obBearish:'#ff5d00',paintSRCandles:false,srLongCandle:'#81d4fa',srShortCandle:'#ffb3ba',
 long_major:{support:'#085d31',resistance:'#b70909',style:'solid',width:4,visible:true},
 long_minor:{support:'#3e7d5c',resistance:'#b14848',style:'dotted',width:2,visible:true},
 short_major:{support:'#83a091',resistance:'#986464',style:'dashed',width:3,visible:true},
 short_minor:{support:'#6e7d75',resistance:'#93716f',style:'dashed',width:1,visible:true}};
const chartStyleKey='thoth.chart.appearance.v1';
const copyStyle=()=>JSON.parse(JSON.stringify(chartStyleDefaults));
const chartTouches=new Map();
let chartPinch=null;
let chartStyle=copyStyle(),chartDrag=null,suppressChartClick=false,chartPriceShift=0,chartPriceZoom=1,chartGeometry=null;
try{const stored=JSON.parse(localStorage.getItem(chartStyleKey)||'null');if(stored&&typeof stored==='object'){
 for(const key of ['bull','bear','longSignal','shortSignal','sma','sma7','sma50','background','srLongCandle','srShortCandle','obBullish','obBearish'])if(/^#[0-9a-f]{6}$/i.test(stored[key]))chartStyle[key]=stored[key];
 for(const key of ['volumeCollapsed','rsiCollapsed','showVolume','showLastPrice','showRSI','showRSISignals','showRSIPriceSignals','showRSIPaneSignals','showRSIStatic','showRSIML','showRSIVWAP','showSRArrows','showSMAArrows','showVolumeDeltaArrows','showOrderBlockZones','paintSRCandles'])if(typeof stored[key]==='boolean'&&(key!=='showSRArrows'||stored.srDisplayVersion===2))chartStyle[key]=stored[key];
 restoreRSIVisual(stored,chartStyle);
 if(Number.isInteger(stored.rightGap)&&stored.rightGap>=0&&stored.rightGap<=100)chartStyle.rightGap=stored.rightGap;
 if(Number.isInteger(stored.signalSize)&&stored.signalSize>=10&&stored.signalSize<=32)chartStyle.signalSize=stored.signalSize;
 for(const key of ['long_major','long_minor','short_major','short_minor']){const value=stored[key];if(!value||typeof value!=='object')continue;for(const side of ['support','resistance'])if(/^#[0-9a-f]{6}$/i.test(value[side]))chartStyle[key][side]=value[side];if(['solid','dotted','dashed'].includes(value.style))chartStyle[key].style=value.style;if(Number.isInteger(value.width)&&value.width>=1&&value.width<=6)chartStyle[key].width=value.width;if(typeof value.visible==='boolean')chartStyle[key].visible=value.visible}
}}catch{/* Invalid/unavailable local storage uses defaults. */}
function persistChartStyle(){if($('showSRSignals'))$('showSRSignals').checked=chartStyle.showSRArrows;try{localStorage.setItem(chartStyleKey,JSON.stringify(chartStyle));$('appearanceStatus').textContent='Appearance saved on this device.'}catch{$('appearanceStatus').textContent='Appearance changed for this session; browser storage unavailable.'}if(state)draw()}
function appearanceInput(label,type,value,oninput,options={}){const row=document.createElement('label');row.append(document.createTextNode(label));const input=document.createElement(type==='select'?'select':'input');if(type!=='select')input.type=type;input.setAttribute('aria-label',label);for(const [key,val]of Object.entries(options))input.setAttribute(key,val);if(type==='select')for(const name of ['solid','dotted','dashed']){const option=document.createElement('option');option.value=name;option.textContent=name;input.append(option)}if(type==='checkbox')input.checked=value;else input.value=value;input.oninput=()=>{oninput(type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value);persistChartStyle()};row.append(input);return row}
function indicatorDropdown(target,name){const group=document.createElement('details'),heading=document.createElement('summary');group.className='indicator-dropdown';group.dataset.indicator=name;heading.textContent=name;group.append(heading);target.append(group);return group}
function renderAppearance(){const target=$('appearanceFields');target.replaceChildren();
 target.append(appearanceInput('Chart background','color',chartStyle.background,v=>chartStyle.background=v));
 target.append(appearanceInput('Show current price line and label','checkbox',chartStyle.showLastPrice,v=>chartStyle.showLastPrice=v));
 for(const [key,label]of [['bull','Bullish candle'],['bear','Bearish candle']])target.append(appearanceInput(label,'color',chartStyle[key],v=>chartStyle[key]=v));
 target.append(appearanceInput('Right spacing (bars)','number',chartStyle.rightGap,v=>{if(Number.isInteger(v)&&v>=0&&v<=100)chartStyle.rightGap=v},{min:0,max:100}));
 for(const [key,label]of [['sma7','SMA 7'],['sma50','SMA 50'],['sma','SMA 200']])indicatorDropdown(target,label).append(appearanceInput(label+' color','color',chartStyle[key],v=>chartStyle[key]=v));
 const volumeGroup=indicatorDropdown(target,'Volume / MA 20');
 volumeGroup.append(appearanceInput('Volume with MA 20','checkbox',chartStyle.showVolume,v=>chartStyle.showVolume=v));
 const rsiGroup=indicatorDropdown(target,'RSI');
 for(const [key,label]of [['showRSI','Show RSI indicator pane'],['showRSISignals','Show RSI signal arrows'],['showRSIPriceSignals','Show RSI arrows on price chart'],['showRSIPaneSignals','Show RSI arrows in RSI pane'],['showRSIStatic','Show static RSI 30/70 arrows'],['showRSIML','Show ML RSI arrows']])rsiGroup.append(appearanceInput(label,'checkbox',chartStyle[key],v=>chartStyle[key]=v));
 const vwapGroup=indicatorDropdown(target,'RSI VWAP');vwapGroup.append(appearanceInput('Show RSI VWAP on price chart','checkbox',chartStyle.showRSIVWAP,v=>chartStyle.showRSIVWAP=v));
 const srGroup=indicatorDropdown(target,'S/R');
 for(const [key,label]of [['showSRArrows','Show S/R breakout arrows'],['paintSRCandles','Color candles on S/R signals']])srGroup.append(appearanceInput(label,'checkbox',chartStyle[key],value=>chartStyle[key]=value));
 for(const [key,label]of [['srLongCandle','S/R bullish signal candle color'],['srShortCandle','S/R bearish signal candle color']])srGroup.append(appearanceInput(label,'color',chartStyle[key],value=>chartStyle[key]=value));
 renderRSIAppearance(rsiGroup);
 const deltaGroup=indicatorDropdown(target,'Volume Delta signals');deltaGroup.append(appearanceInput('Show Volume Delta reference arrows','checkbox',chartStyle.showVolumeDeltaArrows,v=>{chartStyle.showVolumeDeltaArrows=v;if(v&&state)loadChart()}));
 const obGroup=indicatorDropdown(target,'Order Block boxes');obGroup.append(appearanceInput('Show Order Block boxes','checkbox',chartStyle.showOrderBlockZones,v=>{chartStyle.showOrderBlockZones=v;if(v&&state)loadChart()}));
 for(const [key,label]of [['obBullish','Bullish block color'],['obBearish','Bearish block color']])obGroup.append(appearanceInput(label,'color',chartStyle[key],v=>chartStyle[key]=v));
 const crossoverGroup=indicatorDropdown(target,'SMA crossover signals');crossoverGroup.append(appearanceInput('Show SMA crossover reference arrows','checkbox',chartStyle.showSMAArrows,v=>chartStyle.showSMAArrows=v));
 for(const [key,label]of [['longSignal','S/R / SMA bullish arrow'],['shortSignal','S/R / SMA bearish arrow']])target.append(appearanceInput(label,'color',chartStyle[key],v=>chartStyle[key]=v));
 target.append(appearanceInput('Signal size','number',chartStyle.signalSize,v=>{if(Number.isInteger(v)&&v>=10&&v<=32)chartStyle.signalSize=v},{min:10,max:32}));
 for(const [key,label]of [['long_major','Long-term major'],['long_minor','Long-term minor'],['short_major','Short-term major'],['short_minor','Short-term minor']]){const group=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=label;group.append(legend);for(const [property,type,name]of [['visible','checkbox','Show'],['support','color','Support'],['resistance','color','Resistance'],['style','select','Line style'],['width','number','Line width']])group.append(appearanceInput(label+' '+name,type,chartStyle[key][property],v=>{if(property!=='width'||Number.isInteger(v)&&v>=1&&v<=6)chartStyle[key][property]=v},property==='width'?{min:1,max:6}:{}));srGroup.append(group)}
}
function refreshPaneControls(){
 $('toggleVolumePane').textContent=!chartStyle.showVolume?'Show Volume':chartStyle.volumeCollapsed?'Expand Volume':'Minimize Volume';
 $('toggleVolumePane').setAttribute('aria-expanded',String(chartStyle.showVolume&&!chartStyle.volumeCollapsed));
 $('toggleRSIPane').hidden=!chartStyle.showRSI;$('closeRSIPane').hidden=!chartStyle.showRSI;$('showRSIPane').hidden=chartStyle.showRSI;
 $('toggleRSIPane').textContent=chartStyle.rsiCollapsed?'Expand RSI':'Minimize RSI';$('toggleRSIPane').setAttribute('aria-expanded',String(chartStyle.showRSI&&!chartStyle.rsiCollapsed));
}
$('toggleVolumePane').onclick=()=>{if(!chartStyle.showVolume){chartStyle.showVolume=true;chartStyle.volumeCollapsed=false}else chartStyle.volumeCollapsed=!chartStyle.volumeCollapsed;persistChartStyle()};
$('toggleRSIPane').onclick=()=>{chartStyle.rsiCollapsed=!chartStyle.rsiCollapsed;persistChartStyle()};
$('closeRSIPane').onclick=()=>{chartStyle.showRSI=false;persistChartStyle()};
$('showRSIPane').onclick=()=>{chartStyle.showRSI=true;chartStyle.rsiCollapsed=false;persistChartStyle()};
function openChartSymbol(symbol){if(!watchlist.some(x=>x.selectable&&x.symbol===symbol))return;location.hash='#chart';showPage();$('symbol').value=symbol;inspectedCandle=null;renderWatchlist();loadChart()}
function renderChartSearch(){const q=$('chartSearchInput').value.trim().toUpperCase(),matches=watchlist.filter(x=>x.selectable&&(x.symbol.includes(q)||x.original.toUpperCase().includes(q)));$('chartSearchCount').textContent=matches.length+' matches'+(matches.length>60?' · Showing first 60; type to narrow':'');$('chartSearchResults').replaceChildren();for(const item of matches.slice(0,60)){const button=document.createElement('button');button.type='button';button.textContent=item.symbol;button.onclick=()=>{openChartSymbol(item.symbol);$('chartSearchDialog').close();$('chartSearch').focus()};$('chartSearchResults').append(button)}if(!matches.length)$('chartSearchResults').textContent='No matching chart symbols'}
$('chartSearch').onclick=()=>{$('chartSearchInput').value='';renderChartSearch();$('chartSearchDialog').showModal();$('chartSearchInput').focus()};
$('chartSearchInput').oninput=renderChartSearch;
$('chartSearchInput').onkeydown=e=>{if(e.key==='Enter'){const first=$('chartSearchResults').querySelector('button');if(first){e.preventDefault();first.click()}}};
$('closeChartSearch').onclick=()=>{$('chartSearchDialog').close();$('chartSearch').focus()};
function toggleAppearance(open){$('chartAppearance').hidden=!open;$('appearanceToggle').setAttribute('aria-expanded',String(open));if(state)draw()}
$('appearanceToggle').onclick=()=>toggleAppearance($('chartAppearance').hidden);
$('closeAppearance').onclick=()=>{toggleAppearance(false);$('appearanceToggle').focus()};
$('resetAppearance').onclick=()=>{chartStyle=copyStyle();renderAppearance();persistChartStyle()};
// A CSS-expanded view works in Android PWAs as well as desktop browsers.
function expandChart(expanded){const card=$('chartCard');card.classList.toggle('chart-expanded',expanded);document.body.classList.toggle('chart-is-expanded',expanded);$('maximizeChart').textContent=expanded?'Restore chart':'Maximize chart';$('maximizeChart').setAttribute('aria-expanded',String(expanded));if(state)draw();$('maximizeChart').focus()}
$('maximizeChart').onclick=()=>expandChart(!$('chartCard').classList.contains('chart-expanded'));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('chartSearchDialog').open&&!$('liveDialog').open){if(!$('chartAppearance').hidden)toggleAppearance(false);else if($('chartCard').classList.contains('chart-expanded'))expandChart(false)}});
renderAppearance();
$('showSRSignals').checked=chartStyle.showSRArrows;
$('showSRSignals').onchange=()=>{chartStyle.showSRArrows=$('showSRSignals').checked;renderAppearance();persistChartStyle()};

// Keep keyboard focus within the expanded chart; background trading controls
// must not be reachable by tabbing behind this view.
document.addEventListener('keydown',e=>{
 if(e.key!=='Tab'||!$('chartCard').classList.contains('chart-expanded')||$('chartSearchDialog').open||$('liveDialog').open)return;
 const controls=[...$('chartCard').querySelectorAll('button,input,select,[tabindex="0"]')].filter(n=>!n.disabled&&n.getClientRects().length);
 const first=controls[0],last=controls.at(-1);if(!first)return;
 if(!$('chartCard').contains(document.activeElement)){e.preventDefault();first.focus()}
 else if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}
 else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
});

function displayAverage(rows,period,field){
 const result=new Map();let sum=0,valid=0;
 for(let i=0;i<rows.length;i++){const value=rows[i][field],ok=Number.isFinite(value);if(ok){sum+=value;valid++}if(i>=period){const old=rows[i-period][field];if(Number.isFinite(old)){sum-=old;valid--}}if(i>=period-1&&valid===period)result.set(rows[i].time,sum/period)}
 return result;
}

let formingDisplayState=null;
function synchronizeFormingCandle(rows,quote,context,previous=null){
 const last=rows.at(-1),seconds=chartIntervalSeconds(context.interval),now=context.now??Date.now()/1000;
 if(!last||!quote||quote.symbol!==context.symbol||quote.environment!==context.environment||!Number.isFinite(quote.price)||quote.price<=0||now-quote.observed_at>=20||now-quote.observed_at< -5||now<last.time||now>=last.time+seconds||quote.observed_at<last.time||quote.observed_at>=last.time+seconds)return {rows,state:previous};
 const key=[context.environment,context.symbol,context.interval,last.time].join(':');
 const prior=previous?.key===key?previous:null;
 const state={key,high:Math.max(last.high,quote.price,prior?.high??-Infinity),low:Math.min(last.low,quote.price,prior?.low??Infinity)};
 // Price/observed extrema are display-only. Do not manufacture volume, a new
 // candle, closed history, or any changes to the strategy's analysis records.
 return {rows:[...rows.slice(0,-1),{...last,close:quote.price,high:state.high,low:state.low}],state};
}

function candleCountdown(nowMilliseconds=marketNow(),interval=$('tf').value){
 const period=chartIntervalSeconds(interval),now=Math.floor(nowMilliseconds/1000),remaining=period-((now%period)+period)%period;
 const seconds=String(remaining%60).padStart(2,'0'),minutes=String(Math.floor(remaining/60)%60).padStart(2,'0');
 return period>=3600?String(Math.floor(remaining/3600)).padStart(2,'0')+':'+minutes+':'+seconds:minutes+':'+seconds;
}
function updateCandleCountdown(){
 const text=candleCountdown();for(const node of document.querySelectorAll('[data-candle-countdown]'))node.textContent=text;
 $('candleCountdown').textContent='Current '+$('tf').selectedOptions[0].textContent+' candle closes in '+text+' · New York display';
}
setInterval(updateCandleCountdown,1000);

let rulerMode=false,chartMeasurement=null,rulerPointer=null;
function measurementStats(start,end,rows){
 const delta=end.price-start.price,percent=start.price?delta/start.price*100:0;
 const a=rows.findIndex(r=>r.time===start.time),b=rows.findIndex(r=>r.time===end.time);
 return {delta,percent,bars:a>=0&&b>=0?Math.abs(b-a):null,seconds:Math.abs(end.time-start.time)};
}
function setRulerMode(enabled){rulerMode=enabled;rulerPointer=null;$('measureChart').setAttribute('aria-pressed',String(enabled));$('measureChart').textContent=enabled?'Cancel measure':'Measure move'}
$('measureChart').onclick=()=>{const enabled=!rulerMode;setRulerMode(enabled);if(enabled||!chartMeasurement?.end)chartMeasurement=null;draw()};
$('clearMeasurement').onclick=()=>{chartMeasurement=null;setRulerMode(false);draw()};
$('oldest').onclick=()=>{chartOffset=Math.max(0,chartRows.length-visibleBars);chartPriceShift=0;chartPriceZoom=1;draw()};
