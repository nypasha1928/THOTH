"""Closed hourly OB entries with SMA200 permission and independent weaker exits."""
from decimal import Decimal
from math import isfinite
from .crypto_ob_strength import THRESHOLDS, STOP_DOLLARS, DISABLED_EXITS

STRATEGY = 'crypto_ob_strength_sma200_closeonly'
NAME = 'Crypto OB · 1h Close · SMA200 · 3.03% Lower Exit'
EXIT_THRESHOLDS = {s: float(Decimal(str(v))*32/33) for s,v in THRESHOLDS.items()}
POLICY = dict(strategy=STRATEGY, exit_ratio_numerator=32, exit_ratio_denominator=33,
              exit_reduction_pct=100/33, entry_thresholds=dict(THRESHOLDS),
              exit_thresholds=dict(EXIT_THRESHOLDS), sma_period=200,
              timeframe='60', opposite_action='close_only')

class StrengthState:
    def __init__(self, symbol, checkpoint=None, interval='60'):
        from .order_block import OrderBlockState
        self.symbol=symbol; self.interval=interval
        identity=dict(symbol=symbol,interval=interval,minimum=THRESHOLDS.get(symbol),
                      exit_minimum=EXIT_THRESHOLDS.get(symbol),policy=STRATEGY)
        if checkpoint and any(checkpoint.get(k)!=v for k,v in identity.items()):
            raise ValueError('SMA200 OB checkpoint identity mismatch')
        self.identity=identity
        self.entry=OrderBlockState((checkpoint or {}).get('entry'),interval=interval,
                                  formation_filter=False,minimum_strength=THRESHOLDS.get(symbol,0))
        self.exit=OrderBlockState((checkpoint or {}).get('exit'),interval=interval,
                                 formation_filter=False,minimum_strength=EXIT_THRESHOLDS.get(symbol,0))
        self.count=self.entry.s['count'];self.last_time=self.entry.s['last_time']
    def checkpoint(self):
        return {**self.identity,'entry':self.entry.checkpoint(),'exit':self.exit.checkpoint()}
    def step(self,row):
        entry=self.entry.step(row);exit=self.exit.step(row)
        self.count=self.entry.s['count'];self.last_time=row['time']
        def formation(result,minimum):
            events=[e for e in result['events'] if e in ('bullish_formed','bearish_formed')]
            if len(events)!=1 or not result['ready'] or minimum is None or self.interval!='60':return None
            side='Long' if events[0]=='bullish_formed' else 'Short'
            zone=result['zones']['demand' if side=='Long' else 'supply'][0]
            return dict(side=side,minimum_strength=minimum,block_high=zone['high'],block_low=zone['low'],
                        block_width=abs(zone['high']-zone['low']))
        formed=formation(entry,THRESHOLDS.get(self.symbol));opposite=formation(exit,EXIT_THRESHOLDS.get(self.symbol))
        sma=entry.get('sma200')
        details=dict(strategy=STRATEGY,symbol=self.symbol,timeframe=self.interval,signal_bar=row['time'],
                     closed_at=row['time']+3600,entry_minimum=THRESHOLDS.get(self.symbol),
                     exit_minimum=EXIT_THRESHOLDS.get(self.symbol),exit_ratio='32/33',
                     close=row['close'],sma200=sma,entry_formation=formed,exit_formation=opposite,
                     basis='raw quote-price width; completed hourly close versus SMA200')
        signal=opposite['side'] if opposite else None
        return dict(time=row['time'],close=row['close'],levels={},sma=sma,signal=signal,
                    conflict=False,entry_sources=['ob:'+('bullish_formed' if signal=='Long' else 'bearish_formed')] if signal else [],
                    crypto_ob_strength=details,signal_diagnostic={**details,'reason':'CLOSED 1h OB; entry and relaxed exit checked separately'} if signal else None)

def _validate(symbol,side,context,now,kind):
    d=(context or {}).get('crypto_ob_strength') or {}
    expected=int(now)//3600*3600-3600
    if (symbol not in THRESHOLDS or side not in ('Long','Short') or d.get('strategy')!=STRATEGY
            or d.get('symbol')!=symbol or d.get('timeframe')!='60' or d.get('signal_bar')!=expected
            or d.get('closed_at')!=expected+3600 or d.get('entry_minimum')!=THRESHOLDS[symbol]
            or d.get('exit_minimum')!=EXIT_THRESHOLDS[symbol] or d.get('exit_ratio')!='32/33'):
        raise ValueError('Latest CLOSED 1h SMA200 OB context required')
    f=d.get(kind+'_formation') or {};minimum=(THRESHOLDS if kind=='entry' else EXIT_THRESHOLDS)[symbol]
    try:
        high=float(f['block_high']);low=float(f['block_low'])
        valid=f.get('side')==side and f.get('minimum_strength')==minimum and isfinite(high) and isfinite(low) and high>low>0 and high-low>=minimum
    except (KeyError,ValueError,TypeError):valid=False
    if not valid:raise ValueError('Order Block below '+kind+' strength or no matching formation')
    return d

def validate_exit(symbol,side,context,now):
    return _validate(symbol,side,context,now,'exit')

def validate_entry(symbol,side,context,now):
    d=_validate(symbol,side,context,now,'entry')
    try:
        price=float(d['close']);sma=float(d['sma200'])
        valid=isfinite(price) and isfinite(sma) and price>0 and sma>0 and (price>sma if side=='Long' else price<sma)
    except (KeyError,ValueError,TypeError):valid=False
    if not valid:raise ValueError('SMA200 entry permission missing, equal or opposite')
    return d
