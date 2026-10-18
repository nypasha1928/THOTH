// Appearance is device-local display state; never included in trading settings.
const chartStyleDefaults={bull:'#26a69a',bear:'#ef5350',longSignal:'#4caf50',shortSignal:'#f23645',sma:'#2962ff',signalSize:18,
 long_major:{support:'#085d31',resistance:'#b70909',style:'solid',width:4,visible:true},
 long_minor:{support:'#3e7d5c',resistance:'#b14848',style:'dotted',width:2,visible:true},
 short_major:{support:'#83a091',resistance:'#986464',style:'dashed',width:3,visible:true},
 short_minor:{support:'#6e7d75',resistance:'#93716f',style:'dashed',width:1,visible:true}};
const chartStyleKey='thoth.chart.appearance.v1';
const copyStyle=()=>JSON.parse(JSON.stringify(chartStyleDefaults));
let chartStyle=copyStyle(),chartDrag=null,suppressChartClick=false;
try{const stored=JSON.parse(localStorage.getItem(chartStyleKey)||'null');if(stored&&typeof stored==='object'){
 for(const key of ['bull','bear','longSignal','shortSignal','sma'])if(/^#[0-9a-f]{6}$/i.test(stored[key]))chartStyle[key]=stored[key];
 if(Number.isInteger(stored.signalSize)&&stored.signalSize>=10&&stored.signalSize<=32)chartStyle.signalSize=stored.signalSize;
 for(const key of ['long_major','long_minor','short_major','short_minor']){const value=stored[key];if(!value||typeof value!=='object')continue;for(const side of ['support','resistance'])if(/^#[0-9a-f]{6}$/i.test(value[side]))chartStyle[key][side]=value[side];if(['solid','dotted','dashed'].includes(value.style))chartStyle[key].style=value.style;if(Number.isInteger(value.width)&&value.width>=1&&value.width<=6)chartStyle[key].width=value.width;if(typeof value.visible==='boolean')chartStyle[key].visible=value.visible}
}}catch{/* Invalid/unavailable local storage uses defaults. */}
function persistChartStyle(){try{localStorage.setItem(chartStyleKey,JSON.stringify(chartStyle));$('appearanceStatus').textContent='Appearance saved on this device.'}catch{$('appearanceStatus').textContent='Appearance changed for this session; browser storage unavailable.'}if(state)draw()}
function appearanceInput(label,type,value,oninput,options={}){const row=document.createElement('label');row.append(document.createTextNode(label));const input=document.createElement(type==='select'?'select':'input');if(type!=='select')input.type=type;input.setAttribute('aria-label',label);for(const [key,val]of Object.entries(options))input.setAttribute(key,val);if(type==='select')for(const name of ['solid','dotted','dashed']){const option=document.createElement('option');option.value=name;option.textContent=name;input.append(option)}if(type==='checkbox')input.checked=value;else input.value=value;input.oninput=()=>{oninput(type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value);persistChartStyle()};row.append(input);return row}
function renderAppearance(){const target=$('appearanceFields');target.replaceChildren();
 for(const [key,label]of [['bull','Bullish candle'],['bear','Bearish candle'],['longSignal','Bullish signal'],['shortSignal','Bearish signal'],['sma','SMA line']])target.append(appearanceInput(label,'color',chartStyle[key],v=>chartStyle[key]=v));
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
