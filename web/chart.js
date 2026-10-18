let inspectedCandle=null;
function draw(){
 const svg=$('chart');svg.replaceChildren();
 const expanded=$('chartCard').classList.contains('chart-expanded');
 const W=Math.max(410,Math.min(1800,svg.clientWidth||800)),H=expanded?Math.max(330,window.innerHeight-270):(W<600?370:460),L=12,R=W-112,T=30,B=H-55,P=R-L;
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 const ns='http://www.w3.org/2000/svg';
 function shape(tag,attrs,parent=svg){const node=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))node.setAttribute(k,v);parent.append(node);return node}
 const end=chartRows.length-chartOffset,rows=chartRows.slice(Math.max(0,end-visibleBars),end);
 $('chartNavigationStatus').textContent=chartOffset>0||chartPriceShift!==0||chartPriceZoom!==1?'Exploring chart · auto refresh paused. Latest candle returns to the live edge and auto scale.':'Wheel over candles: time zoom · Drag/wheel on right price scale: price zoom · Double-click price scale: auto scale';
 $('title').textContent=$('symbol').value+' · '+$('tf').selectedOptions[0].textContent;
 $('older').disabled=chartOffset>=Math.max(0,chartRows.length-visibleBars);$('newer').disabled=chartOffset===0;$('latest').disabled=chartOffset===0&&chartPriceShift===0&&chartPriceZoom===1;
 if(!rows.length){$('candleInfo').textContent='No candles available for this chart.';return}
 const start=rows[0].time,finish=rows.at(-1).time,step=P/(rows.length+12),x=i=>L+(i+.5)*step;
 const levelSnapshot=analysis.filter(b=>b.time<=finish).at(-1);
 const family=key=>(key.startsWith('long_')?'long_':'short_')+(key.endsWith('_major')?'major':'minor');
 const byTime=new Map(analysis.map(b=>[b.time,b])),smaByTime=new Map(smaRows.map(s=>[s.time,s.value]));
 const settings=current().settings,showLevels=$('showLevels').checked,showSma=$('showSma').checked&&settings.sma_enabled;
 const enabled=key=>(!key.endsWith('_major')||settings.major)&&(!key.endsWith('_minor')||settings.minor)&&chartStyle[family(key)]?.visible;
 const active=rows.map(r=>byTime.get(r.time)).filter(Boolean),positions=current().positions.filter(p=>p.symbol===$('symbol').value);
 const stop=p=>Number(p.stop)>0?Number(p.stop):p.entry+(p.side==='Long'?-1:1)*p.stop_dollars/p.qty;
 const values=rows.flatMap(r=>[Number(r.low),Number(r.high)]);
 if($('fitLevels').checked){if(showLevels)for(const [key,level]of Object.entries(levelSnapshot?.levels||{}))if(enabled(key)&&Number.isFinite(level.value))values.push(level.value);if(showSma)for(const r of rows){const v=smaByTime.get(r.time);if(Number.isFinite(v))values.push(v)}for(const p of positions)values.push(p.entry,stop(p))}
 const bottom=Math.min(...values),top=Math.max(...values),pad=Math.max((top-bottom)*.12,Math.abs(top)*.00002,.000001),center=(bottom+top)/2+chartPriceShift,half=((top-bottom)/2+pad)*chartPriceZoom,min=center-half,max=center+half;
 chartGeometry={W,H,L,R,T,B,P,step,firstIndex:Math.max(0,end-visibleBars),pricePerPixel:(max-min)/(B-T)*H/svg.getBoundingClientRect().height};
 const y=p=>B-(Number(p)-min)/(max-min)*(B-T),price=marketPrice;
 const axis=shape('rect',{x:R+2,y:T-14,width:W-R-2,height:B-T+28,fill:'#14221c','data-price-axis':'true'});shape('title',{},axis).textContent='Drag up/down or scroll to scale prices. Double-click for auto scale.';
 for(let i=0;i<6;i++){const value=min+(max-min)*i/5,yy=y(value);shape('path',{d:`M${L} ${yy} H${R}`,stroke:'#263c32','stroke-dasharray':'2 4'});shape('text',{x:R+8,y:yy+4,fill:'#a9bbb0','font-size':11,'data-price-tick':value}).textContent=price(value)}
 const defs=shape('defs',{}),clip=shape('clipPath',{id:'plotClip'},defs);shape('rect',{x:L,y:T-10,width:P,height:B-T+20},clip);
 const plot=shape('g',{'clip-path':'url(#plotClip)'});
 const daily=$('tf').value==='D',labelWidth=daily?94:45,labelEvery=Math.max(1,Math.ceil(labelWidth/step));
 rows.forEach((r,i)=>{shape('path',{d:`M${x(i)} ${B} v5`,stroke:'#62776b','data-time-tick':r.time});if(i%labelEvery===0){const stamp=new Date(r.time*1000).toISOString();shape('path',{d:`M${x(i)} ${T} V${B}`,stroke:'#20352c'});shape('text',{x:x(i),y:H-22,fill:'#b3c6b9','font-size':10,'text-anchor':i===0?'start':'middle','data-time-label':r.time}).textContent=daily?stamp.slice(0,10):stamp.slice(11,16)}});
 shape('text',{x:R+8,y:H-22,fill:'#849b8b','font-size':10}).textContent='UTC';
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
  shape('title',{},ray).textContent=key.replaceAll('_',' ')+' · '+price(level.value)+' · active at '+new Date(levelSnapshot.time*1000).toISOString();
 }}
 if(showSma){let d='',connected=false;rows.forEach((r,i)=>{const value=smaByTime.get(r.time);if(!Number.isFinite(value)){connected=false;return}d+=`${connected?' L':' M'}${x(i)} ${y(value)}`;connected=true});shape('path',{d,fill:'none',stroke:chartStyle.sma,'stroke-width':2},plot)}
 rows.forEach((r,i)=>{const color=r.close>=r.open?chartStyle.bull:chartStyle.bear;shape('path',{d:`M${x(i)} ${y(r.high)} V${y(r.low)}`,stroke:color,'stroke-width':1.2},plot);shape('rect',{x:x(i)-Math.max(.7,step*.3),y:Math.min(y(r.open),y(r.close)),width:Math.max(1.4,step*.6),height:Math.max(1.5,Math.abs(y(r.open)-y(r.close))),fill:color,rx:.5,'data-candle':r.close>=r.open?'bull':'bear'},plot);const bar=byTime.get(r.time);if($('showSignals').checked&&bar?.signal)shape('text',{x:x(i),y:y(bar.signal==='Long'?r.low:r.high)+(bar.signal==='Long'?chartStyle.signalSize:-9),fill:bar.signal==='Long'?chartStyle.longSignal:chartStyle.shortSignal,'text-anchor':'middle','font-size':chartStyle.signalSize,'data-signal':bar.signal},plot).textContent=bar.signal==='Long'?'▲':'▼'});
 function line(value,color,label,dash=''){const yy=y(value);if(yy<T||yy>B)return;shape('path',{d:`M${L} ${yy} H${R}`,stroke:color,'stroke-dasharray':dash},plot);shape('text',{x:L+5,y:yy-5,fill:color,'font-size':11},plot).textContent=label+' '+price(value)}
 for(const p of positions){line(p.entry,'#edf3e8','Entry '+p.side,'6 4');line(stop(p),'#ff9b91',Number(p.stop)>0?'Exchange stop':'Estimated dollar stop','3 4')}
 const last=rows.at(-1);line(last.close,'#62deb0','Last close','2 4');
 const axisLabelPositions=[];
 function axisLabel(value,label,color){const yy=y(value),onScale=yy>=T&&yy<=B,base=Math.max(T+10,Math.min(B-10,yy));let at=base;for(const other of axisLabelPositions)if(Math.abs(at-other)<22)at=other+22<=B-10?other+22:other-22;axisLabelPositions.push(at);const group=shape('g',{'data-extreme':label});if(onScale)shape('path',{d:`M${L} ${yy} H${R}`,stroke:color,'stroke-dasharray':'2 5',opacity:.65},plot);shape('rect',{x:R+4,y:at-10,width:W-R-6,height:20,fill:color,rx:3},group);shape('text',{x:R+9,y:at+4,fill:'#f5f7ff','font-size':10},group).textContent=label+' '+price(value)+(onScale?'':yy<T?' ↑':' ↓')}
 const visibleHigh=Math.max(...rows.map(r=>Number(r.high))),visibleLow=Math.min(...rows.map(r=>Number(r.low)));
 axisLabel(visibleHigh,'High','#234578');axisLabel(visibleLow,'Low','#234578');
 const freshQuote=currentQuote&&currentQuote.symbol===$('symbol').value&&currentQuote.environment===mode&&Date.now()/1000-currentQuote.observed_at<20;
 if(freshQuote){axisLabel(currentQuote.price,'Now','#276c55');line(currentQuote.price,'#63d3a2','Current price','3 4')}

 for(const trade of current().trades.filter(t=>t.symbol===$('symbol').value))for(const [stamp,label,color]of [[trade.opened_at,'E','#d7f7ca'],[trade.closed_at,'X','#ffaaa1']]){const t=typeof stamp==='number'?(stamp>1e12?stamp/1000:stamp):Date.parse(stamp)/1000;const i=rows.findIndex((r,j)=>t>=r.time&&t<(rows[j+1]?.time??r.time+($('tf').value==='1'?60:86400)));if(i>=0)shape('text',{x:x(i),y:20,fill:color,'font-size':12,'text-anchor':'middle'}).textContent=label}
 const cross=shape('path',{d:'',stroke:'#d8e5dd','stroke-dasharray':'3 4',opacity:.5,'pointer-events':'none'},plot);
 const dot=shape('circle',{r:3,fill:'#edf3e8',visibility:'hidden','pointer-events':'none'},plot);
 const timeTag=shape('g',{'data-crosshair-time':'true',visibility:'hidden','pointer-events':'none'}),timeBox=shape('rect',{height:20,rx:3,fill:'#354d40'},timeTag),timeText=shape('text',{fill:'#f4f7f2','font-size':10,'text-anchor':'middle'},timeTag);
 function inspect(i,show=true){const r=rows[i];$('candleInfo').textContent=new Date(r.time*1000).toISOString().replace('T',' ').slice(0,16)+' UTC · O '+price(r.open)+'  H '+price(r.high)+'  L '+price(r.low)+'  C '+price(r.close);if(show){cross.setAttribute('d',`M${x(i)} ${T} V${B} M${L} ${y(r.close)} H${R}`);dot.setAttribute('cx',x(i));dot.setAttribute('cy',y(r.close));dot.setAttribute('visibility','visible');const text=new Date(r.time*1000).toISOString();timeText.textContent=(daily?text.slice(0,10):text.slice(0,10)+' '+text.slice(11,16))+' UTC';const width=daily?100:145,xx=Math.max(L+width/2,Math.min(R-width/2,x(i)));timeBox.setAttribute('x',xx-width/2);timeBox.setAttribute('y',H-20);timeBox.setAttribute('width',width);timeText.setAttribute('x',xx);timeText.setAttribute('y',H-6);timeTag.setAttribute('visibility','visible')}}
 const inspectedIndex=rows.findIndex(r=>r.time===inspectedCandle);inspect(inspectedIndex>=0?inspectedIndex:rows.length-1,false);
 function point(e){const rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*W;const i=Math.max(0,Math.min(rows.length-1,Math.floor((px-L)/step)));inspectedCandle=rows[i].time;inspect(i)}
 svg.onpointerdown=e=>{if(e.button!==0)return;const rect=svg.getBoundingClientRect(),axisDrag=(e.clientX-rect.left)/rect.width*W>=R;chartDrag={axis:axisDrag,zoom:chartPriceZoom,id:e.pointerId,startX:e.clientX,startY:e.clientY,offset:chartOffset,step:step*svg.getBoundingClientRect().width/W,priceShift:chartPriceShift,pricePerPixel:chartGeometry.pricePerPixel,moved:false};suppressChartClick=false;svg.setPointerCapture(e.pointerId)};
 svg.onpointermove=e=>{
  if(chartDrag&&chartDrag.id===e.pointerId){const dx=e.clientX-chartDrag.startX,dy=e.clientY-chartDrag.startY;if(!chartDrag.moved&&Math.max(Math.abs(dx),Math.abs(dy))<5)return;if(e.pointerType==='touch'&&!chartDrag.moved&&Math.abs(dy)>Math.abs(dx))return;chartDrag.moved=true;suppressChartClick=true;if(chartDrag.axis){chartPriceZoom=Math.max(.05,Math.min(30,chartDrag.zoom*Math.exp(-dy/180)));draw();return;}svg.classList.add('dragging');const next=Math.max(0,Math.min(Math.max(0,chartRows.length-visibleBars),chartDrag.offset+Math.round(dx/chartDrag.step)));const shift=e.pointerType==='touch'?chartDrag.priceShift:chartDrag.priceShift+dy*chartDrag.pricePerPixel;if(next!==chartOffset||shift!==chartPriceShift){chartOffset=next;chartPriceShift=shift;inspectedCandle=null;draw()}return}
  if(e.pointerType==='mouse')point(e);
 };
 function endDrag(e){if(chartDrag?.id===e.pointerId){chartDrag=null;svg.classList.remove('dragging');if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId)}}
 svg.onpointerup=endDrag;svg.onpointercancel=endDrag;svg.onlostpointercapture=()=>{chartDrag=null;svg.classList.remove('dragging')};
 svg.ondblclick=e=>{const rect=svg.getBoundingClientRect();if((e.clientX-rect.left)/rect.width*W>=R){chartPriceZoom=1;chartPriceShift=0;draw()}};
 svg.onclick=e=>{if(suppressChartClick){suppressChartClick=false;return}point(e)};
 svg.onpointerleave=()=>{if(!chartDrag){cross.setAttribute('d','');dot.setAttribute('visibility','hidden');timeTag.setAttribute('visibility','hidden')}};
}
$('zoom').oninput=()=>{visibleBars=Number($('zoom').value);chartOffset=Math.min(chartOffset,Math.max(0,chartRows.length-visibleBars));draw()};
$('older').onclick=()=>{chartOffset=Math.min(Math.max(0,chartRows.length-visibleBars),chartOffset+Math.max(10,Math.floor(visibleBars/3)));draw()};
$('newer').onclick=()=>{chartOffset=Math.max(0,chartOffset-Math.max(10,Math.floor(visibleBars/3)));draw()};
$('latest').onclick=()=>{chartOffset=0;chartPriceShift=0;chartPriceZoom=1;inspectedCandle=null;draw()};
$('resetChartView').onclick=()=>{visibleBars=70;$('zoom').value='70';chartOffset=0;chartPriceShift=0;chartPriceZoom=1;inspectedCandle=null;draw()};
$('autoPriceScale').onclick=()=>{chartPriceZoom=1;chartPriceShift=0;draw()};
$('refresh').onclick=()=>loadChart();
for(const id of ['showLevels','showSma','showSignals','fitLevels'])$(id).onchange=()=>draw();
window.addEventListener('resize',()=>{if(chartRows.length)draw()});

// Wheel handling must be non-passive to zoom the plot instead of scrolling
// the dashboard. Keep the candle under the pointer anchored when bounds allow.
$('chart').addEventListener('wheel',e=>{
 if(!chartRows.length||!chartGeometry||!Number.isFinite(e.deltaY)||e.deltaY===0)return;
 e.preventDefault();
 const g=chartGeometry,rect=$('chart').getBoundingClientRect();if((e.clientX-rect.left)/rect.width*g.W>=g.R){chartPriceZoom=Math.max(.05,Math.min(30,chartPriceZoom*(e.deltaY<0?.85:1/.85)));draw();return;}const fraction=Math.max(0,Math.min(1,((e.clientX-rect.left)/rect.width*g.W-g.L)/g.P));
 const oldCount=Math.min(visibleBars,chartRows.length),anchor=g.firstIndex+fraction*(oldCount+12)-.5;
 const next=Math.max(20,Math.min(500,Math.round(visibleBars*(e.deltaY<0?.85:1/.85))));
 const count=Math.min(next,chartRows.length),first=anchor-fraction*(count+12)+.5;
 visibleBars=next;$('zoom').value=String(next);chartOffset=Math.max(0,Math.min(Math.max(0,chartRows.length-next),Math.round(chartRows.length-count-first)));
 inspectedCandle=null;draw();
},{passive:false});
