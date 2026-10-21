"""Corrected, causal ML RSI. No trading or display permissions live here."""
from bisect import bisect_right
from copy import deepcopy
from datetime import datetime,timezone
from math import exp,sqrt,isfinite


def mean(values):return sum(values)/len(values) if values else None


def weighted(values):return sum((i+1)*v for i,v in enumerate(values))/(len(values)*(len(values)+1)/2)


class MovingAverage:
    def __init__(self,length,kind='Ema',sigma=6,state=None):
        self.length=length;self.kind=kind;self.sigma=sigma
        self.s=deepcopy(state) if state else dict(values=[],ema=[None]*6,hull=[])
    def step(self,value):
        if value is None:return None
        n=self.length;s=self.s;s['values'].append(value);s['values']=s['values'][-n:]
        a=2/(n+1);stage=value
        for i in range(6):
            s['ema'][i]=stage if s['ema'][i] is None else a*stage+(1-a)*s['ema'][i]
            stage=s['ema'][i]
        e=s['ema'];ready=len(s['values'])==n
        if self.kind=='Ema':return e[0]
        if self.kind=='Dema':return 2*e[0]-e[1]
        if self.kind=='TEMA':return 3*e[0]-3*e[1]+e[2]
        if self.kind=='T3':
            v=.7;c1=-v**3;c2=3*v*v+3*v**3;c3=-6*v*v-3*v-3*v**3;c4=1+3*v+v**3+3*v*v
            return c1*e[5]+c2*e[4]+c3*e[3]+c4*e[2]
        if self.kind=='RMA':
            if 'rma' not in s:
                if not ready:return None
                s['rma']=mean(s['values'])
            else:s['rma']=(s['rma']*(n-1)+value)/n
            return s['rma']
        if not ready:return None
        values=s['values']
        if self.kind=='SMA':return mean(values)
        if self.kind=='Wma':return weighted(values)
        if self.kind=='Hull':
            h=max(1,n//2);root=max(1,int(sqrt(n)+.5))
            s['hull'].append(2*weighted(values[-h:])-weighted(values));s['hull']=s['hull'][-root:]
            return mean(s['hull']) if root==1 else weighted(s['hull']) if len(s['hull'])==root else None
        if self.kind=='LINREG':
            if n==1:return value
            center=(n-1)/2;average=mean(values);slope=sum((i-center)*(v-average) for i,v in enumerate(values))/sum((i-center)**2 for i in range(n))
            return average+slope*center
        if self.kind=='ALMA':
            weights=[exp(-i*i/(2*(n/self.sigma)**2)) for i in range(n)]  # Pine offset=0.
            return sum(v*w for v,w in zip(values,weights))/sum(weights)
        raise ValueError('Unsupported RSI smoothing type')


def clusters(values,iterations):
    """Three ordered 1-D means, exact nearest-center ties assigned to the first.

    Sorted prefix sums avoid allocating three distances per sample per iteration.
    Empty groups retain their previous center. Centers are kept ordered so lower
    and upper thresholds retain their meaning even for degenerate distributions.
    """
    if len(values)<4:return None
    values=sorted(values);prefix=[0.]
    for value in values:prefix.append(prefix[-1]+value)
    def percentile(p):
        at=(len(values)-1)*p;i=int(at);j=min(i+1,len(values)-1)
        return values[i]+(values[j]-values[i])*(at-i)
    centers=[percentile(p) for p in (.25,.5,.75)]
    converged=False
    for _ in range(iterations+1):
        unique=[]
        for i,value in enumerate(centers):
            if not unique or unique[-1][1]!=value:unique.append((i,value))
        boundaries=[0]+[bisect_right(values,(a[1]+b[1])/2) for a,b in zip(unique,unique[1:])]+[len(values)]
        updated=list(centers)
        for j,(index,_) in enumerate(unique):
            left,right=boundaries[j:j+2]
            if right>left:updated[index]=(prefix[right]-prefix[left])/(right-left)
        updated.sort()
        if all(abs(a-b)<=1e-10 for a,b in zip(updated,centers)):
            centers=updated;converged=True;break
        centers=updated
    return dict(short=centers[0],long=centers[2],converged=converged)


def anchor_key(stamp,period):
    if period=='M':
        date=datetime.fromtimestamp(stamp,timezone.utc);return (date.year,date.month)
    if period=='W':return (int(stamp)+3*86400)//(7*86400)  # Monday UTC.
    return int(stamp)//(86400 if period=='D' else int(period)*60)


def source(row,name):
    if name=='hlc3':return (row['high']+row['low']+row['close'])/3
    if name=='ohlc4':return (row['open']+row['high']+row['low']+row['close'])/4
    return row[name]


def signal_events(previous,rsi,old_thresholds,thresholds):
    if previous is None or rsi is None:return []
    events=[]
    for name,level in thresholds.items():
        old=old_thresholds.get(name)
        if old is None or level is None:continue
        if name!='static70' and previous<=old and rsi>level:events.append(dict(kind=name+'_up',side='Long'))
        if name!='static30' and previous>=old and rsi<level:events.append(dict(kind=name+'_down',side='Short'))
    return events


class RSIState:
    def __init__(self,cfg,state=None):
        self.cfg=cfg
        self.s=deepcopy(state) if state else dict(count=0,last_time=None,last_source=None,gains=[],losses=[],gain=None,loss=None,prices=[],volumes=[],rsi_values=[],rsi=None,thresholds={},anchor=None,weighted=0.,weight=0.)
        self.smooth=MovingAverage(cfg.smooth_period,cfg.ma_type,cfg.alma_sigma,self.s.get('smooth_state'))
        self.fast=MovingAverage(cfg.macd_fast,'Ema',state=self.s.get('fast_state'))
        self.slow=MovingAverage(cfg.macd_slow,'Ema',state=self.s.get('slow_state'))
        self.macd_signal=MovingAverage(cfg.macd_signal,'Ema',state=self.s.get('macd_signal_state'))
    def checkpoint(self):
        return deepcopy({**self.s,'smooth_state':self.smooth.s,'fast_state':self.fast.s,'slow_state':self.slow.s,'macd_signal_state':self.macd_signal.s})
    def step(self,row,duration):
        cfg=self.cfg;s=self.s;stamp=row['time']
        names=('open','high','low','close','volume')
        try:values=[float(row[name]) for name in names]
        except (ValueError,TypeError,KeyError):raise ValueError('RSI requires valid OHLCV data') from None
        if any(not isfinite(value) for value in values):raise ValueError('RSI requires finite OHLCV data')
        o,h,l,close,volume=values
        if min(o,h,l,close)<=0 or volume<0 or not l<=min(o,close)<=max(o,close)<=h:raise ValueError('RSI requires valid OHLCV ranges')
        if not isfinite(stamp) or int(stamp)!=stamp or stamp%duration!=0:raise ValueError('RSI requires aligned candle timestamps')
        row={**row,**dict(zip(names,values))}
        if s['last_time'] is not None and stamp-s['last_time']!=duration:raise ValueError('RSI candle gap')
        value=source(row,cfg.source)
        if s['last_source'] is not None:
            change=value-s['last_source'];up=max(change,0);down=max(-change,0)
            if s['gain'] is None:
                s['gains'].append(up);s['losses'].append(down)
                if len(s['gains'])==cfg.rsi_length:s['gain']=mean(s['gains']);s['loss']=mean(s['losses'])
            else:
                s['gain']=(s['gain']*(cfg.rsi_length-1)+up)/cfg.rsi_length
                s['loss']=(s['loss']*(cfg.rsi_length-1)+down)/cfg.rsi_length
        gain,loss=s['gain'],s['loss']
        raw=None if gain is None or gain==loss==0 else 100. if loss==0 else 100-100/(1+gain/loss)
        rsi=self.smooth.step(raw) if cfg.smooth else raw
        s['prices'].append(close);s['prices']=s['prices'][-max(cfg.sma_fast,cfg.sma_slow):]
        s['volumes'].append(volume);s['volumes']=s['volumes'][-cfg.volume_period:]
        average=mean(s['volumes']) if len(s['volumes'])==cfg.volume_period else None
        fast=mean(s['prices'][-cfg.sma_fast:]) if len(s['prices'])>=cfg.sma_fast else None
        slow=mean(s['prices'][-cfg.sma_slow:]) if len(s['prices'])>=cfg.sma_slow else None
        anchor=list(anchor_key(stamp,cfg.vwap_anchor)) if cfg.vwap_anchor=='M' else anchor_key(stamp,cfg.vwap_anchor)
        if s['anchor']!=anchor:s.update(anchor=anchor,weight=0.,weighted=0.)
        s['weighted']+=source(row,cfg.vwap_source)*volume;s['weight']+=volume
        vwap=s['weighted']/s['weight'] if s['weight'] else None
        macd=self.fast.step(close)-self.slow.step(close);histogram=macd-self.macd_signal.step(macd)
        ml=None
        if rsi is not None:
            s['rsi_values'].append(rsi);s['rsi_values']=s['rsi_values'][-(cfg.max_data+1):]
            ml=clusters(s['rsi_values'],cfg.max_iterations)
        thresholds=dict(static30=30.,static70=70.,ml_short=ml['short'] if ml and ml['converged'] else None,ml_long=ml['long'] if ml and ml['converged'] else None)
        events=[]
        volume_ok=not cfg.volume_filter or (average is not None and volume>average*cfg.volume_multiplier)
        for event in signal_events(s['rsi'],rsi,s['thresholds'],thresholds):
            static=event['kind'].startswith('static');bull=event['side']=='Long'
            if static and not cfg.static_signals or not static and not cfg.ml_signals:continue
            if not volume_ok and (not static or cfg.volume_for_static):continue
            if cfg.vwap_filter and (vwap is None or not (close>vwap if bull else close<vwap)):continue
            if cfg.sma_filter and (fast is None or slow is None or not (fast>slow if bull else fast<slow)):continue
            positive=cfg.bull_hist_positive if bull else not cfg.bear_hist_negative
            if cfg.histogram_filter and not (histogram>0 if positive else histogram<0):continue
            events.append(event)
        sides={event['side'] for event in events}
        s.update(count=s['count']+1,last_time=stamp,last_source=value,rsi=rsi,thresholds=thresholds)
        return dict(time=stamp,close=close,levels={},sma=None,rsi=rsi,thresholds=thresholds,vwap=vwap,volume_ma=average,histogram=histogram,events=events,signal=next(iter(sides)) if len(sides)==1 else None,conflict=len(sides)>1,ml_ready=len(s['rsi_values'])>=cfg.max_data+1 and bool(ml and ml['converged']),rsi_samples=len(s['rsi_values']))
