"""Calendar-period equity peaks and durable pause latches; no exchange actions."""
from .portfolio_trailing import amount
PERIODS={'daily':86400,'three_day':259200,'weekly':604800}
LABELS={'daily':'Daily equity trailing stop','three_day':'3-Day equity trailing stop','weekly':'Weekly equity trailing stop'}


def window(period,now):
    duration=PERIODS[period]
    # Epoch is Thursday; shift three days so weekly windows begin Monday UTC.
    offset=-259200 if period=='weekly' else 0
    start=int((now-offset)//duration)*duration+offset
    return start,start+duration


def advance(previous,equity,net_transfers,enabled,drop,period,now):
    start,end=window(period,now);state=dict(previous or {})
    if state and now<state['last_sample']:raise ValueError('Equity trailing clock moved backwards')
    # A triggered pause survives disabling/settings edits, until its window ends.
    if state.get('triggered') and now<state['period_end']:return state
    if not enabled:return {}
    current=amount(equity);net=amount(net_transfers);distance=amount(drop)
    if current<0 or distance<=0:raise ValueError('Invalid equity trailing sample or drop')
    if state.get('period_start')!=start:
        state=dict(period_start=start,period_end=end,baseline_ms=int(now*1000),baseline=str(current),
            baseline_transfers=str(net),peak=str(current),triggered=False,triggered_at=None)
    adjusted=current-(net-amount(state['baseline_transfers']))
    peak=max(adjusted,amount(state['peak']));threshold=peak-distance
    used=peak-adjusted
    state.update(current=str(current),adjusted=str(adjusted),peak=str(peak),drop=str(distance),
        stop_equity=str(threshold+net-amount(state['baseline_transfers'])),remaining=str(max(amount(0),distance-used)),
        last_sample=now)
    if used>=distance:state.update(triggered=True,triggered_at=now)
    return state


def active_locks(states,now):
    return {key:state for key,state in states.items() if state.get('triggered') and now<state['period_end']}
