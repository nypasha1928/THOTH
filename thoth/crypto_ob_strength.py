"""Fixed research-candidate crypto universe; closed formations only, raw quote widths."""
STRATEGY='crypto_ob_strength_1h_close'
LIST='crypto_ob_candidates'
STOP_DOLLARS=.75
NAME='Crypto Order Block · 1h Close · Per-coin Strength · $0.75 Stop'
THRESHOLDS={'LDOUSDT':.005,'UNIUSDT':.1,'WIFUSDT':.0019,'LINKUSDT':.107,
    'JASMYUSDT':.000052,'GRTUSDT':.00033,'JUPUSDT':.0045,'GALAUSDT':.000021,
    'ETHUSDT':7.45,'SEIUSDT':.00081,
    'BNBUSDT':2.58,'AVAXUSDT':.07,'OPUSDT':.00235,'POLUSDT':.00087,'ENAUSDT':.004}
DISABLED_EXITS=('sma_exit_enabled','sma_ob_exit_enabled','trailing_enabled',
    'coin_breakeven_enabled','take_profit_enabled')

class StrengthState:
    def __init__(self,symbol,checkpoint=None,interval='60'):
        from .order_block import OrderBlockState
        from .timeframes import interval_seconds
        self.symbol=symbol;self.interval=interval;self.duration=interval_seconds(interval)
        self.minimum=THRESHOLDS.get(symbol)
        if checkpoint and (checkpoint.get('symbol')!=symbol or checkpoint.get('interval')!=interval
                or checkpoint.get('minimum')!=self.minimum):raise ValueError('Crypto strength checkpoint identity mismatch')
        self.machine=OrderBlockState((checkpoint or {}).get('ob'),interval=interval,
            formation_filter=False,minimum_strength=self.minimum or 0)
        self.count=self.machine.s['count'];self.last_time=self.machine.s['last_time']
    def checkpoint(self):
        return dict(symbol=self.symbol,interval=self.interval,minimum=self.minimum,ob=self.machine.checkpoint())
    def step(self,row):
        result=self.machine.step(row);self.count=self.machine.s['count'];self.last_time=row['time']
        events=[e for e in result['events'] if e in ('bullish_formed','bearish_formed')]
        sides={'Long' if e=='bullish_formed' else 'Short' for e in events}
        signal=next(iter(sides)) if len(sides)==1 and result['ready'] and self.minimum is not None and self.interval=='60' else None
        diagnostic=None
        if signal:
            zone=result['zones']['demand' if signal=='Long' else 'supply'][0]
            diagnostic=dict(reason='closed Order Block formation passed per-coin raw strength',
                symbol=self.symbol,timeframe=self.interval,signal_bar=row['time'],closed_at=row['time']+self.duration,
                side=signal,minimum_strength=self.minimum,block_high=zone['high'],block_low=zone['low'],
                block_width=abs(zone['high']-zone['low']),basis='raw quote-price width; not ATR or volume')
        return dict(time=row['time'],close=row['close'],levels={},sma=None,signal=signal,
            conflict=len(sides)>1,entry_sources=['ob:'+e for e in events] if signal else [],
            signal_diagnostic=diagnostic,crypto_ob_strength=diagnostic)

def validate_entry(symbol,side,context,now):
    """Reject injected/expired/incorrect-symbol contexts before any exchange order."""
    from math import isfinite
    details=(context or {}).get('crypto_ob_strength') or {}
    expected=int(now)//3600*3600-3600
    if symbol not in THRESHOLDS:raise ValueError('Symbol is outside Crypto OB Strength candidates')
    if (details.get('symbol')!=symbol or details.get('timeframe')!='60'
            or details.get('signal_bar')!=expected or details.get('closed_at')!=expected+3600
            or details.get('side')!=side or details.get('minimum_strength')!=THRESHOLDS[symbol]):
        raise ValueError('Latest CLOSED 1h per-coin strength context required')
    try:width=abs(float(details['block_high'])-float(details['block_low']))
    except (KeyError,TypeError,ValueError):raise ValueError('Recorded Order Block boundaries required') from None
    if not isfinite(width) or width<THRESHOLDS[symbol]:raise ValueError('Order Block below per-coin strength')
    return details
