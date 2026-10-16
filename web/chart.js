function draw(){
 const svg=$('chart');svg.replaceChildren();if(!chartRows.length)return;
 const ns='http://www.w3.org/2000/svg';
 function shape(tag,attrs){const node=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))node.setAttribute(k,v);svg.append(node);return node}
 const end=chartRows.length-chartOffset,rows=chartRows.slice(Math.max(0,end-visibleBars),end);
 if(!rows.length)return;
 const start=rows[0].time,finish=rows.at(-1).time;
 svg.onclick=e=>{const rect=svg.getBoundingClientRect();const i=Math.max(0,Math.min(rows.length-1,Math.floor((e.clientX-rect.left)/rect.width*640/(550/rows.length))));const r=rows[i];$('candleInfo').textContent=new Date(r.time*1000).toISOString().replace('T',' ').slice(0,16)+' UTC · O '+r.open+' H '+r.high+' L '+r.low+' C '+r.close};
 const values=rows.flatMap(r=>[r.low,r.high]);
 const active=analysis.filter(b=>b.time>=start&&b.time<=finish);
 const enabled=key=>(!key.endsWith('_major')||current().settings.major)&&(!key.endsWith('_minor')||current().settings.minor);
 for(const b of active){for(const [key,level] of Object.entries(b.levels))if(enabled(key))values.push(level.value);if(current().settings.sma_enabled&&b.sma)values.push(b.sma)}
 const positions=current().positions.filter(p=>p.symbol===$('symbol').value);
 for(const p of positions){values.push(p.entry,p.entry+(p.side==='Long'?-1:1)*p.stop_dollars/p.qty)}
 const min=Math.min(...values),max=Math.max(...values),step=550/rows.length;
 const y=p=>260-(p-min)/(max-min||1)*225;
 for(let i=0;i<5;i++){const price=min+(max-min)*i/4,yy=y(price);shape('path',{d:`M0 ${yy} H550`,stroke:'#344139'});shape('text',{x:556,y:yy+4,fill:'#a8b5aa','font-size':10}).textContent=price.toFixed(price<10?4:2)}
 rows.forEach((r,i)=>{const x=i*step+step/2,color=r.close>=r.open?'#b9ee79':'#fc9e91';const node=shape('path',{d:`M${x} ${y(r.high)} V${y(r.low)}`,stroke:color});const title=document.createElementNS(ns,'title');title.textContent=new Date(r.time*1000).toISOString()+` O ${r.open} H ${r.high} L ${r.low} C ${r.close}`;node.append(title);shape('rect',{x:x-Math.max(1,step*.3),y:Math.min(y(r.open),y(r.close)),width:Math.max(1,step*.6),height:Math.max(1,Math.abs(y(r.open)-y(r.close))),fill:color})});
 const keys=[...new Set(active.flatMap(b=>Object.keys(b.levels)))];
 for(const key of keys){if(!enabled(key))continue;let d='';rows.forEach((r,i)=>{const level=analysis.find(b=>b.time===r.time)?.levels[key];if(level)d+=d?` H${i*step+step/2} V${y(level.value)}`:`M${i*step+step/2} ${y(level.value)}`});shape('path',{d,fill:'none',stroke:key.endsWith('_major')?'#b9ee79':'#b5c9ff','stroke-dasharray':key.endsWith('_minor')?'4 5':'','stroke-width':key.startsWith('long_')?1.8:1})}
 if(current().settings.sma_enabled){const points=rows.map((r,i)=>{const v=smaRows.find(s=>s.time===r.time);return v?`${i*step+step/2},${y(v.value)}`:null}).filter(Boolean).join(' ');shape('polyline',{points,fill:'none',stroke:'#ebce82','stroke-width':2})}
 rows.forEach((r,i)=>{const bar=analysis.find(b=>b.time===r.time);if(bar?.signal)shape('text',{x:i*step+step/2,y:y(bar.signal==='Long'?r.low:r.high)+(bar.signal==='Long'?14:-6),fill:bar.signal==='Long'?'#b9ee79':'#fc9e91','font-size':12}).textContent=bar.signal==='Long'?'▲':'▼'});
 function line(value,color,label,dash=''){const yy=y(value);shape('path',{d:`M0 ${yy} H550`,stroke:color,'stroke-dasharray':dash});shape('text',{x:5,y:yy-4,fill:color,'font-size':10}).textContent=label}
 for(const p of positions){line(p.entry,'#e9eee6','Open '+p.side);line(p.entry+(p.side==='Long'?-1:1)*p.stop_dollars/p.qty,'#fc9e91','Dollar stop','3 4')}
 for(const trade of current().trades.filter(t=>t.symbol===$('symbol').value)){
  for(const [stamp,label,color]of [[trade.opened_at,'E','#e9eee6'],[trade.closed_at,'X','#fc9e91']]){const t=typeof stamp==='number'?(stamp>1e12?stamp/1000:stamp):Date.parse(stamp)/1000;const i=rows.findIndex((r,j)=>t>=r.time&&(j===rows.length-1?t<r.time+($('tf').value==='1'?60:86400):t<rows[j+1].time));if(i>=0)shape('text',{x:i*step,y:20,fill:color,'font-size':11}).textContent=label}
 }
 shape('text',{x:0,y:285,fill:'#a8b5aa','font-size':10}).textContent=new Date(start*1000).toISOString().replace('T',' ').slice(0,16)+' UTC';
 $('title').textContent=$('symbol').value+' · '+$('tf').selectedOptions[0].textContent;
}
$('zoom').oninput=()=>{visibleBars=Number($('zoom').value);draw()};
$('older').onclick=()=>{chartOffset=Math.min(Math.max(0,chartRows.length-visibleBars),chartOffset+20);draw()};
$('newer').onclick=()=>{chartOffset=Math.max(0,chartOffset-20);draw()};
$('refresh').onclick=()=>loadChart();
