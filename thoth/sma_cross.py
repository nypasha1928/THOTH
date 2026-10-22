"""Confirmed-candle SMA 7/200 or 7/50 crossover, independent of S/R filters."""
from collections import deque


class SMACross:
    def __init__(self,checkpoint=None,slow_period=200):
        state=checkpoint or {}
        if slow_period not in (50,200):raise ValueError('Unsupported SMA crossover period')
        self.slow_period=slow_period
        self.closes=deque(state.get('closes',[]),maxlen=slow_period)
        self.previous=state.get('previous')
        self.last_time=state.get('last_time')

    def checkpoint(self):
        return dict(closes=list(self.closes),previous=self.previous,last_time=self.last_time)

    def step(self,row,duration):
        contiguous=self.last_time is None or row['time']==self.last_time+duration
        if not contiguous:
            self.closes.clear();self.previous=None
        self.closes.append(row['close'])
        values=list(self.closes)
        fast=sum(values[-7:])/7 if len(values)>=7 else None
        slow=sum(values)/self.slow_period if len(values)==self.slow_period else None
        diff=fast-slow if slow is not None else None
        signal=None
        if diff is not None and self.previous is not None:
            if self.previous<=0 and diff>0:signal='Long'
            elif self.previous>=0 and diff<0:signal='Short'
        self.previous=diff;self.last_time=row['time']
        return dict(time=row['time'],levels={},sma=slow,sma7=fast,signal=signal,conflict=False,close=row['close'])


def combine_sr_sma(sr,cross):
    directions={s for s in (sr['signal'],cross['signal']) if s}
    if sr['conflict']:directions.update(('Long','Short'))
    return {**sr,'signal':next(iter(directions)) if len(directions)==1 else None,
        'conflict':len(directions)>1,'sr_signal':sr['signal'],'sma_signal':cross['signal'],
        'sma7':cross['sma7'],'sma50':cross['sma']}
