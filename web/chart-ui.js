// Appearance is device-local display state; never included in trading settings.
const chartStyleDefaults={background:'#111d19',showLastPrice:true,bull:'#26a69a',bear:'#ef5350',longSignal:'#4caf50',shortSignal:'#f23645',sma:'#2962ff',signalSize:18,rightGap:24,showVolume:true,showSma7:true,
 long_major:{support:'#085d31',resistance:'#b70909',style:'solid',width:4,visible:true},
 long_minor:{support:'#3e7d5c',resistance:'#b14848',style:'dotted',width:2,visible:true},
 short_major:{support:'#83a091',resistance:'#986464',style:'dashed',width:3,visible:true},
 short_minor:{support:'#6e7d75',resistance:'#93716f',style:'dashed',width:1,visible:true}};
const chartStyleKey='thoth.chart.appearance.v1';
const copyStyle=()=>JSON.parse(JSON.stringify(chartStyleDefaults));
let chartStyle=copyStyle(),chartDrag=null,suppressChartClick=false,chartPriceShift=0,chartPriceZoom=1,chartGeometry=null;
try{const stored=JSON.parse(localStorage.getItem(chartStyleKey)||'null');if(stored&&typeof stored==='object'){
 for(const key of ['bull','bear','longSignal','shortSignal','sma','background'])if(/^#[0-9a-f]{6}$/i.test(stored[key]))chartStyle[key]=stored[key];
 for(const key of ['showVolume','showSma7','showLastPrice'])if(typeof stored[key]==='boolean')chartStyle[key]=stored[key];
 if(Number.isInteger(stored.rightGap)&&stored.rightGap>=0&&stored.rightGap<=100)chartStyle.rightGap=stored.rightGap;
 if(Number.isInteger(stored.signalSize)&&stored.signalSize>=10&&stored.signalSize<=32)chartStyle.signalSize=stored.signalSize;
 for(const key of ['long_major','long_minor','short_major','short_minor']){const value=stored[key];if(!value||typeof value!=='object')continue;for(const side of ['support','resistance'])if(/^#[0-9a-f]{6}$/i.test(value[side]))chartStyle[key][side]=value[side];if(['solid','dotted','dashed'].includes(value.style))chartStyle[key].style=value.style;if(Number.isInteger(value.width)&&value.width>=1&&value.width<=6)chartStyle[key].width=value.width;if(typeof value.visible==='boolean')chartStyle[key].visible=value.visible}
}}catch{/* Invalid/unavailable local storage uses defaults. */}
function persistChartStyle(){try{localStorage.setItem(chartStyleKey,JSON.stringify(chartStyle));$('appearanceStatus').textContent='Appearance saved on this device.'}catch{$('appearanceStatus').textContent='Appearance changed for this session; browser storage unavailable.'}if(state)draw()}
function appearanceInput(label,type,value,oninput,options={}){const row=document.createElement('label');row.append(document.createTextNode(label));const input=document.createElement(type==='select'?'select':'input');if(type!=='select')input.type=type;input.setAttribute('aria-label',label);for(const [key,val]of Object.entries(options))input.setAttribute(key,val);if(type==='select')for(const name of ['solid','dotted','dashed']){const option=document.createElement('option');option.value=name;option.textContent=name;input.append(option)}if(type==='checkbox')input.checked=value;else input.value=value;input.oninput=()=>{oninput(type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value);persistChartStyle()};row.append(input);return row}
function renderAppearance(){const target=$('appearanceFields');target.replaceChildren();
 target.append(appearanceInput('Chart background','color',chartStyle.background,v=>chartStyle.background=v));
 target.append(appearanceInput('Show current price line and label','checkbox',chartStyle.showLastPrice,v=>chartStyle.showLastPrice=v));
 for(const [key,label]of [['bull','Bullish candle'],['bear','Bearish candle'],['longSignal','Bullish signal'],['shortSignal','Bearish signal'],['sma','SMA line']])target.append(appearanceInput(label,'color',chartStyle[key],v=>chartStyle[key]=v));
 target.append(appearanceInput('Right spacing (bars)','number',chartStyle.rightGap,v=>{if(Number.isInteger(v)&&v>=0&&v<=100)chartStyle.rightGap=v},{min:0,max:100}));
 target.append(appearanceInput('Volume with MA 20','checkbox',chartStyle.showVolume,v=>chartStyle.showVolume=v));
 target.append(appearanceInput('SMA 7 (white)','checkbox',chartStyle.showSma7,v=>chartStyle.showSma7=v));
 target.append(appearanceInput('Signal size','number',chartStyle.signalSize,v=>{if(Number.isInteger(v)&&v>=10&&v<=32)chartStyle.signalSize=v},{min:10,max:32}));
 for(const [key,label]of [['long_major','Long-term major'],['long_minor','Long-term minor'],['short_major','Short-term major'],['short_minor','Short-term minor']]){const group=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=label;group.append(legend);for(const [property,type,name]of [['visible','checkbox','Show'],['support','color','Support'],['resistance','color','Resistance'],['style','select','Line style'],['width','number','Line width']])group.append(appearanceInput(label+' '+name,type,chartStyle[key][property],v=>{if(property!=='width'||Number.isInteger(v)&&v>=1&&v<=6)chartStyle[key][property]=v},property==='width'?{min:1,max:6}:{}));target.append(group)}
}
function openChartSymbol(symbol){if(!watchlist.some(x=>x.selectable&&x.symbol===symbol))return;$('symbol').value=symbol;inspectedCandle=null;renderWatchlist();loadChart()}
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
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('chartSearchDialog').open&&!$('testnetDialog').open){if(!$('chartAppearance').hidden)toggleAppearance(false);else if($('chartCard').classList.contains('chart-expanded'))expandChart(false)}});
renderAppearance();

// Keep keyboard focus within the expanded chart; background trading controls
// must not be reachable by tabbing behind this view.
document.addEventListener('keydown',e=>{
 if(e.key!=='Tab'||!$('chartCard').classList.contains('chart-expanded')||$('chartSearchDialog').open||$('testnetDialog').open)return;
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
 const last=rows.at(-1),seconds=context.interval==='D'?86400:60,now=context.now??Date.now()/1000;
 if(!last||!quote||quote.symbol!==context.symbol||quote.environment!==context.environment||!Number.isFinite(quote.price)||quote.price<=0||now-quote.observed_at>=20||now-quote.observed_at< -5||now<last.time||now>=last.time+seconds||quote.observed_at<last.time||quote.observed_at>=last.time+seconds)return {rows,state:previous};
 const key=[context.environment,context.symbol,context.interval,last.time].join(':');
 const prior=previous?.key===key?previous:null;
 const state={key,high:Math.max(last.high,quote.price,prior?.high??-Infinity),low:Math.min(last.low,quote.price,prior?.low??Infinity)};
 // Price/observed extrema are display-only. Do not manufacture volume, a new
 // candle, closed history, or any changes to the strategy's analysis records.
 return {rows:[...rows.slice(0,-1),{...last,close:quote.price,high:state.high,low:state.low}],state};
}

function candleCountdown(nowMilliseconds=marketNow(),interval=$('tf').value){
 const period=interval==='D'?86400:60,now=Math.floor(nowMilliseconds/1000),remaining=period-((now%period)+period)%period;
 const seconds=String(remaining%60).padStart(2,'0'),minutes=String(Math.floor(remaining/60)%60).padStart(2,'0');
 return interval==='D'?String(Math.floor(remaining/3600)).padStart(2,'0')+':'+minutes+':'+seconds:minutes+':'+seconds;
}
function updateCandleCountdown(){
 const text=candleCountdown();for(const node of document.querySelectorAll('[data-candle-countdown]'))node.textContent=text;
 $('candleCountdown').textContent='Current '+($('tf').value==='D'?'Daily':'1m')+' candle closes in '+text+' · UTC';
}
setInterval(updateCandleCountdown,1000);
