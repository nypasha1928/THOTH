"""Durable trailing state for combined unrealized P&L, independent of prices."""
from decimal import Decimal, InvalidOperation


def amount(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Invalid open P&L sample') from None
    if not result.is_finite():raise ValueError('Invalid open P&L sample')
    return result


def new_state():
    return dict(active=False,peak=None,current=None,threshold=None,triggered=False,triggered_at=None,last_sample=None)


def advance(state,pnl,has_exposure,enabled,activation,drop,now):
    """Latch on >= activation, then <= peak minus fixed dollar distance.

    Call only with fresh verified positions. Flat resets an untriggered cycle;
    a triggered cycle remains latched until explicit flat-account reactivation.
    Decimal comparisons preserve inclusive boundaries such as 1.5 - .1 = 1.4.
    """
    result={**new_state(),**state}
    if result['triggered']:return result
    if not enabled or not has_exposure:return new_state()
    current=amount(pnl);activation=amount(activation);drop=amount(drop)
    if activation<=0 or drop<=0:raise ValueError('Activation and trailing drop must be positive')
    result.update(current=str(current),last_sample=now)
    if not result['active'] and current>=activation:result.update(active=True,peak=str(current))
    if result['active']:
        peak=max(amount(result['peak']),current);threshold=peak-drop
        result.update(peak=str(peak),threshold=str(threshold))
        if current<=threshold:result.update(triggered=True,triggered_at=now)
    return result
