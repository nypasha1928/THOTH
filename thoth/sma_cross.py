"""Confirmed-candle SMA 7/200 crossover, independent of S/R filters."""
from collections import deque


class SMACross:
    def __init__(self,checkpoint=None):
        state=checkpoint or {}
        self.closes=deque(state.get('closes',[]),maxlen=200)
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
        slow=sum(values)/200 if len(values)==200 else None
        diff=fast-slow if slow is not None else None
        signal=None
        if diff is not None and self.previous is not None:
            if self.previous<=0 and diff>0:signal='Long'
            elif self.previous>=0 and diff<0:signal='Short'
        self.previous=diff;self.last_time=row['time']
        return dict(time=row['time'],levels={},sma=slow,sma7=fast,signal=signal,conflict=False,close=row['close'])
