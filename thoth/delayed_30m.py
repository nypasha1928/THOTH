"""Fixed quarter-hour scheduling for saved CLOSED 30m signals; no exchange writes."""
from .verification import strategy_fingerprint

STRATEGY='ob_sr_30m_delayed'


def candidate(observation,cfg,now):
    bar=observation.get('closed_bar');side=observation.get('signal')
    if side not in ('Long','Short') or not isinstance(bar,(int,float)) or bar%1800:return None
    closed_at=bar+1800;due_at=closed_at+900
    if bar!=int(now)//1800*1800-1800 or not closed_at<=now<due_at:return None
    return dict(closed_bar=bar,closed_at=closed_at,due_at=due_at,expires_at=due_at+60,side=side,
        sources=list(observation.get('entry_sources',[])),captured_at=now,strategy_fingerprint=strategy_fingerprint(cfg))


def ready(item,cfg,now):
    return (item.get('strategy_fingerprint')==strategy_fingerprint(cfg) and item.get('side') in ('Long','Short')
        and isinstance(item.get('due_at'),(int,float)) and isinstance(item.get('expires_at'),(int,float))
        and item['due_at']<=now<item['expires_at'])
