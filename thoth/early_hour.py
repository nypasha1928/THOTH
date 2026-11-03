"""Executable signal adapter; normal reversal semantics are owned by the supervisor."""
from .late_hour_comparison import SourceState,decision
from .indicator_stream import IndicatorState
from .order_block import OrderBlockState
from .verification import strategy_fingerprint

STRATEGY='order_block_sr_1h_early'

def rules(cfg):
    return dict(start_minute=55,max_opposing_wick_pct=cfg.early_max_opposing_wick_pct,
        minimum_distance_atr=cfg.early_min_distance_atr,maximum_distance_atr=cfg.early_max_distance_atr,ob_thickness_filter=False,direction_filter='1h_closed_sma200')

def window(now):return 3300<=now%3600<3600

def context(state,rows,cfg,now):
    bar=int(now)//3600*3600
    previous=next((r for r in reversed(rows) if r['time']==bar-3600),None)
    if previous is None:raise ValueError('Previous closed hour unavailable')
    return dict(early_bar=bar,early_price=rows[-1]['close'],primary_timeframe='60',strategy_fingerprint=strategy_fingerprint(cfg),
        sma200_baseline=sma200_baseline(rows,now),early_state=dict(ob=state.ob.checkpoint(),sr=state.sr.checkpoint(),sr_report=state.sr_report,previous=previous))

def recheck(rows,cfg,side,ctx,now):
    if not window(now) or ctx.get('early_bar')!=int(now)//3600*3600:raise ValueError('Early :55–:00 entry window closed')
    if ctx.get('strategy_fingerprint')!=strategy_fingerprint(cfg):raise ValueError('Early signal settings changed')
    saved=ctx.get('early_state')
    if not saved:raise ValueError('Early source checkpoint unavailable')
    state=SourceState(cfg)
    state.ob=OrderBlockState(saved['ob'],interval='60',formation_filter=False)
    state.sr=IndicatorState(state.cfg,saved['sr']);state.sr_report=saved['sr_report']
    previous=saved['previous'];state.recent={previous['time']:previous}
    if not any(row==previous for row in rows):raise ValueError('Previous closed hour changed; fresh early decision required')
    result=filter_sample(decision(rows,cfg,now,now,state,rules(cfg)),rows,now,ctx.get('sma200_baseline',{}))
    if result['signal']!=side:raise ValueError('Early signal no longer qualifies: '+result['reason'])
    return result


def sma200_baseline(rows,now):
    """Only the 200 consecutive, completed hourly closes; never the forming bar."""
    from decimal import Decimal,InvalidOperation
    bar=int(now)//3600*3600
    result=dict(period=200,timeframe='60',bar=bar,count=0,value=None,basis='last 200 completed hourly closes')
    closed=[r for r in rows if r.get('time',bar)>=bar-200*3600 and r.get('time',bar)<bar]
    result['count']=len(closed)
    if len(closed)!=200 or [r.get('time') for r in closed]!=list(range(bar-200*3600,bar,3600)):
        result['reason']='1h SMA200 unavailable: require 200 consecutive completed hourly candles';return result
    try:
        prices=[Decimal(str(r['close'])) for r in closed]
        if any(not p.is_finite() or p<=0 for p in prices):raise ValueError()
        result['value']=str(sum(prices)/Decimal(200))
    except (InvalidOperation,ValueError,KeyError,TypeError):
        result['reason']='1h SMA200 unavailable: invalid completed close';return result
    result['reason']='1h SMA200 ready';return result


def direction_check(price,side,baseline,now):
    """Strict comparisons: equality, missing history/price and stale baselines fail closed."""
    from decimal import Decimal,InvalidOperation
    result=dict(baseline or {});result.update(side=side,price=str(price),passed=False)
    if result.get('bar')!=int(now)//3600*3600 or result.get('count')!=200 or result.get('period')!=200 or result.get('value') is None:
        result['reason']='1h SMA200 entry blocked: 200 completed hourly candles unavailable or stale';return result
    try:
        current=Decimal(str(price));average=Decimal(str(result['value']))
        if not current.is_finite() or current<=0 or not average.is_finite() or average<=0:raise ValueError()
    except (InvalidOperation,ValueError,TypeError):
        result['reason']='1h SMA200 entry blocked: valid price and average required';return result
    if current==average:result['reason']='1h SMA200 entry blocked: price exactly equals SMA200';return result
    direction='Long' if current>average else 'Short'
    result.update(relation='above' if current>average else 'below',allowed_side=direction,passed=side==direction)
    result['reason']='1h SMA200: price '+result['relation']+' average; '+direction+' entries only'
    return result


def filter_sample(sample,rows,now,baseline=None):
    if not sample.get('signal'):return sample
    checked=direction_check(sample['price'],sample['signal'],baseline if baseline is not None else sma200_baseline(rows,now),now)
    sample['sma200_direction']=checked
    if sample.get('metrics') is not None:sample['metrics']['sma200_direction']=checked
    if not checked['passed']:sample.update(signal=None,passed_sources=[],reason='Skip: '+checked['reason'])
    return sample
