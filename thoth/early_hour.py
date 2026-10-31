"""Executable signal adapter; normal reversal semantics are owned by the supervisor."""
from .late_hour_comparison import SourceState,decision
from .indicator_stream import IndicatorState
from .order_block import OrderBlockState
from .verification import strategy_fingerprint

STRATEGY='order_block_sr_1h_early'

def rules(cfg):
    return dict(start_minute=55,max_opposing_wick_pct=cfg.early_max_opposing_wick_pct,
        minimum_distance_atr=cfg.early_min_distance_atr,maximum_distance_atr=cfg.early_max_distance_atr,ob_thickness_filter=False)

def window(now):return 3300<=now%3600<3600

def context(state,rows,cfg,now):
    bar=int(now)//3600*3600
    previous=next((r for r in reversed(rows) if r['time']==bar-3600),None)
    if previous is None:raise ValueError('Previous closed hour unavailable')
    return dict(early_bar=bar,primary_timeframe='60',strategy_fingerprint=strategy_fingerprint(cfg),
        early_state=dict(ob=state.ob.checkpoint(),sr=state.sr.checkpoint(),sr_report=state.sr_report,previous=previous))

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
    result=decision(rows,cfg,now,now,state,rules(cfg))
    if result['signal']!=side:raise ValueError('Early signal no longer qualifies: '+result['reason'])
    return result
