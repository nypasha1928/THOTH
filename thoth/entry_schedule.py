"""New York wall-clock entry restrictions, independent of indicator signals."""
from datetime import datetime,timedelta,time as wall_time
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('America/New_York')
FIELDS=('entry_daily_enabled','entry_daily_start','entry_daily_end','entry_weekend_enabled',
        'entry_weekend_start_day','entry_weekend_start','entry_weekend_end_day','entry_weekend_end','entry_schedule_scope')


def minutes(value):
    hour,minute=map(int,value.split(':'))
    return hour*60+minute


def within(value,start,end,period):
    return (value-start)%period < (end-start)%period


def applies(cfg):
    group='tradfi' if cfg.active_symbol_list.startswith('tradfi') else 'main'
    return cfg.entry_schedule_scope in ('all',group)


def reason(cfg,now):
    if not applies(cfg):return ''
    local=datetime.fromtimestamp(now,ZONE)
    minute=local.hour*60+local.minute+local.second/60+local.microsecond/60000000
    reasons=[]
    if cfg.entry_daily_enabled and within(minute,minutes(cfg.entry_daily_start),minutes(cfg.entry_daily_end),1440):
        reasons.append('daily '+cfg.entry_daily_start+'–'+cfg.entry_daily_end)
    if cfg.entry_weekend_enabled and within(local.weekday()*1440+minute,
            cfg.entry_weekend_start_day*1440+minutes(cfg.entry_weekend_start),
            cfg.entry_weekend_end_day*1440+minutes(cfg.entry_weekend_end),10080):
        reasons.append('weekend window')
    return 'Scheduled entry pause (New York): '+' + '.join(reasons) if reasons else ''


def status(cfg,now):
    enabled=cfg.entry_daily_enabled or cfg.entry_weekend_enabled
    active=enabled and applies(cfg);blocked=bool(reason(cfg,now))
    result=dict(enabled=enabled,applies=active,blocked=blocked,reason=reason(cfg,now),
        timezone='America/New_York',scope=cfg.entry_schedule_scope,next_open_at=None,next_block_at=None)
    if not active:return result
    # Include both folds and hour boundaries: UTC conversion handles repeated or
    # skipped local hours without assuming a fixed EST/EDT offset.
    date=datetime.fromtimestamp(now,ZONE).date();candidates=set()
    boundaries={minutes(cfg.entry_daily_start),minutes(cfg.entry_daily_end),minutes(cfg.entry_weekend_start),minutes(cfg.entry_weekend_end),*range(0,1440,60)}
    for day in range(9):
        current=date+timedelta(days=day)
        for minute in boundaries:
            for fold in (0,1):
                value=datetime.combine(current,wall_time(minute//60,minute%60),ZONE).replace(fold=fold).timestamp()
                if value>now:candidates.add(value)
    for value in sorted(candidates):
        if bool(reason(cfg,value))!=blocked:
            result['next_open_at' if blocked else 'next_block_at']=value;break
    return result
