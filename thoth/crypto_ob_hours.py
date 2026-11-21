"""Separate research50 closed-hour profile; fixed NY exclusions gate entries only."""
from decimal import Decimal
from math import isfinite
from .crypto_ob_strength import STOP_DOLLARS, DISABLED_EXITS
from datetime import datetime
from zoneinfo import ZoneInfo

LIST = 'crypto_ob_hours_candidates'
BLOCKED_HOURS = (5, 8, 10, 12, 21, 23)
TIMEZONE = 'America/New_York'
THRESHOLDS = {'1000BONKUSDT': 2.6e-05,
 '1000PEPEUSDT': 0.0001,
 'AAVEUSDT': 2.51,
 'ADAUSDT': 0.0017,
 'ARBUSDT': 0.00271,
 'ARUSDT': 0.057,
 'ATOMUSDT': 0.03,
 'AVAXUSDT': 0.07,
 'BNBUSDT': 2.58,
 'BTCUSDT': 254.4,
 'CAKEUSDT': 0.031,
 'COMPUSDT': 0.34,
 'DOGEUSDT': 0.00109,
 'DOTUSDT': 0.0181,
 'ENAUSDT': 0.004,
 'ETHUSDT': 7.45,
 'FILUSDT': 0.0182,
 'GALAUSDT': 2.1e-05,
 'GRTUSDT': 0.00033,
 'HBARUSDT': 0.00043,
 'ICPUSDT': 0.053,
 'INJUSDT': 0.0,
 'JASMYUSDT': 5.2e-05,
 'JUPUSDT': 0.0045,
 'KASUSDT': 0.00067,
 'LDOUSDT': 0.005,
 'LINKUSDT': 0.107,
 'LTCUSDT': 0.42,
 'MNTUSDT': 0.0038,
 'NEARUSDT': 0.062,
 'OPUSDT': 0.00235,
 'PAXGUSDT': 12.69,
 'POLUSDT': 0.00087,
 'RENDERUSDT': 0.015,
 'SANDUSDT': 0.00054,
 'SEIUSDT': 0.00081,
 'SHIB1000USDT': 3.2e-05,
 'SOLUSDT': 1.34,
 'SUIUSDT': 0.0193,
 'TAOUSDT': 5.5,
 'TRXUSDT': 0.0009,
 'UNIUSDT': 0.1,
 'VETUSDT': 4.4e-05,
 'WIFUSDT': 0.0019,
 'WLDUSDT': 0.0,
 'XAUTUSDT': 8.91,
 'XLMUSDT': 0.0021,
 'XMRUSDT': 6.65,
 'XRPUSDT': 0.0158,
 'ZECUSDT': 26.27}

# Keep the complete historical map for legacy positions' exit validation.
ENTRY_THRESHOLDS = {s:v for s,v in THRESHOLDS.items() if v>0}
ZERO_STRENGTH_SYMBOLS = tuple(s for s,v in THRESHOLDS.items() if v<=0)

STRATEGY = 'crypto_ob_strength_sma200_hours'
NAME = 'Crypto OB · 1h Close · SMA200 · 6-Hour Entry Block'
EXIT_THRESHOLDS = {s: float(Decimal(str(v))*32/33) for s,v in THRESHOLDS.items()}
POLICY = dict(strategy=STRATEGY, exit_ratio_numerator=32, exit_ratio_denominator=33,
              exit_reduction_pct=100/33, entry_thresholds=dict(ENTRY_THRESHOLDS),
              exit_thresholds=dict(EXIT_THRESHOLDS), sma_period=200,
              timeframe='60', opposite_action='close_only', timezone=TIMEZONE,
              blocked_entry_hours=list(BLOCKED_HOURS), entry_only=True,
              excluded_zero_strength_symbols=list(ZERO_STRENGTH_SYMBOLS))

class StrengthState:
    policy = STRATEGY
    def __init__(self, symbol, checkpoint=None, interval='60'):
        from .order_block import OrderBlockState
        self.symbol=symbol; self.interval=interval
        identity=dict(symbol=symbol,interval=interval,minimum=THRESHOLDS.get(symbol),
                      exit_minimum=EXIT_THRESHOLDS.get(symbol),policy=self.policy)
        if checkpoint and any(checkpoint.get(k)!=v for k,v in identity.items()):
            raise ValueError('Crypto OB checkpoint identity mismatch')
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
        details=dict(strategy=self.policy,symbol=self.symbol,timeframe=self.interval,signal_bar=row['time'],
                     closed_at=row['time']+3600,entry_minimum=THRESHOLDS.get(self.symbol),
                     exit_minimum=EXIT_THRESHOLDS.get(self.symbol),exit_ratio='32/33',
                     close=row['close'],sma200=sma,entry_formation=formed,exit_formation=opposite,
                     basis='raw quote-price width; completed hourly close versus SMA200',
                     entry_hour_policy=dict(timezone=TIMEZONE,blocked_hours=list(BLOCKED_HOURS)))
        signal=opposite['side'] if opposite else None
        return dict(time=row['time'],close=row['close'],levels={},sma=sma,signal=signal,
                    conflict=False,entry_sources=['ob:'+('bullish_formed' if signal=='Long' else 'bearish_formed')] if signal else [],
                    crypto_ob_strength=details,signal_diagnostic={**details,'reason':'CLOSED 1h OB; entry and relaxed exit checked separately'} if signal else None)

def _validate(symbol,side,context,now,kind,*,policy=STRATEGY):
    d=(context or {}).get('crypto_ob_strength') or {}
    expected=int(now)//3600*3600-3600
    if (symbol not in THRESHOLDS or side not in ('Long','Short') or d.get('strategy')!=policy
            or d.get('symbol')!=symbol or d.get('timeframe')!='60' or d.get('signal_bar')!=expected
            or d.get('closed_at')!=expected+3600 or d.get('entry_minimum')!=THRESHOLDS[symbol]
            or d.get('exit_minimum')!=EXIT_THRESHOLDS[symbol] or d.get('exit_ratio')!='32/33'):
        raise ValueError('Latest CLOSED 1h strength OB context required')
    f=d.get(kind+'_formation') or {};minimum=(THRESHOLDS if kind=='entry' else EXIT_THRESHOLDS)[symbol]
    try:
        high=float(f['block_high']);low=float(f['block_low'])
        valid=f.get('side')==side and f.get('minimum_strength')==minimum and isfinite(high) and isfinite(low) and high>=low>0 and high-low>=minimum
    except (KeyError,ValueError,TypeError):valid=False
    if not valid:raise ValueError('Order Block below '+kind+' strength or no matching formation')
    return d

def validate_exit(symbol,side,context,now):
    return _validate(symbol,side,context,now,'exit')

def validate_entry(symbol,side,context,now):
    if symbol in ZERO_STRENGTH_SYMBOLS:
        raise ValueError('Zero OB minimum strength: '+symbol+' new entries disabled; existing exits remain active')
    d=_validate(symbol,side,context,now,'entry')
    reason=entry_block_reason(now)
    if reason:raise ValueError(reason)
    try:
        price=float(d['close']);sma=float(d['sma200'])
        valid=isfinite(price) and isfinite(sma) and price>0 and sma>0 and (price>sma if side=='Long' else price<sma)
    except (KeyError,ValueError,TypeError):valid=False
    if not valid:raise ValueError('SMA200 entry permission missing, equal or opposite')
    return d


def entry_block_reason(now):
    hour=datetime.fromtimestamp(now,ZoneInfo(TIMEZONE)).hour
    if hour in BLOCKED_HOURS:
        return f'Strategy entry-hour exclusion: {hour:02d}:00–{hour+1:02d}:00 New York; exits remain active'
    return None
