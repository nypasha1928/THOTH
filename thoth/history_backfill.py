"""Bounded recovery of actual missing historical candles; no invented OHLC or order access."""
import sqlite3
from pathlib import Path
from .indicator_stream import validate
from .timeframes import interval_seconds


def gaps(times,duration):
    ordered=sorted(set(times))
    return [(a+duration,b-duration) for a,b in zip(ordered,ordered[1:]) if b>a+duration]


def recover(path,symbol,interval,fresh,fetch,now,previous=None,max_pages=2):
    """Return missing real rows for Runner.history to persist and validate.

    Normal operation adds no market calls. At most two 1,000-bar pages per coin
    per scan, oldest hole first. Empty/malformed/unavailable pages wait 60s.
    Partial progress is returned so the next scan does not repeat a filled page.
    This function never edits the candle database or indicator/execution state.
    """
    path=Path(path);duration=interval_seconds(interval)
    if not fresh:return fresh,dict(active=False,reason='Fresh candles unavailable')
    if not path.exists():return fresh,dict(active=False,reason='Initial history bootstrap')
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
            times=[r[0] for r in db.execute('SELECT time FROM candles WHERE symbol=? AND interval=? ORDER BY time',(symbol,interval))]
    except sqlite3.OperationalError:return fresh,dict(active=False,reason='History bootstrap')
    if not times:return fresh,dict(active=False,reason='Initial symbol bootstrap')
    known=set(times)|{r['time'] for r in fresh}
    missing=gaps(known,duration)
    status=dict(active=bool(missing),gap_count=len(missing),recovered_bars=0,pages=0,reason='History contiguous' if not missing else 'Backfill pending')
    if not missing:return fresh,status
    if previous and now<previous.get('retry_after',0):
        return fresh,{**status,'retry_after':previous['retry_after'],'reason':previous.get('reason','Backfill retry waiting')}
    additions={};error=None
    for _ in range(max_pages):
        missing=gaps(known,duration)
        if not missing:break
        left,right=missing[0];last=min(right,left+duration*999);limit=int((last-left)//duration)+1
        status.update(first_missing=left,pages=status['pages']+1)
        try:
            page=fetch(symbol,interval,limit=limit,end=int((last+duration)*1000-1))
            selected={}
            for row in page:
                stamp=row['time']
                # A wrong interval, shifted bar or nonadvancing page must not heal a hole.
                if left<=stamp<=last:
                    if (stamp-left)%duration:raise ValueError('Backfill candle timestamp is off interval')
                    validate(row)
                    selected[stamp]=row
            new={stamp:row for stamp,row in selected.items() if stamp not in known}
            if not new:raise ValueError('Bybit returned no missing candles')
            additions.update(new);known.update(new)
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)[:140];break
    remaining=gaps(known,duration)
    status.update(active=bool(remaining),gap_count=len(remaining),recovered_bars=len(additions),reason='Backfill complete; source validation pending' if not remaining else 'Backfill partially complete; entries remain blocked')
    if remaining:status['first_missing']=remaining[0][0]
    if error:status.update(reason='Backfill unavailable: '+error,retry_after=now+60)
    merged={r['time']:r for r in fresh};merged.update(additions)
    return [merged[t] for t in sorted(merged)],status
