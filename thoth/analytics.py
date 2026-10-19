"""Descriptive statistics of stored, reconciled trades; no order operations."""
from collections import defaultdict
from math import isfinite


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


def analyze(records):
    coins=defaultdict(list);strategies=defaultdict(list);combined=defaultdict(list)
    for record in records:
        symbol=record['symbol'];strategy=record.get('strategy','unknown')
        coins[symbol].append(record);strategies[strategy].append(record);combined[(symbol,strategy)].append(record)
    return dict(totals=summarize(records),
        by_symbol=[dict(symbol=key,**summarize(value)) for key,value in sorted(coins.items())],
        by_strategy=[dict(strategy=key,**summarize(value)) for key,value in sorted(strategies.items())],
        by_symbol_strategy=[dict(symbol=key[0],strategy=key[1],**summarize(value)) for key,value in sorted(combined.items())])
