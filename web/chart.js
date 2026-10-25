let inspectedCandle=null;
function draw(){
 renderHistoryStatus();refreshPaneControls();
 const svg=$('chart');svg.replaceChildren();svg.style.backgroundColor=chartStyle.background;svg.classList.toggle('ruler-mode',rulerMode);
 const rgb=chartStyle.background.slice(1).match(/../g).map(v=>parseInt(v,16)),light=.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2]>160,axisText=light?'#314138':'#a9bbb0',gridColor=light?'#d5ddd8':'#263c32';
 const expanded=$('chartCard').classList.contains('chart-expanded');
 const W=Math.max(410,Math.min(1800,svg.clientWidth||800)),H=expanded?Math.max(560,window.innerHeight-270):(W<600?370:460)+chartStyle.rsiPaneHeight+25,L=12,R=W-112,T=30,axisBottom=H-55,rsiHeight=chartStyle.showRSI?(chartStyle.rsiCollapsed?22:Math.min(chartStyle.rsiPaneHeight,H*.4)):0,volumeBottom=axisBottom-rsiHeight-(rsiHeight?20:0),volumeHeight=chartStyle.showVolume?(chartStyle.volumeCollapsed?20:Math.max(65,Math.min(130,H*.2))):0,B=volumeBottom-volumeHeight-(chartStyle.showVolume?22:0),P=R-L;
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 const ns='http://www.w3.org/2000/svg';
 function shape(tag,attrs,parent=svg){const node=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))node.setAttribute(k,v);parent.append(node);return node}
 const synchronized=synchronizeFormingCandle(chartRows,currentQuote,{symbol:$('symbol').value,environment:mode,interval:$('tf').value},formingDisplayState);formingDisplayState=synchronized.state;const displayRows=synchronized.rows;
 const end=displayRows.length-chartOffset,rows=displayRows.slice(Math.max(0,end-visibleBars),end);
 $('chartNavigationStatus').textContent=chartLookback>1000||chartOffset>0?'Exploring chart · auto refresh paused. Latest candle returns to the live edge and auto scale.':'Finger drag: time/price · Pinch or wheel: candle zoom · Right price scale: price zoom · Scroll outside the chart to move the page';
 $('title').textContent=$('symbol').value+' · '+$('tf').selectedOptions[0].textContent;
 $('oldest').disabled=!chartRows.length||chartOffset>=Math.max(0,chartRows.length-visibleBars);
 $('older').disabled=chartOffset>=Math.max(0,chartRows.length-visibleBars);$('newer').disabled=chartOffset===0;$('latest').disabled=chartLookback===1000&&chartOffset===0&&chartPriceShift===0&&chartPriceZoom===1;
 if(!rows.length){chartGeometry=null;for(const key of ['onpointerdown','onpointermove','onpointerup','onpointercancel','onclick','ondblclick'])svg[key]=null;$('candleInfo').textContent=chartLoading?'Loading candles…':'No candles available for this chart.';return}
 const start=rows[0].time,finish=rows.at(-1).time,step=P/(rows.length+chartStyle.rightGap),x=i=>L+(i+.5)*step;
 const levelSnapshot=srAnalysis.filter(b=>b.time<=finish).at(-1);
 const family=key=>(key.startsWith('long_')?'long_':'short_')+(key.endsWith('_major')?'major':'minor');
 const displaySmas=[{period:7,color:chartStyle.sma7},{period:50,color:chartStyle.sma50},{period:200,color:chartStyle.sma}].map(line=>({...line,values:displayAverage(displayRows,line.period,'close')})),volumeMA=displayAverage(chartRows,20,'volume');
 const byTime=new Map(analysis.map(b=>[b.time,b])),srByTime=new Map(srAnalysis.map(b=>[b.time,b])),volumeDeltaByTime=new Map(volumeDeltaRows.map(b=>[b.time,b]));
 const settings=current().settings,showLevels=$('showLevels').checked;
 const enabled=key=>(!key.endsWith('_major')||settings.major)&&(!key.endsWith('_minor')||settings.minor)&&chartStyle[family(key)]?.visible;
 const active=rows.map(r=>byTime.get(r.time)).filter(Boolean),positions=current().positions.filter(p=>p.symbol===$('symbol').value);
 const stop=p=>Number(p.stop)>0?Number(p.stop):p.entry+(p.side==='Long'?-1:1)*p.stop_dollars/p.qty;
 const values=rows.flatMap(r=>[Number(r.low),Number(r.high)]);
 for(const line of displaySmas)for(const r of rows){const value=line.values.get(r.time);if(Number.isFinite(value))values.push(value)}
 if($('fitLevels').checked){if(showLevels)for(const [key,level]of Object.entries(levelSnapshot?.levels||{}))if(enabled(key)&&Number.isFinite(level.value))values.push(level.value);for(const p of positions){values.push(p.entry,stop(p));if(Number(p.take_profit?.exchange_price)>0)values.push(Number(p.take_profit.exchange_price));if(p.trailing?.enabled&&Number(p.trailing.activation_price)>0)values.push(Number(p.trailing.activation_price))}}
 const bottom=Math.min(...values),top=Math.max(...values),pad=Math.max((top-bottom)*.12,Math.abs(top)*.00002,.000001),center=(bottom+top)/2+chartPriceShift,half=((top-bottom)/2+pad)*chartPriceZoom,min=center-half,max=center+half;
 chartGeometry={W,H,L,R,T,B,P,step,firstIndex:Math.max(0,end-visibleBars),pricePerPixel:(max-min)/(B-T)*H/svg.getBoundingClientRect().height};
 const y=p=>B-(Number(p)-min)/(max-min)*(B-T),price=marketPrice;
 const axis=shape('rect',{x:R+2,y:T-14,width:W-R-2,height:B-T+28,fill:chartStyle.background,'data-price-axis':'true'});shape('title',{},axis).textContent='Drag up/down or scroll to scale prices. Double-click for auto scale.';
 for(let i=0;i<6;i++){const value=min+(max-min)*i/5,yy=y(value);shape('path',{d:`M${L} ${yy} H${R}`,stroke:gridColor,'stroke-dasharray':'2 4'});shape('text',{x:R+8,y:yy+4,fill:axisText,'font-size':11,'data-price-tick':value}).textContent=price(value)}
 const defs=shape('defs',{}),clip=shape('clipPath',{id:'plotClip'},defs);shape('rect',{x:L,y:T-10,width:P,height:B-T+20},clip);
 const plot=shape('g',{'clip-path':'url(#plotClip)'});
 const daily=$('tf').value==='D',labelWidth=daily?94:45,labelEvery=Math.max(1,Math.ceil(labelWidth/step));
 rows.forEach((r,i)=>{shape('path',{d:`M${x(i)} ${axisBottom} v5`,stroke:axisText,'data-time-tick':r.time});if(i%labelEvery===0){const stamp=chartTime(r.time,{time:!daily,zone:false});shape('path',{d:`M${x(i)} ${T} V${B}`,stroke:gridColor});shape('text',{x:x(i),y:H-22,fill:axisText,'font-size':10,'text-anchor':i===0?'start':'middle','data-time-label':r.time}).textContent=daily?stamp:stamp.slice(11,16)}});
 shape('text',{x:R+8,y:H-22,fill:axisText,'font-size':10}).textContent='NY';
 if(showLevels){for(const [key,level]of Object.entries(levelSnapshot?.levels||{})){
  if(!enabled(key)||!Number.isFinite(level.value))continue;
  const style=chartStyle[family(key)],color=style[key.includes('_support_')?'support':'resistance'];
  // Display the active confirmed level as a right-extending horizontal ray,
  // matching the Pine drawing style. Pivot anchoring is retrospective display;
  // signal calculations continue using the causal per-candle analysis unchanged.
  const pivot=Number(level.pivot_time);if(Number.isFinite(pivot)&&pivot>finish)continue;
  const index=Number.isFinite(pivot)?rows.findIndex(r=>r.time>=pivot):0;
  const anchor=pivot<start?L:x(Math.max(0,index));
  const ray=shape('path',{d:`M${anchor} ${y(level.value)} H${R}`,fill:'none',stroke:color,'stroke-width':style.width,
    'stroke-dasharray':style.style==='dashed'?'12 10':style.style==='dotted'?'1 6':'','stroke-linecap':style.style==='dotted'?'round':'butt',
    'data-sr-key':key},plot);
  shape('title',{},ray).textContent=key.replaceAll('_',' ')+' · '+price(level.value)+' · active at '+chartTime(levelSnapshot.time);
 }}
 for(const line of displaySmas){let d='',connected=false;rows.forEach((r,i)=>{const value=line.values.get(r.time);if(!Number.isFinite(value)){connected=false;return}d+=`${connected?' L':' M'}${x(i)} ${y(value)}`;connected=true});if(d)shape('path',{d,fill:'none',stroke:line.color,'stroke-width':line.period===7?1.6:2,['data-sma'+line.period]:'true'},plot)}
 if(chartStyle.showVolume&&!chartStyle.volumeCollapsed){const volumeTop=B+25,validVolumes=rows.filter(r=>Number.isFinite(r.volume)&&r.volume>=0),maxVolume=Math.max(1,...validVolumes.map(r=>r.volume),...rows.map(r=>volumeMA.get(r.time)||0)),vy=v=>volumeBottom-(v/maxVolume)*(volumeBottom-volumeTop-18);
  shape('path',{d:`M${L} ${B+13} H${R}`,stroke:'#344b3d'});
  shape('text',{x:L,y:volumeTop+8,fill:axisText,'font-size':10,'data-volume-heading':'true'}).textContent='Volume · MA 20'+(validVolumes.length?'':' · unavailable');
  for(const [i,r]of rows.entries()){if(!Number.isFinite(r.volume)||r.volume<0)continue;shape('rect',{x:x(i)-Math.max(.7,step*.3),y:vy(r.volume),width:Math.max(1.4,step*.6),height:volumeBottom-vy(r.volume),fill:r.close>=r.open?chartStyle.bull:chartStyle.bear,opacity:.7,'data-volume-bar':r.time})}
  let d='',connected=false;rows.forEach((r,i)=>{const value=volumeMA.get(r.time);if(!Number.isFinite(value)){connected=false;return}d+=`${connected?' L':' M'}${x(i)} ${vy(value)}`;connected=true});shape('path',{d,fill:'none',stroke:'#f2b84b','stroke-width':1.5,'data-volume-ma':'20'});
  shape('text',{x:R+8,y:volumeTop+22,fill:axisText,'font-size':10}).textContent=marketPrice(maxVolume);
 }
 if(chartStyle.showVolume&&chartStyle.volumeCollapsed)shape('text',{x:L,y:volumeBottom-4,fill:axisText,'font-size':10,'data-volume-minimized':'true'}).textContent='Volume · minimized';
 const rsiByTime=new Map(rsiRows.map(bar=>[bar.time,bar]));
 const rsiEvents=bar=>chartStyle.rsiFilteredSignalsOnly?(bar?.events||[]):(bar?.crossings??bar?.events??[]);
 const visibleRSIEvents=bar=>rsiEvents(bar).filter(event=>rsiSignalTypes[event.kind]&&(event.kind.startsWith('static')?chartStyle.showRSIStatic:chartStyle.showRSIML));
 if(chartStyle.showRSI&&!chartStyle.rsiCollapsed){
  const paneTop=axisBottom-rsiHeight,paneBottom=axisBottom-5,ry=value=>paneBottom-Math.max(0,Math.min(100,value))/100*(paneBottom-paneTop-20),latestRSI=rsiRows.filter(bar=>bar.time<=finish).at(-1);
  shape('path',{d:`M${L} ${paneTop-10} H${R}`,stroke:gridColor});
  shape('text',{x:L,y:paneTop+8,fill:axisText,'font-size':10,'data-rsi-heading':'true'}).textContent=(latestRSI?.calculation_mode==='fixed'?'RSI · Fixed Long/Short':latestRSI?.calculation_mode==='uploaded_pine'?'Uploaded Pine RSI':'Corrected ML RSI')+(Number.isFinite(latestRSI?.rsi)?' '+latestRSI.rsi.toFixed(2):' · warming up')+(latestRSI?.calculation_mode==='fixed'?'':latestRSI?.calculation_mode==='uploaded_pine'?' · source ML thresholds unavailable':latestRSI?.ml_ready?'':' · partial ML window');
  // Source backgrounds are independent of the Static/ML marker visibility groups.
  if(chartStyle.showRSISignals)for(const [i,row]of rows.entries())for(const event of visibleRSIEvents(rsiByTime.get(row.time))){const style=chartStyle.rsiSignalStyles[event.kind];if(style?.backgroundVisible&&style.backgroundOpacity>0)shape('rect',{x:x(i)-step/2,y:paneTop+15,width:step,height:paneBottom-paneTop-15,fill:style.backgroundColor,opacity:style.backgroundOpacity,'data-rsi-background':event.kind})}
  for(const value of [0,30,50,70,100]){const yy=ry(value);shape('path',{d:`M${L} ${yy} H${R}`,stroke:[30,70].includes(value)?chartStyle.rsiStaticLevelColor:gridColor,'stroke-width':[30,70].includes(value)?.8:1,opacity:[30,70].includes(value)?.65:1,'data-rsi-static-level':[30,70].includes(value)?value:'','stroke-dasharray':'2 4'});shape('text',{x:R+8,y:yy+3,fill:axisText,'font-size':10}).textContent=value}
  if(chartStyle.showRSIThresholds)for(const [key,color]of [['ml_long',chartStyle.rsiLongColor],['ml_short',chartStyle.rsiShortColor]]){let d='',connected=false;rows.forEach((r,i)=>{const value=rsiByTime.get(r.time)?.thresholds?.[key];if(!Number.isFinite(value)){connected=false;return}d+=`${connected?' L':' M'}${x(i)} ${ry(value)}`;connected=true});if(d)shape('path',{d,fill:'none',stroke:color,'stroke-width':1.2,'data-rsi-line':key})}
  const rsiLine=shape('g',{'data-rsi-line':'rsi'});let segment='',segmentColor=null,previous=null;
  function flushRSI(){if(segment)shape('path',{d:segment,fill:'none',stroke:segmentColor,'stroke-width':chartStyle.rsiLineWidth,'data-rsi-segment':segmentColor},rsiLine);segment=''}
  rows.forEach((row,i)=>{const bar=rsiByTime.get(row.time);if(!Number.isFinite(bar?.rsi)){flushRSI();previous=null;return}const point=[x(i),ry(bar.rsi)],color=rsiTrendColor(bar,chartStyle);if(color!==segmentColor||!previous){flushRSI();segmentColor=color;segment=previous?`M${previous[0]} ${previous[1]} L${point[0]} ${point[1]}`:`M${point[0]} ${point[1]}`}else segment+=` L${point[0]} ${point[1]}`;previous=point});flushRSI();
  if(chartStyle.showRSIClusters)for(const [i,row]of rows.entries()){const bar=rsiByTime.get(row.time);if(Number.isFinite(bar?.rsi)&&Number.isInteger(bar?.rsi_cluster)&&bar.rsi_cluster>=0&&bar.rsi_cluster<3)shape('circle',{cx:x(i),cy:ry(bar.rsi),r:2,fill:chartStyle['rsiCluster'+(bar.rsi_cluster+1)],'data-rsi-cluster':bar.rsi_cluster})}
  if(chartStyle.showRSISignals&&chartStyle.showRSIPaneSignals)for(const [i,row]of rows.entries()){const bar=rsiByTime.get(row.time);for(const event of visibleRSIEvents(bar)){const style=chartStyle.rsiSignalStyles[event.kind],value=rsiEventLevel(bar,event,style.panePosition);if(!style.paneVisible||!Number.isFinite(value))continue;const marker=shape('text',{x:x(i),y:ry(value),fill:style.paneColor,'font-size':chartStyle.rsiSignalSize,'text-anchor':'middle','dominant-baseline':'central','data-rsi-pane-signal':event.side,'data-rsi-kind':event.kind,'data-rsi-value':value,'data-signal-time':row.time});marker.textContent=rsiMarkerSymbol(event,style.paneShape);shape('title',{},marker).textContent=rsiSignalTypes[event.kind].label+' · '+value.toFixed(2)+' RSI'}}
  const tags=[['rsi',latestRSI?.rsi,latestRSI?rsiTrendColor(latestRSI,chartStyle):chartStyle.rsiNeutralColor],...(chartStyle.showRSIThresholds?[['ml_long',latestRSI?.thresholds?.ml_long,chartStyle.rsiLongColor],['ml_short',latestRSI?.thresholds?.ml_short,chartStyle.rsiShortColor]]:[])].filter(([,value])=>Number.isFinite(value)).sort((a,b)=>b[1]-a[1]);let tagY=paneTop+14;
  for(const [key,value,color]of tags){const yy=Math.max(paneTop+23,Math.min(paneBottom-9,Math.max(ry(value),tagY+18)));tagY=yy;shape('rect',{x:R+3,y:yy-8,width:W-R-6,height:16,fill:color,rx:2,'data-rsi-value-tag':key});shape('text',{x:R+7,y:yy+4,fill:['#00ff00','#ffeb3b','#00e676'].includes(color.toLowerCase())?'#101918':'#ffffff','font-size':10}).textContent=value.toFixed(2)}
 }
 if(chartStyle.showRSIVWAP){let d='',connected=false;rows.forEach((r,i)=>{const value=rsiByTime.get(r.time)?.vwap;if(!Number.isFinite(value)){connected=false;return}d+=`${connected?' L':' M'}${x(i)} ${y(value)}`;connected=true});if(d)shape('path',{d,fill:'none',stroke:'#58c9c4','stroke-width':1.4,'data-rsi-vwap':'true'},plot)}
 if(chartStyle.showRSI&&chartStyle.rsiCollapsed)shape('text',{x:L,y:axisBottom-6,fill:axisText,'font-size':10,'data-rsi-minimized':'true'}).textContent='RSI · minimized';
 if(chartStyle.showRSISignals&&chartStyle.showRSIPriceSignals)for(const [i,row]of rows.entries()){const bar=rsiByTime.get(row.time),stack={};for(const event of visibleRSIEvents(bar)){const style=chartStyle.rsiSignalStyles[event.kind];if(!style.chartVisible)continue;const where=style.chartPosition,slot=stack[where]||0;stack[where]=slot+1;const above=where==='above'||where==='top',base=where==='top'?T+10:where==='bottom'?B-10:y(above?row.high:row.low)+(above?-12:12);const marker=shape('text',{x:x(i),y:base+(above?-1:1)*slot*(chartStyle.rsiSignalSize+2),fill:style.chartColor,'font-size':chartStyle.rsiSignalSize,'text-anchor':'middle','dominant-baseline':'central','data-rsi-signal':event.side,'data-rsi-kind':event.kind,'data-rsi-position':where,'data-signal-time':row.time},plot);marker.textContent=rsiMarkerSymbol(event,style.chartShape);shape('title',{},marker).textContent='RSI reference · '+rsiSignalTypes[event.kind].label}}
 rows.forEach((r,i)=>{
  const srBar=srByTime.get(r.time),srSignal=srBar?.signal;
  const tinted=chartStyle.paintSRCandles&&['Long','Short'].includes(srSignal);
  const color=tinted?(srSignal==='Long'?chartStyle.srLongCandle:chartStyle.srShortCandle):(r.close>=r.open?chartStyle.bull:chartStyle.bear);
  shape('path',{d:`M${x(i)} ${y(r.high)} V${y(r.low)}`,stroke:color,'stroke-width':1.2},plot);
  const candle=shape('rect',{x:x(i)-Math.max(.7,step*.3),y:Math.min(y(r.open),y(r.close)),width:Math.max(1.4,step*.6),height:Math.max(1.5,Math.abs(y(r.open)-y(r.close))),fill:color,rx:.5,'data-candle':r.close>=r.open?'bull':'bear','data-candle-close':r.close,'data-candle-time':r.time,'data-sr-candle':tinted?srSignal:''},plot);
  if(tinted)shape('title',{},candle).textContent='S/R '+srSignal+' signal candle · display tint only';
  const markers=[];if(chartStyle.showSRArrows&&srSignal)markers.push([srSignal,'S/R']);
  const bar=byTime.get(r.time);if(['sma_cross','sma_7_50','sr_sma_7_50'].includes(referenceSignalStrategy)&&chartStyle.showSMAArrows){const signal=referenceSignalStrategy==='sr_sma_7_50'?bar?.sma_signal:bar?.signal;if(signal)markers.push([signal,referenceSignalStrategy==='sma_cross'?'SMA 7/200':'SMA 7/50'])}
  if(chartStyle.showVolumeDeltaArrows&&volumeDeltaByTime.get(r.time)?.signal)markers.push([volumeDeltaByTime.get(r.time).signal,'Volume Delta']);
  if($('showSignals').checked)for(const [slot,[signal,source]]of markers.entries()){
   const marker=shape('text',{x:x(i),y:y(signal==='Long'?r.low:r.high)+(signal==='Long'?chartStyle.signalSize+slot*12:-9-slot*12),fill:signal==='Long'?chartStyle.longSignal:chartStyle.shortSignal,'text-anchor':'middle','font-size':chartStyle.signalSize,'data-signal':signal,'data-signal-source':source,'data-signal-time':r.time},plot);
   marker.textContent=source==='Volume Delta'?(signal==='Long'?'V▲':'V▼'):source.startsWith('SMA ')?(signal==='Long'?'S▲':'S▼'):(signal==='Long'?'▲':'▼');shape('title',{},marker).textContent=source+' reference signal';
  }
 });
 function line(value,color,label,dash=''){const yy=y(value);if(yy<T||yy>B)return;shape('path',{d:`M${L} ${yy} H${R}`,stroke:color,'stroke-dasharray':dash,'data-price-line':label},plot);shape('text',{x:L+5,y:yy-5,fill:color,'font-size':11},plot).textContent=label+' '+price(value)}
 for(const p of positions){if(Number(p.take_profit?.exchange_price)>0)line(Number(p.take_profit.exchange_price),'#7be3a4',p.take_profit.verified?'Exchange take profit':'Take profit (verification pending)','5 4');line(p.entry,'#edf3e8','Entry '+p.side,'6 4');line(stop(p),p.trailing?.active?'#b7a4ff':'#ff9b91',p.trailing?.active?'Trailing exchange stop':Number(p.stop)>0?'Exchange stop':'Estimated dollar stop','3 4');if(p.trailing?.enabled)line(Number(p.trailing.activation_price),'#58c9c4',p.trailing.active?'Trailing activated':'Trailing activation','8 5')}
 const last=rows.at(-1);
 const axisLabelPositions=[];
 function axisLabel(value,label,color){const yy=y(value),onScale=yy>=T&&yy<=B,base=Math.max(T+10,Math.min(B-10,yy));let at=label==='Now'&&onScale?yy:base;if(label!=='Now'){const candidates=[base,...Array.from({length:8},(_,i)=>base+(i%2===0?1:-1)*42*(Math.floor(i/2)+1))];at=candidates.find(candidate=>candidate>=T+10&&candidate<=B-10&&axisLabelPositions.every(other=>Math.abs(candidate-other)>=42))??base;}axisLabelPositions.push(at);const group=shape('g',{'data-extreme':label,'data-price-y':yy});if(onScale&&label!=='Now')shape('path',{d:`M${L} ${yy} H${R}`,stroke:color,'stroke-dasharray':'2 5',opacity:.65},plot);shape('rect',{x:R+4,y:at-10,width:W-R-6,height:20,fill:color,rx:3},group);shape('text',{x:R+9,y:at+4,fill:label==='Now'?'#181b12':'#f5f7ff','font-size':10},group).textContent=label+' '+price(value)+(onScale?'':yy<T?' ↑':' ↓');if(label==='Now'){const timerY=at>B-35?at-29:at+12;shape('rect',{x:R+4,y:timerY,width:W-R-6,height:17,fill:'#ffdf00',rx:2},group);shape('text',{x:R+9,y:timerY+12,fill:'#181b12','font-size':10,'data-candle-countdown':'true'},group).textContent=candleCountdown()}}
 const visibleHigh=Math.max(...rows.map(r=>Number(r.high))),visibleLow=Math.min(...rows.map(r=>Number(r.low)));
 const freshQuote=currentQuote&&currentQuote.symbol===$('symbol').value&&currentQuote.environment===mode&&Date.now()/1000-currentQuote.observed_at<20;
 if(freshQuote&&chartStyle.showLastPrice){axisLabel(currentQuote.price,'Now','#ffdf00');line(currentQuote.price,'#ffdf00','Current price','2 4')}
 axisLabel(visibleHigh,'High','#234578');axisLabel(visibleLow,'Low','#234578');updateCandleCountdown();

 for(const trade of current().trades.filter(t=>t.symbol===$('symbol').value))for(const [stamp,label,color]of [[trade.opened_at,'E','#d7f7ca'],[trade.closed_at,'X','#ffaaa1']]){const t=typeof stamp==='number'?(stamp>1e12?stamp/1000:stamp):Date.parse(stamp)/1000;const i=rows.findIndex((r,j)=>t>=r.time&&t<(rows[j+1]?.time??r.time+($('tf').value==='1'?60:86400)));if(i>=0)shape('text',{x:x(i),y:20,fill:color,'font-size':12,'text-anchor':'middle'}).textContent=label}
 if(chartMeasurement){const m=chartMeasurement,endPoint=m.end||m.start;const project=point=>{const absolute=chartRows.findIndex(r=>r.time===point.time),relative=absolute>=0?absolute-(end-rows.length):(point.time-rows[0].time)/chartIntervalSeconds($('tf').value);return {x:x(relative),y:y(point.price)}};const a=project(m.start),b=project(endPoint),color=endPoint.price>=m.start.price?'#57c6ec':'#ff9c9c';const group=shape('g',{'data-measurement':'true'},plot);shape('rect',{x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),width:Math.max(1,Math.abs(b.x-a.x)),height:Math.max(1,Math.abs(b.y-a.y)),fill:color,'fill-opacity':.15,stroke:color,'stroke-dasharray':'5 4'},group);shape('path',{d:`M${a.x} ${a.y} L${b.x} ${b.y}`,stroke:color,'stroke-width':1.5},group);for(const point of [a,b])shape('circle',{cx:point.x,cy:point.y,r:4,fill:color},group);
  if(m.end){const stats=measurementStats(m.start,m.end,chartRows),days=Math.floor(stats.seconds/86400),hours=Math.floor(stats.seconds%86400/3600),minutes=Math.floor(stats.seconds%3600/60);$('measurementReadout').textContent='Price move: '+(stats.delta>=0?'+':'')+marketPrice(stats.delta)+' USDT ('+(stats.percent>=0?'+':'')+stats.percent.toFixed(2)+'%) · '+(stats.bars??'—')+' candle intervals · '+(days?days+'d ':'')+(hours?hours+'h ':'')+minutes+'m · '+marketPrice(m.start.price)+' → '+marketPrice(m.end.price)+' · fees/leverage excluded';const labelWidth=Math.min(225,P-16),labelX=Math.max(L+8,Math.min(R-labelWidth-8,(a.x+b.x)/2-labelWidth/2)),labelY=Math.max(T+8,Math.min(B-52,Math.min(a.y,b.y)-50)),summary=shape('g',{'data-ruler-summary':'true'},group);shape('rect',{x:labelX,y:labelY,width:labelWidth,height:42,rx:5,fill:'#173947',stroke:color},summary);shape('text',{x:labelX+8,y:labelY+16,fill:'#f2faff','font-size':12},summary).textContent=(stats.percent>=0?'+':'')+stats.percent.toFixed(2)+'% · '+(stats.delta>=0?'+':'')+marketPrice(stats.delta)+' USDT';shape('text',{x:labelX+8,y:labelY+32,fill:'#c2e7f0','font-size':11},summary).textContent=(stats.bars??'—')+' candle intervals · '+(days?days+'d ':'')+(hours?hours+'h ':'')+minutes+'m'}else $('measurementReadout').textContent='First point set · Click a second point or drag to measure.';
 }else $('measurementReadout').textContent=rulerMode?'Measure ON · Hold and drag to the second point, then release. Or click the first and second points. The result stays on the chart.':'Measure: enable the ruler to compare signals. No orders are placed.';
 function chartPointer(e){const matrix=svg.getScreenCTM();if(matrix)return new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());const rect=svg.getBoundingClientRect();return {x:(e.clientX-rect.left)/rect.width*W,y:(e.clientY-rect.top)/rect.height*H}}
 function rulerPoint(e){const point=chartPointer(e),px=point.x,py=point.y,index=Math.max(0,Math.min(rows.length-1,Math.floor((px-L)/step)));return {time:rows[index].time,price:Math.max(.000000000001,min+(B-py)/(B-T)*(max-min))}}
 const cross=shape('path',{d:'',stroke:'#d8e5dd','stroke-dasharray':'3 4',opacity:.65,'pointer-events':'none','data-chart-crosshair':'true'});
 const dot=shape('circle',{r:3,fill:'#edf3e8',visibility:'hidden','pointer-events':'none'},plot);
 const timeTag=shape('g',{'data-crosshair-time':'true',visibility:'hidden','pointer-events':'none'}),timeBox=shape('rect',{height:20,rx:3,fill:'#354d40'},timeTag),timeText=shape('text',{fill:'#f4f7f2','font-size':10,'text-anchor':'middle'},timeTag);
 let hoverReadout=$('rsiHoverReadout');if(!hoverReadout){hoverReadout=document.createElement('p');hoverReadout.id='rsiHoverReadout';hoverReadout.className='note';hoverReadout.setAttribute('role','status');$('candleInfo').after(hoverReadout)}
 const signalTooltip=shape('g',{'data-signal-tooltip':'true',visibility:'hidden','pointer-events':'none'});let hoveredMarker=null;
 function inspect(i,show=true,pointerY=null){
  const r=rows[i],bar=rsiByTime.get(r.time),stamp=chartTime(r.time);
  $('candleInfo').textContent=stamp+' · O '+price(r.open)+'  H '+price(r.high)+'  L '+price(r.low)+'  C '+price(r.close);
  const number=value=>Number.isFinite(value)?value.toFixed(2):'—';
  hoverReadout.textContent=chartStyle.showRSI?'RSI at '+stamp+': '+number(bar?.rsi)+' · ML upper '+number(bar?.thresholds?.ml_long)+' · ML lower '+number(bar?.thresholds?.ml_short)+(visibleRSIEvents(bar).length?' · '+visibleRSIEvents(bar).map(event=>rsiSignalTypes[event.kind]?.label||event.kind).join(', '):' · no RSI signal'):'';
  if(show){const horizontal=pointerY===null?y(r.close):Math.max(T,Math.min(axisBottom,pointerY));cross.setAttribute('d',`M${x(i)} ${T} V${axisBottom} M${L} ${horizontal} H${R}`);dot.setAttribute('cx',x(i));dot.setAttribute('cy',y(r.close));dot.setAttribute('visibility','visible');timeText.textContent=chartTime(r.time,{time:!daily});const width=daily?110:155,xx=Math.max(L+width/2,Math.min(R-width/2,x(i)));timeBox.setAttribute('x',xx-width/2);timeBox.setAttribute('y',H-20);timeBox.setAttribute('width',width);timeText.setAttribute('x',xx);timeText.setAttribute('y',H-6);timeTag.setAttribute('visibility','visible')}
 }
 const inspectedIndex=rows.findIndex(r=>r.time===inspectedCandle);inspect(inspectedIndex>=0?inspectedIndex:rows.length-1,false);
 function point(e){
  const pointer=chartPointer(e),target=e.target.closest?.('[data-rsi-kind], [data-signal-source]');
  const markedTime=Number(target?.getAttribute('data-signal-time'));
  const marked=target?rows.findIndex(row=>row.time===markedTime):-1;
  const i=marked>=0?marked:Math.max(0,Math.min(rows.length-1,Math.floor((pointer.x-L)/step)));
  inspectedCandle=rows[i].time;inspect(i,true,pointer.y);
  if(hoveredMarker&&hoveredMarker!==target)hoveredMarker.classList.remove('signal-hovered');hoveredMarker=target||null;
  signalTooltip.replaceChildren();signalTooltip.setAttribute('visibility','hidden');
  if(target){target.classList.add('signal-hovered');const bar=rsiByTime.get(rows[i].time),kind=target.getAttribute('data-rsi-kind'),meta=rsiSignalTypes[kind],name=meta?.label||target.getAttribute('data-signal-source')+' reference signal';
   const width=Math.min(285,P-10),height=58,xx=Math.max(L,Math.min(R-width,pointer.x+14)),yy=Math.max(T,Math.min(axisBottom-height,pointer.y-65));
   shape('rect',{x:xx,y:yy,width,height,rx:5,fill:chartStyle.background,stroke:'#aabbb2'},signalTooltip);
   const details=meta?'RSI '+(Number.isFinite(bar?.rsi)?bar.rsi.toFixed(2):'—')+' · threshold '+(Number.isFinite(bar?.thresholds?.[meta.threshold])?bar.thresholds[meta.threshold].toFixed(2):'—'):'Candle close '+price(rows[i].close);
   [name,chartTime(rows[i].time),details].forEach((text,lineIndex)=>{shape('text',{x:xx+7,y:yy+15+lineIndex*16,fill:axisText,'font-size':10},signalTooltip).textContent=text});signalTooltip.setAttribute('visibility','visible');
  }
 }

 svg.onpointerdown=e=>{if(e.button!==0)return;
  if(e.pointerType==='touch'&&!rulerMode){
   e.preventDefault();chartTouches.set(e.pointerId,{x:e.clientX,y:e.clientY});svg.setPointerCapture(e.pointerId);
   if(chartTouches.size>=2){const [a,b]=[...chartTouches.values()],rect=svg.getBoundingClientRect(),mid=(a.x+b.x)/2,fraction=Math.max(0,Math.min(1,((mid-rect.left)/rect.width*W-L)/P));
    chartPinch={distance:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y)),bars:visibleBars,anchor:chartGeometry.firstIndex+fraction*(Math.min(visibleBars,chartRows.length)+chartStyle.rightGap)-.5,midY:(a.y+b.y)/2,shift:chartPriceShift,pricePerPixel:chartGeometry.pricePerPixel};chartDrag=null;suppressChartClick=true;return;
   }
  }
  if(rulerMode&&rulerPointer&&rulerPointer.id!==e.pointerId)return;
  const pointer=chartPointer(e),px=pointer.x,py=pointer.y;if(rulerMode&&px>=L&&px<=R&&py>=T&&py<=B){const point=rulerPoint(e);if(!chartMeasurement||chartMeasurement.end)chartMeasurement={start:point,end:null};else chartMeasurement.end=point;rulerPointer={id:e.pointerId,x:e.clientX,y:e.clientY};suppressChartClick=true;svg.setPointerCapture(e.pointerId);draw();return;}const rect=svg.getBoundingClientRect(),axisDrag=(e.clientX-rect.left)/rect.width*W>=R;chartDrag={axis:axisDrag,zoom:chartPriceZoom,id:e.pointerId,startX:e.clientX,startY:e.clientY,offset:chartOffset,step:step*svg.getBoundingClientRect().width/W,priceShift:chartPriceShift,pricePerPixel:chartGeometry.pricePerPixel,moved:false};suppressChartClick=false;svg.setPointerCapture(e.pointerId)};
 svg.onpointermove=e=>{
  if(e.pointerType==='touch'&&chartTouches.has(e.pointerId)){
   e.preventDefault();chartTouches.set(e.pointerId,{x:e.clientX,y:e.clientY});
   if(chartPinch&&chartTouches.size>=2){const [a,b]=[...chartTouches.values()],rect=svg.getBoundingClientRect(),distance=Math.max(1,Math.hypot(a.x-b.x,a.y-b.y)),fraction=Math.max(0,Math.min(1,(((a.x+b.x)/2-rect.left)/rect.width*W-L)/P)),next=Math.max(20,Math.min(500,Math.round(chartPinch.bars*chartPinch.distance/distance))),count=Math.min(next,chartRows.length),first=chartPinch.anchor-fraction*(count+chartStyle.rightGap)+.5;
    visibleBars=next;$('zoom').value=String(next);chartOffset=Math.max(0,Math.min(Math.max(0,chartRows.length-next),Math.round(chartRows.length-count-first)));chartPriceShift=chartPinch.shift+((a.y+b.y)/2-chartPinch.midY)*chartPinch.pricePerPixel;inspectedCandle=null;suppressChartClick=true;draw();return;
   }
  }
  if(rulerPointer?.id===e.pointerId){if(Math.hypot(e.clientX-rulerPointer.x,e.clientY-rulerPointer.y)>=3){chartMeasurement.end=rulerPoint(e);draw()}return;}
  if(chartDrag&&chartDrag.id===e.pointerId){const dx=e.clientX-chartDrag.startX,dy=e.clientY-chartDrag.startY;if(!chartDrag.moved&&Math.max(Math.abs(dx),Math.abs(dy))<5)return;chartDrag.moved=true;suppressChartClick=true;if(chartDrag.axis){chartPriceZoom=Math.max(.05,Math.min(30,chartDrag.zoom*Math.exp(-dy/180)));draw();return;}svg.classList.add('dragging');const next=Math.max(0,Math.min(Math.max(0,chartRows.length-visibleBars),chartDrag.offset+Math.round(dx/chartDrag.step)));const shift=chartDrag.priceShift+dy*chartDrag.pricePerPixel;if(next!==chartOffset||shift!==chartPriceShift){chartOffset=next;chartPriceShift=shift;inspectedCandle=null;draw()}return}
  if(e.pointerType==='mouse')point(e);
 };
 function endDrag(e){chartTouches.delete(e.pointerId);if(chartPinch){chartPinch=null;chartDrag=null;suppressChartClick=true;svg.classList.remove('dragging');if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);return;}if(rulerPointer?.id===e.pointerId){rulerPointer=null;if(chartMeasurement?.end)setRulerMode(false);if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);draw();return;}if(chartDrag?.id===e.pointerId){chartDrag=null;svg.classList.remove('dragging');if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId)}}
 svg.onpointerup=endDrag;svg.onpointercancel=endDrag;svg.onlostpointercapture=e=>{if(e.target!==svg||svg.hasPointerCapture(e.pointerId))return;chartTouches.delete(e.pointerId);chartPinch=null;rulerPointer=null;chartDrag=null;svg.classList.remove('dragging')};
 svg.ondblclick=e=>{const rect=svg.getBoundingClientRect();if((e.clientX-rect.left)/rect.width*W>=R){chartPriceZoom=1;chartPriceShift=0;draw()}};
 svg.onclick=e=>{if(rulerMode)return;if(suppressChartClick){suppressChartClick=false;return}point(e)};
 svg.onpointerleave=()=>{if(!chartDrag){cross.setAttribute('d','');dot.setAttribute('visibility','hidden');timeTag.setAttribute('visibility','hidden');signalTooltip.setAttribute('visibility','hidden');if(hoveredMarker)hoveredMarker.classList.remove('signal-hovered')}};
}
$('zoom').oninput=()=>{visibleBars=Number($('zoom').value);chartOffset=Math.min(chartOffset,Math.max(0,chartRows.length-visibleBars));draw()};
$('older').onclick=()=>{chartOffset=Math.min(Math.max(0,chartRows.length-visibleBars),chartOffset+Math.max(10,Math.floor(visibleBars/3)));draw()};
$('newer').onclick=()=>{chartOffset=Math.max(0,chartOffset-Math.max(10,Math.floor(visibleBars/3)));draw()};
$('latest').onclick=()=>{chartOffset=0;chartPriceShift=0;chartPriceZoom=1;inspectedCandle=null;chartLookback=1000;historyExhausted=false;loadChart()};
$('resetChartView').onclick=()=>{visibleBars=70;$('zoom').value='70';chartOffset=0;chartPriceShift=0;chartPriceZoom=1;inspectedCandle=null;draw()};
$('autoPriceScale').onclick=()=>{chartPriceZoom=1;chartPriceShift=0;draw()};
$('refresh').onclick=()=>loadChart();
for(const id of ['showLevels','showSignals','fitLevels'])$(id).onchange=()=>draw();
window.addEventListener('resize',()=>{if(chartRows.length)draw()});

// Wheel handling must be non-passive to zoom the plot instead of scrolling
// the dashboard. Keep the candle under the pointer anchored when bounds allow.
$('chart').addEventListener('wheel',e=>{
 if(!chartRows.length||!chartGeometry||!Number.isFinite(e.deltaY)||e.deltaY===0)return;
 e.preventDefault();
 const g=chartGeometry,rect=$('chart').getBoundingClientRect();if((e.clientX-rect.left)/rect.width*g.W>=g.R){chartPriceZoom=Math.max(.05,Math.min(30,chartPriceZoom*(e.deltaY<0?.85:1/.85)));draw();return;}const fraction=Math.max(0,Math.min(1,((e.clientX-rect.left)/rect.width*g.W-g.L)/g.P));
 const oldCount=Math.min(visibleBars,chartRows.length),anchor=g.firstIndex+fraction*(oldCount+chartStyle.rightGap)-.5;
 const next=Math.max(20,Math.min(500,Math.round(visibleBars*(e.deltaY<0?.85:1/.85))));
 const count=Math.min(next,chartRows.length),first=anchor-fraction*(count+chartStyle.rightGap)+.5;
 visibleBars=next;$('zoom').value=String(next);chartOffset=Math.max(0,Math.min(Math.max(0,chartRows.length-next),Math.round(chartRows.length-count-first)));
 inspectedCandle=null;draw();
},{passive:false});
