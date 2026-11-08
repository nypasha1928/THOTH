"""Closed price/SMA50 side changes; equality and same-side bars never refire."""
from collections import deque
from math import isfinite,fsum

STRATEGY='price_sma50_30m_close'

class PriceSMACross:
    def __init__(self,checkpoint=None):
        s=checkpoint or {};self.closes=deque(s.get('closes',[]),maxlen=50)
        self.side=s.get('side');self.last_time=s.get('last_time');self.previous=s.get('previous')
    def checkpoint(self):return dict(closes=list(self.closes),side=self.side,last_time=self.last_time,previous=self.previous)
    def step(self,row,duration):
        close=float(row['close'])
        if not isfinite(close) or close<=0:raise ValueError('Price/SMA50 requires a positive finite close')
        if self.last_time is not None and row['time']!=self.last_time+duration:
            self.closes.clear();self.side=None;self.previous=None
        self.closes.append(close);average=fsum(self.closes)/50 if len(self.closes)==50 else None
        side='Long' if average is not None and close>average else 'Short' if average is not None and close<average else None
        comparable=self.side is not None or self.previous is not None and self.previous.get('sma50') is not None
        signal=side if side and comparable and side!=self.side else None
        diagnostic=None
        if signal:
            diagnostic=dict(reason='confirmed price crossed SMA50',timeframe=str(duration//60),signal_bar=row['time'],closed_at=row['time']+duration,
                close=close,sma50=average,previous=self.previous,previous_nonzero_side=self.side,side=side,
                candle={k:row[k] for k in ('time','open','high','low','close') if k in row})
        if side:self.side=side
        self.previous=dict(time=row['time'],close=close,sma50=average)
        self.last_time=row['time']
        return dict(time=row['time'],close=close,levels={},sma=average,signal=signal,conflict=False,
            entry_sources=['price_sma:50'] if signal else [],signal_diagnostic=diagnostic)
