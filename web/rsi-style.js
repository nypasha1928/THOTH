// Device-local RSI presentation. Never included in trading settings.
const rsiSignalTypes={
 static30_up:{label:'Bullish Signal (RSI over 30)',side:'Long',threshold:'static30',offset:-3,chart:'#4caf50',pane:'#4caf50',background:'#008000',opacity:.10},
 static30_down:{label:'Bearish Signal (RSI below 30)',side:'Short',threshold:'static30',offset:3,chart:'#e91e63',pane:'#e91e63',background:'#e91e63',opacity:0},
 static70_up:{label:'Bullish Signal (RSI over 70)',side:'Long',threshold:'static70',offset:-3,chart:'#26a69a',pane:'#26a69a',background:'#26a69a',opacity:0},
 static70_down:{label:'Bearish Signal (RSI below 70)',side:'Short',threshold:'static70',offset:3,chart:'#ff5252',pane:'#ff5252',background:'#ff0000',opacity:.10},
 ml_long_up:{label:'Bullish Signal (ML Long)',side:'Long',threshold:'ml_long',offset:-3,chart:'#00e676',pane:'#00bcd4',background:'#00ff00',opacity:.30},
 ml_short_down:{label:'Bearish Signal (ML Short)',side:'Short',threshold:'ml_short',offset:3,chart:'#ffeb3b',pane:'#ffeb3b',background:'#880e4f',opacity:.30},
 ml_short_up:{label:'Bullish Signal (ML Short Up)',side:'Long',threshold:'ml_short',offset:-3,chart:'#2962ff',pane:'#2962ff',background:'#2962ff',opacity:0},
 ml_long_down:{label:'Bearish Signal (ML Long Down)',side:'Short',threshold:'ml_long',offset:3,chart:'#ff9800',pane:'#ff9800',background:'#ff9800',opacity:0}
};
const rsiVisualDefaults={rsiPaletteVersion:1,rsiSharedColorsVersion:1,rsiFilteredSignalsOnly:false,rsiPaneHeight:220,rsiLineWidth:3,rsiLineMode:'trend',rsiSolidColor:'#b58cf4',rsiLongColor:'#00ff00',rsiShortColor:'#ff0000',rsiNeutralColor:'#787b86',rsiStaticLevelColor:'#81d4fa',showRSIThresholds:true,showRSIClusters:true,rsiCluster1:'#008000',rsiCluster2:'#ff9800',rsiCluster3:'#ff5252',rsiSignalSize:11,
 rsiSignalStyles:Object.fromEntries(Object.entries(rsiSignalTypes).map(([kind,meta])=>[kind,{chartVisible:true,paneVisible:true,chartColor:meta.chart,paneColor:meta.pane,chartShape:'triangle',paneShape:'triangle',chartPosition:meta.side==='Long'?'below':'above',panePosition:'threshold',backgroundVisible:meta.opacity>0,backgroundColor:meta.background,backgroundOpacity:meta.opacity}]))};
function restoreRSIVisual(stored,target){
 for(const key of ['rsiSolidColor','rsiLongColor','rsiShortColor','rsiNeutralColor','rsiStaticLevelColor','rsiCluster1','rsiCluster2','rsiCluster3'])if(/^#[0-9a-f]{6}$/i.test(stored[key]))target[key]=stored[key];
 // Upgrade only the previous default gray; later custom choices stay device-local.
 if(!stored.rsiPaletteVersion&&stored.rsiNeutralColor?.toLowerCase()==='#808080')target.rsiNeutralColor='#787b86';
 for(const key of ['rsiFilteredSignalsOnly','showRSIThresholds','showRSIClusters'])if(typeof stored[key]==='boolean')target[key]=stored[key];
 for(const [key,min,max]of [['rsiPaneHeight',100,360],['rsiLineWidth',1,4],['rsiSignalSize',6,24]])if(Number.isInteger(stored[key])&&stored[key]>=min&&stored[key]<=max)target[key]=stored[key];
 if(['trend','solid'].includes(stored.rsiLineMode))target.rsiLineMode=stored.rsiLineMode;
 // Adopt the requested Pine trend view once; later deliberate Solid choices persist.
 if(!stored.rsiSharedColorsVersion)target.rsiLineMode='trend';
 for(const kind of Object.keys(rsiSignalTypes)){const from=stored.rsiSignalStyles?.[kind],to=target.rsiSignalStyles[kind];if(!from||typeof from!=='object')continue;
  for(const key of ['chartVisible','paneVisible','backgroundVisible'])if(typeof from[key]==='boolean')to[key]=from[key];
  for(const key of ['chartColor','paneColor','backgroundColor'])if(/^#[0-9a-f]{6}$/i.test(from[key]))to[key]=from[key];
  for(const key of ['chartShape','paneShape'])if(['triangle','arrow','circle','cross'].includes(from[key]))to[key]=from[key];
  if(['below','above','top','bottom'].includes(from.chartPosition))to.chartPosition=from.chartPosition;
  if(['threshold','above_rsi','below_rsi','top','bottom'].includes(from.panePosition))to.panePosition=from.panePosition;
  if(Number.isFinite(from.backgroundOpacity)&&from.backgroundOpacity>=0&&from.backgroundOpacity<=1)to.backgroundOpacity=from.backgroundOpacity;
 }
}
function rsiTrendColor(bar,style){if(style.rsiLineMode==='solid')return style.rsiSolidColor;return Number.isFinite(bar?.rsi)&&Number.isFinite(bar?.thresholds?.ml_long)&&bar.rsi>bar.thresholds.ml_long?style.rsiLongColor:Number.isFinite(bar?.rsi)&&Number.isFinite(bar?.thresholds?.ml_short)&&bar.rsi<bar.thresholds.ml_short?style.rsiShortColor:style.rsiNeutralColor}
function rsiEventLevel(bar,event,position='threshold'){
 const meta=rsiSignalTypes[event.kind];if(!meta)return null;
 if(position==='top')return 95;if(position==='bottom')return 5;
 if(position==='above_rsi')return Number.isFinite(bar?.rsi)?bar.rsi+3:null;
 if(position==='below_rsi')return Number.isFinite(bar?.rsi)?bar.rsi-3:null;
 const value=bar?.thresholds?.[meta.threshold];return Number.isFinite(value)?value+meta.offset:null;
}
function rsiMarkerSymbol(event,shape){return shape==='circle'?'●':shape==='cross'?'×':shape==='arrow'?(event.side==='Long'?'↑':'↓'):(event.side==='Long'?'▲':'▼')}
function rsiChoice(label,value,choices,update){const row=document.createElement('label'),input=document.createElement('select');row.append(document.createTextNode(label));input.setAttribute('aria-label',label);for(const choice of choices){const option=document.createElement('option');option.value=choice;option.textContent=choice.replaceAll('_',' ');input.append(option)}input.value=value;input.oninput=()=>{update(input.value);persistChartStyle()};row.append(input);return row}
function renderRSIAppearance(target){
 const group=document.createElement('details');group.id='rsiVisualSettings';const heading=document.createElement('summary');heading.textContent='RSI line, pane and eight signal styles';group.append(heading);
 group.append(appearanceInput('RSI pane height','number',chartStyle.rsiPaneHeight,value=>{if(Number.isInteger(value)&&value>=100&&value<=360)chartStyle.rsiPaneHeight=value},{min:100,max:360}));
 group.append(rsiChoice('RSI line coloring',chartStyle.rsiLineMode,['trend','solid'],value=>chartStyle.rsiLineMode=value));
 for(const [key,label]of [['rsiStaticLevelColor','RSI 30/70 line color'],['rsiLongColor','RSI Long colour (curve and threshold)'],['rsiShortColor','RSI Short colour (curve and threshold)'],['rsiNeutralColor','RSI line neutral color'],['rsiSolidColor','RSI solid line color'],['rsiCluster1','RSI cluster 1 color'],['rsiCluster2','RSI cluster 2 color'],['rsiCluster3','RSI cluster 3 color']])group.append(appearanceInput(label,'color',chartStyle[key],value=>chartStyle[key]=value));
 for(const [key,label]of [['rsiFilteredSignalsOnly','Show only RSI crossings passing entry filters'],['showRSIThresholds','Show RSI ML threshold lines'],['showRSIClusters','Show RSI cluster circles']])group.append(appearanceInput(label,'checkbox',chartStyle[key],value=>chartStyle[key]=value));
 group.append(appearanceInput('RSI line width','number',chartStyle.rsiLineWidth,value=>{if(Number.isInteger(value)&&value>=1&&value<=4)chartStyle.rsiLineWidth=value},{min:1,max:4}));
 group.append(appearanceInput('RSI arrow size','number',chartStyle.rsiSignalSize,value=>{if(Number.isInteger(value)&&value>=6&&value<=24)chartStyle.rsiSignalSize=value},{min:6,max:24}));
 const note=document.createElement('p');note.className='note';note.textContent='RSI styles are separate from S/R/SMA reference arrows. Trend coloring shares Long/Short colours between the RSI curve, ML threshold lines and value labels, as in Pine. Gray is used between the thresholds. Solid is an optional custom override. Every crossing is shown by default, including filtered-out crossings. The filtered-only option follows the saved entry rules and filters. Arrows are reference observations, not proof of a live order. Each family keeps its own color and placement. Pane threshold markers use the Pine offsets (±3 RSI points). Colors default to the supplied screenshots.';group.append(note);
 for(const [kind,meta]of Object.entries(rsiSignalTypes)){const section=document.createElement('details'),title=document.createElement('summary'),style=chartStyle.rsiSignalStyles[kind];title.textContent=meta.label;section.dataset.rsiStyle=kind;section.append(title);
  for(const [key,suffix,type]of [['chartVisible','chart visible','checkbox'],['paneVisible','pane visible','checkbox'],['chartColor','chart color','color'],['paneColor','pane color','color']])section.append(appearanceInput(meta.label+' '+suffix,type,style[key],value=>style[key]=value));
  for(const [key,suffix]of [['chartShape','chart shape'],['paneShape','pane shape']])section.append(rsiChoice(meta.label+' '+suffix,style[key],['triangle','arrow','circle','cross'],value=>style[key]=value));
  section.append(rsiChoice(meta.label+' chart position',style.chartPosition,['below','above','top','bottom'],value=>style.chartPosition=value));
  section.append(rsiChoice(meta.label+' pane position',style.panePosition,['threshold','above_rsi','below_rsi','top','bottom'],value=>style.panePosition=value));
  section.append(appearanceInput(meta.label+' background visible','checkbox',style.backgroundVisible,value=>style.backgroundVisible=value));
  section.append(appearanceInput(meta.label+' background color','color',style.backgroundColor,value=>style.backgroundColor=value));
  section.append(appearanceInput(meta.label+' background opacity (%)','number',Math.round(style.backgroundOpacity*100),value=>{if(value>=0&&value<=100)style.backgroundOpacity=value/100},{min:0,max:100,step:5}));
  group.append(section);
 }
 target.append(group);
}
