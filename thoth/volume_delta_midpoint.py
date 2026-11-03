"""One sampled hourly Volume Delta decision at :30; no exchange writes."""
from .volume_delta import signal
from .verification import strategy_fingerprint

STRATEGY='volume_delta_1h_midpoint'
def window(now):return 1800<=now%3600<1860

def evaluate(rows,lower,cfg,now):
    if not window(now):raise ValueError('Volume Delta :30–:31 decision window closed')
    v=cfg.volume_delta
    return signal(rows,lower,'60',now,sma_filter=v.sma_filter,bull_type=v.bull_type,bear_type=v.bear_type,
        bullish_enabled=v.bullish,bearish_enabled=v.bearish)

def context(rows,cfg,now):
    return dict(midpoint_bar=int(now)//3600*3600,midpoint_observed_at=now,
        strategy_fingerprint=strategy_fingerprint(cfg),primary_timeframe='60',midpoint_history=rows[-50:])

def recheck(rows,lower,cfg,side,ctx,now):
    if not window(now) or ctx.get('midpoint_bar')!=int(now)//3600*3600:raise ValueError('Volume Delta :30–:31 decision window closed')
    observed=ctx.get('midpoint_observed_at')
    if observed is None or not window(observed) or not 0<=now-observed<=30:raise ValueError('Volume Delta :30 decision expired')
    if ctx.get('strategy_fingerprint')!=strategy_fingerprint(cfg):raise ValueError('Volume Delta decision settings changed')
    history=ctx.get('midpoint_history') or []
    if len(history)<2 or len(rows)<2 or history[-2]!=rows[-2]:raise ValueError('Prior hourly candle changed or missing; decision not replayed')
    checked=evaluate(history[:-1]+[rows[-1]],lower,cfg,now)
    if checked['signal']!=side:raise ValueError('Volume Delta :30 signal no longer qualifies in its original direction')
    return checked
