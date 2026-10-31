"""Bounded recovery of actual missing historical candles; no invented OHLC or order access."""
import sqlite3
import json
from math import isfinite
from pathlib import Path
from .indicator_stream import validate
from .timeframes import interval_seconds


def validate_hourly(rows,interval):
    """Reject minute/shifted data before it can enter the hourly cache."""
    if interval!='60':return
    previous=None
    for row in rows:
        stamp=row['time']
        if type(stamp) not in (int,float) or not isfinite(stamp) or stamp%3600:
            raise ValueError('Hourly candle timestamp is not on the UTC hour')
        if previous is not None and (stamp<=previous or (stamp-previous)%3600):
            raise ValueError('Hourly candles have mixed intervals or duplicate timestamps')
        validate(row);previous=stamp


def quarantine_hourly(path,symbol,now):
    """Archive mixed-resolution spans, including potentially overwritten hour bars.

    No OHLC is inferred. The normal bounded backfill must replace the removed
    span with real hourly candles before source validation can pass.
    """
    path=Path(path)
    if not path.exists():return dict(quarantined_bars=0)
    with sqlite3.connect(path,timeout=15) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candles'").fetchone()
        if not exists:return dict(quarantined_bars=0)
        contaminated=db.execute("SELECT 1 FROM candles WHERE symbol=? AND interval='60' AND (time%3600!=0 OR json_extract(payload,'$.time')!=time) LIMIT 1",(symbol,)).fetchone()
        if not contaminated:return dict(quarantined_bars=0)
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute("SELECT time,payload FROM candles WHERE symbol=? AND interval='60' ORDER BY time",(symbol,)).fetchall()
        bad=[];hours=set()
        for stamp,payload in rows:
            actual=json.loads(payload)['time']
            if type(actual) not in (int,float) or not isfinite(actual):
                raise ValueError('Invalid stored hourly candle timestamp')
            if stamp%3600 or actual!=stamp:
                bad.append(stamp)
                for value in (stamp,actual):
                    hour=int(value)//3600*3600
                    hours.update((hour,hour+3600))
        if not bad:return dict(quarantined_bars=0)
        affected=[(stamp,payload) for stamp,payload in rows if int(stamp)//3600*3600 in hours or stamp in bad]
        db.execute('CREATE TABLE IF NOT EXISTS candle_quarantine(symbol TEXT,interval TEXT,time INTEGER,payload TEXT,reason TEXT,quarantined_at REAL,PRIMARY KEY(symbol,interval,time,payload))')
        db.executemany('INSERT OR IGNORE INTO candle_quarantine VALUES(?,?,?,?,?,?)',
            [(symbol,'60',stamp,payload,'Mixed-resolution hourly span; real hourly replacement required',now) for stamp,payload in affected])
        db.executemany("DELETE FROM candles WHERE symbol=? AND interval='60' AND time=?",[(symbol,stamp) for stamp,_ in affected])
        for table in ('checkpoints','analyses'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                db.execute('DELETE FROM '+table+" WHERE symbol=? AND interval='60'",(symbol,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='order_block_checkpoints'").fetchone():
            db.execute('DELETE FROM order_block_checkpoints WHERE symbol=?',(symbol,))
        db.execute('CREATE TABLE IF NOT EXISTS candle_repairs(symbol TEXT,interval TEXT,generation INTEGER,PRIMARY KEY(symbol,interval))')
        db.execute("INSERT INTO candle_repairs VALUES(?,'60',1) ON CONFLICT(symbol,interval) DO UPDATE SET generation=generation+1",(symbol,))
    return dict(quarantined_bars=len(affected),irregular_bars=len(bad),
        quarantine_from=min(t for t,_ in affected),quarantine_to=max(t for t,_ in affected))


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
    validate_hourly(fresh,interval)
    if not fresh:return fresh,dict(active=False,reason='Fresh candles unavailable')
    if not path.exists():return fresh,dict(active=False,reason='Initial history bootstrap')
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
            times=[r[0] for r in db.execute('SELECT time FROM candles WHERE symbol=? AND interval=? ORDER BY time',(symbol,interval))]
    except sqlite3.OperationalError:return fresh,dict(active=False,reason='History bootstrap')
    if not times:return fresh,dict(active=False,reason='Initial symbol bootstrap')
    if interval=='60' and any(t%3600 for t in times):
        raise ValueError('Mixed-resolution hourly cache requires quarantine before backfill')
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
