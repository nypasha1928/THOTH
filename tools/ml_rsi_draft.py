"""Offline default-settings prototype for the uploaded ML RSI script.

Not imported by the app and cannot place orders. Static RSI signals follow the
supplied default EMA smoothing/filter rules. Experimental ML explicitly corrects
array allocation and uses bounded causal history; TradingView parity is unproven.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class DraftConfig:
    rsi_length: int = 14
    smooth: bool = True
    smooth_period: int = 4
    volume_filter: bool = True
    volume_period: int = 20
    volume_multiplier: float = 1.
    volume_for_static: bool = True
    vwap_filter: bool = True
    sma_filter: bool = True
    sma_fast: int = 7
    sma_slow: int = 50
    histogram_filter: bool = False
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bull_hist_positive: bool = True
    bear_hist_negative: bool = True
    experimental_ml: bool = False
    max_data: int = 3000
    max_iterations: int = 1000

    def __post_init__(self):
        for name in ('rsi_length','smooth_period','volume_period','sma_fast','sma_slow','macd_fast','macd_slow','macd_signal','max_data','max_iterations'):
            value=getattr(self,name)
            if not isinstance(value,int) or isinstance(value,bool) or not 1<=value<=10000:raise ValueError('Invalid '+name)
        if self.rsi_length<2 or not isfinite(self.volume_multiplier) or self.volume_multiplier<=0:raise ValueError('Invalid RSI or volume setting')


class EMA:
    def __init__(self,length):self.alpha=2/(length+1);self.value=None
    def step(self,value):
        if value is None:return None
        self.value=value if self.value is None else self.alpha*value+(1-self.alpha)*self.value
        return self.value


def percentile(values,fraction):
    values=sorted(values);index=(len(values)-1)*fraction;low=int(index);high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(index-low)


def corrected_clusters(values,max_iterations):
    """Intended three-centroid algorithm, not a literal port of broken arrays."""
    if len(values)<4:return None
    centers=[percentile(values,p) for p in (.25,.5,.75)]
    for _ in range(max_iterations+1):
        groups=[[],[],[]]
        for value in values:
            index=min(range(3),key=lambda i:abs(value-centers[i]))
            groups[index].append(value)
        # Explicit policy: retain an empty group's centroid instead of introducing na.
        updated=[sum(group)/len(group) if group else centers[i] for i,group in enumerate(groups)]
        if updated==centers:break
        centers=updated
    return dict(short=centers[0],long=centers[2])


def cross_events(previous,current,previous_thresholds,thresholds):
    result=[]
    if previous is None or current is None:return result
    for name,threshold in thresholds.items():
        old=previous_thresholds.get(name)
        if old is None or threshold is None:continue
        if previous<=old and current>threshold and name in ('static30','ml_long','ml_short'):
            result.append((name+'_up','Long'))
        if previous>=old and current<threshold and name in ('static70','ml_long','ml_short'):
            result.append((name+'_down','Short'))
    return result


def evaluate_default(rows,cfg=DraftConfig(),interval=3600):
    """Preview close-source RSI/EMA, hlc3 daily UTC VWAP and all six signal families.

    Caller may supply a forming final candle for a tentative preview. Nothing is
    checkpointed or dispatched. Other Pine MA/source/anchor choices are not yet
    implemented. Flat all-zero RSI seeds remain unavailable pending parity review.
    """
    if interval<=0:raise ValueError('Invalid candle interval')
    prices=[];volumes=[];gains=[];losses=[];rsi_values=[];output=[]
    gain=loss=None;previous_rsi=None;previous_thresholds={};last_time=None
    smooth=EMA(cfg.smooth_period);fast=EMA(cfg.macd_fast);slow=EMA(cfg.macd_slow);signal=EMA(cfg.macd_signal)
    anchor=None;weighted=weight=0.
    for row in rows:
        stamp=row['time']
        if not isfinite(stamp) or int(stamp)!=stamp or stamp%interval!=0 or (last_time is not None and stamp-last_time!=interval):raise ValueError('Candle gap or invalid timestamp')
        values=[float(row[k]) for k in ('open','high','low','close','volume')]
        o,h,l,close,volume=values
        if any(not isfinite(v) for v in values) or min(o,h,l,close)<=0 or volume<0 or not l<=min(o,close)<=max(o,close)<=h:raise ValueError('Invalid OHLCV candle')
        if prices:
            change=close-prices[-1];up=max(change,0.);down=max(-change,0.)
            if gain is None:
                gains.append(up);losses.append(down)
                if len(gains)==cfg.rsi_length:gain=sum(gains)/cfg.rsi_length;loss=sum(losses)/cfg.rsi_length
            else:
                gain=(gain*(cfg.rsi_length-1)+up)/cfg.rsi_length
                loss=(loss*(cfg.rsi_length-1)+down)/cfg.rsi_length
        raw=None if gain is None or gain==loss==0 else 100. if loss==0 else 100-100/(1+gain/loss)
        rsi=smooth.step(raw) if cfg.smooth else raw
        prices.append(close);volumes.append(volume)
        day=int(stamp)//86400
        if anchor!=day:anchor=day;weighted=weight=0.
        weighted+=(h+l+close)/3*volume;weight+=volume
        vwap=weighted/weight if weight else None
        volume_ma=sum(volumes[-cfg.volume_period:])/cfg.volume_period if len(volumes)>=cfg.volume_period else None
        volume_ok=not cfg.volume_filter or (volume_ma is not None and volume>volume_ma*cfg.volume_multiplier)
        sma_fast=sum(prices[-cfg.sma_fast:])/cfg.sma_fast if len(prices)>=cfg.sma_fast else None
        sma_slow=sum(prices[-cfg.sma_slow:])/cfg.sma_slow if len(prices)>=cfg.sma_slow else None
        macd=fast.step(close)-slow.step(close);histogram=macd-signal.step(macd)
        ml=None
        if cfg.experimental_ml and rsi is not None:
            rsi_values.append(rsi);rsi_values=rsi_values[-(cfg.max_data+1):]
            ml=corrected_clusters(rsi_values,cfg.max_iterations)
        thresholds=dict(static30=30.,static70=70.,ml_long=ml['long'] if ml else None,ml_short=ml['short'] if ml else None)
        accepted=[]
        for name,side in cross_events(previous_rsi,rsi,previous_thresholds,thresholds):
            static=name.startswith('static');bull=side=='Long'
            if not volume_ok and (not static or cfg.volume_for_static):continue
            if cfg.vwap_filter and (vwap is None or not (close>vwap if bull else close<vwap)):continue
            if cfg.sma_filter and (sma_fast is None or sma_slow is None or not (sma_fast>sma_slow if bull else sma_fast<sma_slow)):continue
            positive=cfg.bull_hist_positive if bull else not cfg.bear_hist_negative
            if cfg.histogram_filter and not (histogram>0 if positive else histogram<0):continue
            accepted.append(dict(kind=name,side=side))
        sides={event['side'] for event in accepted}
        output.append(dict(time=stamp,rsi=rsi,thresholds=thresholds,vwap=vwap,volume_ma=volume_ma,sma_fast=sma_fast,sma_slow=sma_slow,histogram=histogram,events=accepted,signal=next(iter(sides)) if len(sides)==1 else None,conflict=len(sides)>1,experimental_ml=cfg.experimental_ml))
        previous_rsi=rsi;previous_thresholds=thresholds;last_time=stamp
    return output
