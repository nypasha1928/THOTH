let inspectedCandle=null;
function draw(){
 const svg=$('chart');svg.replaceChildren();
 const expanded=$('chartCard').classList.contains('chart-expanded');
 const W=Math.max(410,Math.min(1800,svg.clientWidth||800)),H=expanded?Math.max(330,window.innerHeight-270):(W<600?370:460),L=12,R=W-82,T=30,B=H-42,P=R-L;
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 const ns='http://www.w3.org/2000/svg';
 function shape(tag,attrs,parent=svg){const node=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))node.setAttribute(k,v);parent.append(node);return node}
 const end=chartRows.length-chartOffset,rows=chartRows.slice(Math.max(0,end-visibleBars),end);
 $('title').textContent=$('symbol').value+' · '+$('tf').selectedOptions[0].textContent;
 $('older').disabled=chartOffset>=Math.max(0,chartRows.length-visibleBars);$('newer').disabled=chartOffset===0;$('latest').disabled=chartOffset===0;
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
 const bottom=Math.min(...values),top=Math.max(...values),pad=Math.max((top-bottom)*.12,Math.abs(top)*.00002,.000001),min=bottom-pad,max=top+pad;
 const y=p=>B-(Number(p)-min)/(max-min)*(B-T),price=p=>Number(p).toLocaleString('en-US',{maximumFractionDigits:Math.abs(p)<1?6:Math.abs(p)<10?4:2});
 for(let i=0;i<6;i++){const value=min+(max-min)*i/5,yy=y(value);shape('path',{d:`M${L} ${yy} H${R}`,stroke:'#263c32','stroke-dasharray':'2 4'});shape('text',{x:R+8,y:yy+4,fill:'#a9bbb0','font-size':11}).textContent=price(value)}
 const defs=shape('defs',{}),clip=shape('clipPath',{id:'plotClip'},defs);shape('rect',{x:L,y:T-10,width:P,height:B-T+20},clip);
 const plot=shape('g',{'clip-path':'url(#plotClip)'});
 for(let j=0;j<4;j++){const i=Math.round(j*(rows.length-1)/3),date=new Date(rows[i].time*1000);shape('path',{d:`M${x(i)} ${T} V${B}`,stroke:'#20352c'});shape('text',{x:x(i),y:H-15,fill:'#a9bbb0','font-size':10,'text-anchor':j===0?'start':j===3?'end':'middle'}).textContent=$('tf').value==='1'?date.toISOString().slice(11,16):date.toISOString().slice(5,10)}
 shape('text',{x:R+8,y:H-15,fill:'#849b8b','font-size':10}).textContent='UTC';
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
 for(const trade of current().trades.filter(t=>t.symbol===$('symbol').value))for(const [stamp,label,color]of [[trade.opened_at,'E','#d7f7ca'],[trade.closed_at,'X','#ffaaa1']]){const t=typeof stamp==='number'?(stamp>1e12?stamp/1000:stamp):Date.parse(stamp)/1000;const i=rows.findIndex((r,j)=>t>=r.time&&t<(rows[j+1]?.time??r.time+($('tf').value==='1'?60:86400)));if(i>=0)shape('text',{x:x(i),y:20,fill:color,'font-size':12,'text-anchor':'middle'}).textContent=label}
 const cross=shape('path',{d:'',stroke:'#d8e5dd','stroke-dasharray':'3 4',opacity:.5,'pointer-events':'none'},plot);
 const dot=shape('circle',{r:3,fill:'#edf3e8',visibility:'hidden','pointer-events':'none'},plot);
 function inspect(i,show=true){const r=rows[i];$('candleInfo').textContent=new Date(r.time*1000).toISOString().replace('T',' ').slice(0,16)+' UTC · O '+price(r.open)+'  H '+price(r.high)+'  L '+price(r.low)+'  C '+price(r.close);if(show){cross.setAttribute('d',`M${x(i)} ${T} V${B} M${L} ${y(r.close)} H${R}`);dot.setAttribute('cx',x(i));dot.setAttribute('cy',y(r.close));dot.setAttribute('visibility','visible')}}
 const inspectedIndex=rows.findIndex(r=>r.time===inspectedCandle);inspect(inspectedIndex>=0?inspectedIndex:rows.length-1,false);
 function point(e){const rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*W;const i=Math.max(0,Math.min(rows.length-1,Math.floor((px-L)/step)));inspectedCandle=rows[i].time;inspect(i)}
 svg.onpointerdown=e=>{if(e.button!==0)return;chartDrag={id:e.pointerId,startX:e.clientX,startY:e.clientY,offset:chartOffset,step:step*svg.getBoundingClientRect().width/W,moved:false};suppressChartClick=false;svg.setPointerCapture(e.pointerId)};
 svg.onpointermove=e=>{
  if(chartDrag&&chartDrag.id===e.pointerId){const dx=e.clientX-chartDrag.startX,dy=e.clientY-chartDrag.startY;if(!chartDrag.moved&&Math.abs(dx)<5)return;if(e.pointerType==='touch'&&!chartDrag.moved&&Math.abs(dy)>Math.abs(dx))return;chartDrag.moved=true;suppressChartClick=true;svg.classList.add('dragging');const next=Math.max(0,Math.min(Math.max(0,chartRows.length-visibleBars),chartDrag.offset+Math.round(dx/chartDrag.step)));if(next!==chartOffset){chartOffset=next;inspectedCandle=null;draw()}return}
  if(e.pointerType==='mouse')point(e);
 };
 function endDrag(e){if(chartDrag?.id===e.pointerId){chartDrag=null;svg.classList.remove('dragging');if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId)}}
 svg.onpointerup=endDrag;svg.onpointercancel=endDrag;svg.onlostpointercapture=()=>{chartDrag=null;svg.classList.remove('dragging')};
 svg.onclick=e=>{if(suppressChartClick){suppressChartClick=false;return}point(e)};
 svg.onpointerleave=()=>{if(!chartDrag){cross.setAttribute('d','');dot.setAttribute('visibility','hidden')}};
}
$('zoom').oninput=()=>{visibleBars=Number($('zoom').value);chartOffset=Math.min(chartOffset,Math.max(0,chartRows.length-visibleBars));draw()};
$('older').onclick=()=>{chartOffset=Math.min(Math.max(0,chartRows.length-visibleBars),chartOffset+Math.max(10,Math.floor(visibleBars/3)));draw()};
$('newer').onclick=()=>{chartOffset=Math.max(0,chartOffset-Math.max(10,Math.floor(visibleBars/3)));draw()};
$('latest').onclick=()=>{chartOffset=0;inspectedCandle=null;draw()};
$('refresh').onclick=()=>loadChart();
for(const id of ['showLevels','showSma','showSignals','fitLevels'])$(id).onchange=()=>draw();
window.addEventListener('resize',()=>{if(chartRows.length)draw()});
