"""Descriptive statistics of stored, reconciled trades; no order operations."""
from collections import defaultdict
from math import isfinite,isclose
from datetime import datetime
from zoneinfo import ZoneInfo


def summarize(records):
    trades=wins=losses=breakeven=0
    pnl=profit=loss=0.
    durations=[]
    for record in records:
        value=float(record['pnl'])
        if not isfinite(value):raise ValueError('Invalid recorded trade P&L')
        trades+=1;pnl+=value
        if value>0:wins+=1;profit+=value
        elif value<0:losses+=1;loss-=value
        else:breakeven+=1
        start=record.get('opened_at');end=record.get('closed_at')
        if isinstance(start,(int,float)) and isinstance(end,(int,float)) and isfinite(start) and isfinite(end) and end>=start:durations.append(end-start)
    return dict(trades=trades,wins=wins,losses=losses,breakeven=breakeven,realized_pnl=pnl,
        gross_profit=profit,gross_loss=loss,win_rate=wins/trades*100 if trades else None,
        average_pnl=pnl/trades if trades else None,profit_factor=profit/loss if loss else None,
        average_hold_seconds=sum(durations)/len(durations) if durations else None,hold_time_records=len(durations))


def analyze(records,time_basis='entry'):
    records=list(records)
    coins=defaultdict(list);strategies=defaultdict(list);combined=defaultdict(list)
    for record in records:
        symbol=record['symbol'];strategy=record.get('strategy','unknown')
        coins[symbol].append(record);strategies[strategy].append(record);combined[(symbol,strategy)].append(record)
    return dict(totals=summarize(records),timing=timing_analysis(records,time_basis),
        by_symbol=[dict(symbol=key,**summarize(value)) for key,value in sorted(coins.items())],
        by_strategy=[dict(strategy=key,**summarize(value)) for key,value in sorted(strategies.items())],
        by_symbol_strategy=[dict(symbol=key[0],strategy=key[1],**summarize(value)) for key,value in sorted(combined.items())])


NY=ZoneInfo('America/New_York')
WEEKDAYS=('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')


def timing_analysis(records,basis='entry'):
    if basis not in ('entry','exit'):raise ValueError('Unsupported time grouping basis')
    field='opened_at' if basis=='entry' else 'closed_at'
    groups={name:defaultdict(list) for name in ('hour','weekday','date')}
    dates={name:defaultdict(set) for name in groups};included=[];excluded=0
    for record in records:
        value=record.get(field)
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not isfinite(value):excluded+=1;continue
        try:local=datetime.fromtimestamp(value,NY)
        except (ValueError,OverflowError,OSError):excluded+=1;continue
        date=local.date().isoformat();included.append(date)
        for name,key in (('hour',local.hour),('weekday',local.weekday()),('date',date)):
            groups[name][key].append(record);dates[name][key].add(date)
    def row(name,key,label):
        result=dict(key=key,label=label,**summarize(groups[name][key]),observed_days=len(dates[name][key]))
        result['limited_sample']=bool(result['trades'] and (result['trades']<10 or name!='date' and result['observed_days']<3))
        return result
    hours=[row('hour',h,f'{h:02}:00–{h:02}:59') for h in range(24)]
    weekdays=[row('weekday',d,WEEKDAYS[d]) for d in range(7)]
    calendar=[row('date',date,date) for date in sorted(groups['date'],reverse=True)]
    def extremes(rows):
        observed=[r for r in rows if r['trades']]
        if not observed:return dict(best=None,worst=None)
        def pick(value):
            ties=[r for r in observed if isclose(r['realized_pnl'],value,rel_tol=0,abs_tol=1e-9)]
            return {**ties[0],'tied_buckets':len(ties)}
        return dict(best=pick(max(r['realized_pnl'] for r in observed)),worst=pick(min(r['realized_pnl'] for r in observed)))
    return dict(timezone='America/New_York',basis=basis,included_trades=len(included),excluded_trades=excluded,
        first_date=min(included) if included else None,last_date=max(included) if included else None,
        hours=hours,weekdays=weekdays,dates=calendar,ranking={name:extremes(rows) for name,rows in (('hour',hours),('weekday',weekdays),('date',calendar))})
