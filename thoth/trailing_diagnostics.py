"""Bounded, durable evidence of observed per-coin trailing decisions, not tick history."""
from copy import deepcopy
from .bybit_testnet import dec


def event(owner,kind,now,**values):
    report=owner.setdefault('trailing_diagnostics',{'version':1,'samples':0,'events':[],'dropped_events':0})
    report['events'].append(dict(time=now,kind=kind,**values))
    if len(report['events'])>40:
        report['dropped_events']+=len(report['events'])-40
        report['events']=report['events'][-40:]
    return report


def sample(owner,before,after,p,cfg,now):
    report=owner.setdefault('trailing_diagnostics',{'version':1,'samples':0,'events':[],'dropped_events':0})
    previous=report.get('last_sample_at')
    report.setdefault('first_sample_at',now)
    report['max_sample_gap_seconds']=max(report.get('max_sample_gap_seconds',0),max(0,now-previous) if previous is not None else 0)
    report['last_sample_at']=now;report['samples']+=1
    pnl=str(p['unrealisedPnl'])
    if report.get('maximum_observed_pnl') is None or dec(pnl)>dec(report['maximum_observed_pnl']):
        report.update(maximum_observed_pnl=pnl,maximum_observed_at=now)
    settings=dict(enabled=cfg.coin_pnl_trailing_enabled,activation=str(cfg.coin_pnl_trailing_activation_dollars),drop=str(cfg.coin_pnl_trailing_drop_dollars))
    if report.get('settings')!=settings:event(owner,'settings_observed',now,settings=settings)
    report.update(settings=settings,last_pnl=pnl,last_mark=p.get('markPrice'),state=deepcopy(after))
    for key,kind in [('active','activated'),('triggered','triggered')]:
        if after.get(key) and not before.get(key):event(owner,kind,now,pnl=pnl,state=deepcopy(after))
