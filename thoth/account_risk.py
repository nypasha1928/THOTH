"""Account risk state for exchange execution, independent from simulated funds."""
from datetime import datetime, timezone, timedelta
from math import isfinite


def day(stamp):
    return datetime.fromtimestamp(stamp,timezone.utc).date().isoformat()


def new_risk():
    return dict(day=None,baseline=None,baseline_ms=None,last_sample=None,loss_pct=0,
                streak=0,pause_until=None,daily_stop=False,baseline_required=False,
                seen_closures=[],portfolio_stop=False)


def sample(risk,equity,net_transfers,cfg,now,initial=False):
    if not all(isfinite(v) for v in (equity,net_transfers)) or equity<0:
        raise ValueError('Invalid account equity sample')
    today=day(now)
    if risk['day']!=today:
        midnight=datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp()
        near_midnight=(now-midnight<=30 and risk['last_sample'] is not None
                       and 0<=now-risk['last_sample']<=30)
        if risk['day'] is None or initial or near_midnight:
            if equity<=0:raise ValueError('A positive account equity baseline is required')
            risk.update(day=today,baseline=equity,baseline_ms=int(now*1000),
                        daily_stop=False,baseline_required=False,loss_pct=0)
            net_transfers=0
        else:
            risk.update(baseline_required=True)
            # Keep the previous baseline and stop latch until an operator can
            # establish a new day's baseline while completely flat.
            risk['last_sample']=now
            return 'baseline_required'
    adjusted=equity-net_transfers
    risk['loss_pct']=max(0,(risk['baseline']-adjusted)/risk['baseline']*100)
    risk['last_sample']=now
    if risk['loss_pct']>=cfg.daily_loss_pct:
        risk['daily_stop']=True
    if risk['daily_stop']:return 'daily_stop'
    if risk.get('pause_until') and now<risk['pause_until']:return 'cooldown'
    if risk.get('pause_until'):
        risk.update(pause_until=None,streak=0)
    return 'ok'


def closure(risk,identity,pnl,cfg,now):
    if identity in risk['seen_closures']:return False
    if not isfinite(pnl):raise ValueError('Invalid realized result')
    risk['seen_closures'].append(identity)
    if pnl<0:risk['streak']+=1
    elif pnl>0:risk['streak']=0
    if risk['streak']>=cfg.consecutive_losses and not risk.get('pause_until'):
        risk['pause_until']=now+cfg.pause_minutes*60
    return True
