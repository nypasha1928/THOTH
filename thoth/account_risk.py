"""Transfer-neutral daily peak protection and independent symbol cooldowns."""
from datetime import datetime, timezone
from math import isfinite


def day(stamp):
    return datetime.fromtimestamp(stamp,timezone.utc).date().isoformat()


def new_risk():
    return dict(day=None,baseline=None,baseline_ms=None,last_sample=None,loss_pct=0,
                streak=0,pause_until=None,daily_stop=False,baseline_required=False,
                seen_closures=[],portfolio_stop=False,peak=None,peak_ms=None,loss_allowance=None,
                loss_used=None,remaining=None,remaining_pct=None,stop_equity=None,symbols={})


def expire(tracker,now):
    if tracker.get('pause_until') and now>=tracker['pause_until']:
        tracker.update(pause_until=None,streak=0)


def symbol_paused(risk,symbol,cfg,now):
    if cfg.loss_streak_scope!='per_coin':return False
    tracker=risk.setdefault('symbols',{}).get(symbol)
    if not tracker:return False
    expire(tracker,now)
    return bool(tracker.get('pause_until') and now<tracker['pause_until'])


def start_daily_period(risk,equity,cfg,now):
    """Replace every daily-derived value together; retain independent risk controls."""
    if equity<=0:raise ValueError('A positive account equity baseline is required')
    allowance=equity*cfg.daily_loss_pct/100
    risk.update(day=day(now),baseline=equity,baseline_ms=int(now*1000),
                peak=equity,peak_ms=int(now*1000),loss_allowance=allowance,
                loss_used=0.,remaining=allowance,remaining_pct=cfg.daily_loss_pct,
                stop_equity=equity-allowance,daily_stop=False,
                baseline_required=False,loss_pct=0.,last_sample=now)


def sample(risk,equity,net_transfers,cfg,now,initial=False):
    if not all(isfinite(v) for v in (equity,net_transfers)) or equity<0:
        raise ValueError('Invalid account equity sample')
    today=day(now)
    if risk['day']!=today:
        midnight=datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp()
        near_midnight=(now-midnight<=30 and risk['last_sample'] is not None
                       and 0<=now-risk['last_sample']<=30)
        if risk['day'] is None or initial or near_midnight:
            start_daily_period(risk,equity,cfg,now)
            net_transfers=0
        else:
            risk.update(baseline_required=True)
            risk['last_sample']=now
            return 'baseline_required'
    adjusted=equity-net_transfers
    # Old deployments did not retain intraday highs. Begin from the larger of
    # starting equity and the first verified sample; never invent earlier peaks.
    previous_peak=risk.get('peak')
    floor=previous_peak if previous_peak is not None else risk['baseline']
    if adjusted>floor:risk['peak_ms']=int(now*1000)
    risk['peak']=max(floor,adjusted)
    allowance=risk['baseline']*cfg.daily_loss_pct/100
    used=max(0,risk['peak']-adjusted)
    risk.update(loss_allowance=allowance,loss_used=used,remaining=max(0,allowance-used),
                remaining_pct=max(0,(allowance-used)/risk['baseline']*100),
                stop_equity=risk['peak']-allowance+net_transfers,
                loss_pct=used/risk['baseline']*100,last_sample=now)
    if used>=allowance:risk['daily_stop']=True
    expire(risk,now)
    for tracker in risk.setdefault('symbols',{}).values():expire(tracker,now)
    if risk['daily_stop']:return 'daily_stop'
    if cfg.loss_streak_scope=='account' and risk.get('pause_until') and now<risk['pause_until']:return 'cooldown'
    return 'ok'


def apply_result(tracker,pnl,cfg,now):
    expire(tracker,now)
    if pnl<0:tracker['streak']+=1
    elif pnl>0:tracker['streak']=0
    if tracker['streak']>=cfg.consecutive_losses and not tracker.get('pause_until'):
        tracker['pause_until']=now+cfg.pause_minutes*60


def closure(risk,identity,pnl,cfg,now,symbol=None):
    if identity in risk['seen_closures']:return False
    if not isfinite(pnl):raise ValueError('Invalid realized result')
    risk['seen_closures'].append(identity)
    apply_result(risk,pnl,cfg,now)  # Retain whole-account statistics for optional mode.
    if symbol:
        tracker=risk.setdefault('symbols',{}).setdefault(symbol,dict(streak=0,pause_until=None))
        apply_result(tracker,pnl,cfg,now)
    return True
