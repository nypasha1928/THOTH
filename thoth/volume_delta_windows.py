"""Forming 1h Volume Delta entries in two five-minute windows; no exchange writes."""
from .volume_delta import signal
from .verification import strategy_fingerprint

STRATEGY='volume_delta_1h_windows_sma7_14_exit'

def window_start(now):
    start=int((now+120)//1800)*1800-120
    return start if 0<=now-start<300 else None

def evaluate(rows,lower,cfg,now):
    if cfg.timeframe!='60':raise ValueError('Volume Delta window entries require the 1h strategy timeframe')
    if window_start(now) is None:raise ValueError('Outside :58–:03 / :28–:33 entry windows')
    v=cfg.volume_delta
    return signal(rows,lower,'60',now,sma_filter=v.sma_filter,bull_type=v.bull_type,bear_type=v.bear_type,
        bullish_enabled=v.bullish,bearish_enabled=v.bearish)

def context(rows,cfg,now):
    return dict(volume_window=window_start(now),source_bar=int(now)//3600*3600,observed_at=now,
        strategy_fingerprint=strategy_fingerprint(cfg),primary_timeframe='60',source_history=rows[-50:])

def fresh(ctx,cfg,now):
    start=window_start(now)
    return (start is not None and ctx.get('volume_window')==start and ctx.get('source_bar')==int(now)//3600*3600
        and 0<=now-ctx.get('observed_at',float('-inf'))<=30 and ctx.get('strategy_fingerprint')==strategy_fingerprint(cfg))

def recheck(rows,lower,cfg,side,ctx,now):
    if not fresh(ctx,cfg,now):raise ValueError('Volume Delta entry window, hourly candle, settings or fresh decision expired')
    history=ctx.get('source_history') or []
    if len(rows)<2 or len(history)<2 or rows[-2]!=history[-2]:raise ValueError('Prior hourly candle changed or missing')
    checked=evaluate(history[:-1]+[rows[-1]],lower,cfg,now)
    if checked['signal']!=side:raise ValueError('Volume Delta window signal no longer qualifies in its original direction')
    return checked
