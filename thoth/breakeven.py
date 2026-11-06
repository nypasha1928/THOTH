"""Fixed per-position P&L floor; never follows favorable movement."""
from .portfolio_trailing import amount


def advance(state,pnl,enabled,activation,action,now):
    result=dict(state or {})
    if result.get('triggered'):return result
    if not enabled:return dict(enabled=False,active=False,triggered=False)
    current=amount(pnl);activation=amount(activation);action=amount(action)
    if not 0<=action<activation:raise ValueError('Breakeven action must be nonnegative and below trigger')
    result.update(enabled=True,current=str(current),activation=str(activation),action=str(action),last_sample=now)
    if not result.get('active') and current>=activation:result.update(active=True,activated_at=now)
    if result.get('active') and current<=action:result.update(triggered=True,triggered_at=now)
    return result
