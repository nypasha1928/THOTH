let inspectedCandle=null;
function draw(){
 const svg=$('chart');svg.replaceChildren();
 const W=Math.max(410,Math.min(1000,svg.clientWidth||800)),H=W<600?370:440,L=12,R=W-82,T=30,B=H-42,P=R-L;
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 const ns='http://www.w3.org/2000/svg';
 function shape(tag,attrs,parent=svg){const node=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))node.setAttribute(k,v);parent.append(node);return node}
 const end=chartRows.length-chartOffset,rows=chartRows.slice(Math.max(0,end-visibleBars),end);
 $('title').textContent=$('symbol').value+' · '+$('tf').selectedOptions[0].textContent;
 $('older').disabled=chartOffset>=Math.max(0,chartRows.length-visibleBars);$('newer').disabled=chartOffset===0;$('latest').disabled=chartOffset===0;
 if(!rows.length){$('candleInfo').textContent='No candles available for this chart.';return}
 const start=rows[0].time,finish=rows.at(-1).time,step=P/rows.length,x=i=>L+(i+.5)*step;
 const byTime=new Map(analysis.map(b=>[b.time,b])),smaByTime=new Map(smaRows.map(s=>[s.time,s.value]));
 const settings=current().settings,showLevels=$('showLevels').checked,showSma=$('showSma').checked&&settings.sma_enabled;
 const enabled=key=>(!key.endsWith('_major')||settings.major)&&(!key.endsWith('_minor')||settings.minor);
 const active=rows.map(r=>byTime.get(r.time)).filter(Boolean),positions=current().positions.filter(p=>p.symbol===$('symbol').value);
 const stop=p=>Number(p.stop)>0?Number(p.stop):p.entry+(p.side==='Long'?-1:1)*p.stop_dollars/p.qty;
 const values=rows.flatMap(r=>[Number(r.low),Number(r.high)]);
 if($('fitLevels').checked){for(const b of active){if(showLevels)for(const [key,level]of Object.entries(b.levels||{}))if(enabled(key)&&Number.isFinite(level.value))values.push(level.value)}if(showSma)for(const r of rows){const v=smaByTime.get(r.time);if(Number.isFinite(v))values.push(v)}for(const p of positions)values.push(p.entry,stop(p))}
 const bottom=Math.min(...values),top=Math.max(...values),pad=Math.max((top-bottom)*.12,Math.abs(top)*.00002,.000001),min=bottom-pad,max=top+pad;
 const y=p=>B-(Number(p)-min)/(max-min)*(B-T),price=p=>Number(p).toLocaleString('en-US',{maximumFractionDigits:Math.abs(p)<1?6:Math.abs(p)<10?4:2});
 for(let i=0;i<6;i++){const value=min+(max-min)*i/5,yy=y(value);shape('path',{d:`M${L} ${yy} H${R}`,stroke:'#263c32','stroke-dasharray':'2 4'});shape('text',{x:R+8,y:yy+4,fill:'#a9bbb0','font-size':11}).textContent=price(value)}
 const defs=shape('defs',{}),clip=shape('clipPath',{id:'plotClip'},defs);shape('rect',{x:L,y:T-10,width:P,height:B-T+20},clip);
 const plot=shape('g',{'clip-path':'url(#plotClip)'});
 for(let j=0;j<4;j++){const i=Math.round(j*(rows.length-1)/3),date=new Date(rows[i].time*1000);shape('path',{d:`M${x(i)} ${T} V${B}`,stroke:'#20352c'});shape('text',{x:x(i),y:H-15,fill:'#a9bbb0','font-size':10,'text-anchor':j===0?'start':j===3?'end':'middle'}).textContent=$('tf').value==='1'?date.toISOString().slice(11,16):date.toISOString().slice(5,10)}
 shape('text',{x:R+8,y:H-15,fill:'#849b8b','font-size':10}).textContent='UTC';
 if(showLevels){const keys=[...new Set(active.flatMap(b=>Object.keys(b.levels||{})))];for(const key of keys){if(!enabled(key))continue;let d='',connected=false;rows.forEach((r,i)=>{const level=byTime.get(r.time)?.levels?.[key];if(!level||!Number.isFinite(level.value)){connected=false;return}d+=connected?` H${x(i)} V${y(level.value)}`:` M${x(i)} ${y(level.value)}`;connected=true});shape('path',{d,fill:'none',stroke:key.endsWith('_major')?'#91d5a1':'#8faaf3','stroke-dasharray':key.endsWith('_minor')?'4 5':'','stroke-width':key.startsWith('long_')?1.6:1,opacity:.85},plot)}}
 if(showSma){let d='',connected=false;rows.forEach((r,i)=>{const value=smaByTime.get(r.time);if(!Number.isFinite(value)){connected=false;return}d+=`${connected?' L':' M'}${x(i)} ${y(value)}`;connected=true});shape('path',{d,fill:'none',stroke:'#edcb77','stroke-width':2},plot)}
 rows.forEach((r,i)=>{const color=r.close>=r.open?'#50d5a0':'#fa827c';shape('path',{d:`M${x(i)} ${y(r.high)} V${y(r.low)}`,stroke:color,'stroke-width':1.2},plot);shape('rect',{x:x(i)-Math.max(.7,step*.3),y:Math.min(y(r.open),y(r.close)),width:Math.max(1.4,step*.6),height:Math.max(1.5,Math.abs(y(r.open)-y(r.close))),fill:color,rx:.5},plot);const bar=byTime.get(r.time);if($('showSignals').checked&&bar?.signal)shape('text',{x:x(i),y:y(bar.signal==='Long'?r.low:r.high)+(bar.signal==='Long'?17:-9),fill:bar.signal==='Long'?'#75f2b1':'#ffaaa1','text-anchor':'middle','font-size':13},plot).textContent=bar.signal==='Long'?'▲':'▼'});
 function line(value,color,label,dash=''){const yy=y(value);if(yy<T||yy>B)return;shape('path',{d:`M${L} ${yy} H${R}`,stroke:color,'stroke-dasharray':dash},plot);shape('text',{x:L+5,y:yy-5,fill:color,'font-size':11},plot).textContent=label+' '+price(value)}
 for(const p of positions){line(p.entry,'#edf3e8','Entry '+p.side,'6 4');line(stop(p),'#ff9b91',Number(p.stop)>0?'Exchange stop':'Estimated dollar stop','3 4')}
 const last=rows.at(-1);line(last.close,'#62deb0','Last close','2 4');
 for(const trade of current().trades.filter(t=>t.symbol===$('symbol').value))for(const [stamp,label,color]of [[trade.opened_at,'E','#d7f7ca'],[trade.closed_at,'X','#ffaaa1']]){const t=typeof stamp==='number'?(stamp>1e12?stamp/1000:stamp):Date.parse(stamp)/1000;const i=rows.findIndex((r,j)=>t>=r.time&&t<(rows[j+1]?.time??r.time+($('tf').value==='1'?60:86400)));if(i>=0)shape('text',{x:x(i),y:20,fill:color,'font-size':12,'text-anchor':'middle'}).textContent=label}
 const cross=shape('path',{d:'',stroke:'#d8e5dd','stroke-dasharray':'3 4',opacity:.5,'pointer-events':'none'},plot);
 const dot=shape('circle',{r:3,fill:'#edf3e8',visibility:'hidden','pointer-events':'none'},plot);
 function inspect(i,show=true){const r=rows[i];$('candleInfo').textContent=new Date(r.time*1000).toISOString().replace('T',' ').slice(0,16)+' UTC · O '+price(r.open)+'  H '+price(r.high)+'  L '+price(r.low)+'  C '+price(r.close);if(show){cross.setAttribute('d',`M${x(i)} ${T} V${B} M${L} ${y(r.close)} H${R}`);dot.setAttribute('cx',x(i));dot.setAttribute('cy',y(r.close));dot.setAttribute('visibility','visible')}}
 const inspectedIndex=rows.findIndex(r=>r.time===inspectedCandle);inspect(inspectedIndex>=0?inspectedIndex:rows.length-1,false);
 function point(e){const rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*W;const i=Math.max(0,Math.min(rows.length-1,Math.floor((px-L)/step)));inspectedCandle=rows[i].time;inspect(i)}
 svg.onpointermove=e=>{if(e.pointerType==='mouse')point(e)};svg.onclick=point;
 svg.onpointerleave=()=>{cross.setAttribute('d','');dot.setAttribute('visibility','hidden')};
}
$('zoom').oninput=()=>{visibleBars=Number($('zoom').value);chartOffset=Math.min(chartOffset,Math.max(0,chartRows.length-visibleBars));draw()};
$('older').onclick=()=>{chartOffset=Math.min(Math.max(0,chartRows.length-visibleBars),chartOffset+Math.max(10,Math.floor(visibleBars/3)));draw()};
$('newer').onclick=()=>{chartOffset=Math.max(0,chartOffset-Math.max(10,Math.floor(visibleBars/3)));draw()};
$('latest').onclick=()=>{chartOffset=0;inspectedCandle=null;draw()};
$('refresh').onclick=()=>loadChart();
for(const id of ['showLevels','showSma','showSignals','fitLevels'])$(id).onchange=()=>draw();
window.addEventListener('resize',()=>{if(chartRows.length)draw()});
