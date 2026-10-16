# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. https://mozilla.org/MPL/2.0/
# Derived from the TFlab source in reference/support_resistance.pine.
"""Bounded, restartable indicator state equivalent to causal batch replay."""
from math import nan,isfinite
from .indicator import confirmed_pivot,extract_levels,TYPES
from .indicator_core import ZigZag,PineArray


def validate(row):
    if any(not isfinite(row[k]) or row[k]<=0 for k in ('open','high','low','close')):raise ValueError('Invalid candle price')
    if not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:raise ValueError('Invalid candle range')


def encode(value):
    if isinstance(value,float) and not isfinite(value):return {'$nan':True}
    if isinstance(value,dict):return {k:encode(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value,dict):return nan if value=={'$nan':True} else {k:decode(v) for k,v in value.items()}
    if isinstance(value,list):return [decode(v) for v in value]
    return value


def prune(machine):
    # Source operations only mutate the last two points once initialized. Keep
    # >2 raw points so size-based initialization/classification remains intact.
    for array in machine.arrays[:4]:
        if len(array)>64:del array[:-64]
    types=machine.arrays[4]
    if len(types)>64:
        tail=set(range(len(types)-64,len(types)))
        found=set()
        for i in range(len(types)-1,-1,-1):
            key=TYPES.get(types[i])
            if key and key not in found:tail.add(i);found.add(key)
        indexes=sorted(tail)
        for array in machine.arrays[4:]:array[:]=[array[i] for i in indexes]


class IndicatorState:
    def __init__(self,cfg,checkpoint=None):
        self.cfg=cfg
        self.buffer=[];self.total=0.;self.count=0;self.previous={};self.last_time=None
        self.machines=[ZigZag(),ZigZag()]
        self.high_info=[(nan,nan,nan),(nan,nan,nan)]
        self.low_info=[(nan,nan,nan),(nan,nan,nan)]
        if checkpoint:
            state=decode(checkpoint)
            if state['version']!=1:raise ValueError('Unsupported indicator checkpoint')
            for key in ('buffer','total','count','previous','last_time','high_info','low_info'):setattr(self,key,state[key])
            for machine,saved in zip(self.machines,state['machines']):
                machine.__dict__.update(saved)
                machine.arrays=[PineArray(a) for a in machine.arrays]

    def checkpoint(self):
        return encode(dict(version=1,buffer=self.buffer,total=self.total,count=self.count,previous=self.previous,
            last_time=self.last_time,high_info=self.high_info,low_info=self.low_info,machines=[m.__dict__ for m in self.machines]))

    def step(self,row):
        validate(row)
        cfg=self.cfg;duration=60 if cfg.timeframe=='1' else 86400
        if self.last_time is not None and row['time']<=self.last_time:raise ValueError('Candle checkpoint must advance')
        self.buffer.append(row);i=len(self.buffer)-1
        self.total+=row['close']
        if self.count>=cfg.sma_length:self.total-=self.buffer[-cfg.sma_length-1]['close']
        average=self.total/cfg.sma_length if self.count>=cfg.sma_length-1 else None
        levels={}
        for j,period in enumerate((cfg.pivot_long,cfg.pivot_short)):
            hi,info=confirmed_pivot(self.buffer,i,period,'high',True)
            if info:self.high_info[j]=(info[0],self.count-period,info[2])
            lo,info=confirmed_pivot(self.buffer,i,period,'low',False)
            if info:self.low_info[j]=(info[0],self.count-period,info[2])
            points=self.machines[j].step(hi,lo,self.high_info[j],self.low_info[j],row['close'])
            for key,value in extract_levels(points).items():levels[('long_' if j==0 else 'short_')+key]=value
            prune(self.machines[j])
        bullish=bearish=False
        if self.count and self.last_time+duration==row['time']:
            prev_close=self.buffer[-2]['close']
            for key,level in levels.items():
                if key.endswith('_major') and not cfg.major:continue
                if key.endswith('_minor') and not cfg.minor:continue
                old=self.previous.get(key)
                if not old:continue
                if 'resistance' in key and prev_close<=old['value'] and row['close']>level['value']:bullish=True
                if 'support' in key and prev_close>=old['value'] and row['close']<level['value']:bearish=True
        if cfg.sma_enabled:
            bullish=bullish and average is not None and row['close']>average
            bearish=bearish and average is not None and row['close']<average
        signal='Long' if bullish and not bearish else 'Short' if bearish and not bullish else None
        self.previous=levels;self.last_time=row['time'];self.count+=1
        keep=max(cfg.sma_length+1,2*cfg.pivot_long+1,2*cfg.pivot_short+1)
        if len(self.buffer)>keep:del self.buffer[:-keep]
        return dict(time=row['time'],levels=levels,sma=average,signal=signal,conflict=bullish and bearish,close=row['close'])
