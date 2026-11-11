#!/usr/bin/env python3
"""Public Bybit minute candles for offline research. No keys, orders or settings writes."""
import argparse
import datetime as dt
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thoth.crypto_ob_strength import THRESHOLDS


def save(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    with gzip.open(temporary,'wt') as handle:json.dump(data,handle)
    temporary.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,default=30,choices=range(1,61))
    parser.add_argument('--warmup-days',type=int,default=2,choices=range(1,15))
    parser.add_argument('--end',help='Exclusive UTC end, e.g. 2026-11-09T00:00:00Z; default last UTC midnight')
    parser.add_argument('--output',type=Path,default=Path('/tmp/thoth-crypto-minute-study.json.gz'))
    args=parser.parse_args()
    end=int(dt.datetime.fromisoformat(args.end.replace('Z','+00:00')).timestamp()) if args.end else int(time.time())//86400*86400
    if end%3600:parser.error('--end must align to a UTC hour')
    scored=end-args.days*86400;start=scored-args.warmup_days*86400
    output=args.output;output.parent.mkdir(parents=True,exist_ok=True)
    cache=output.parent/(output.name+'.cache');cache.mkdir(exist_ok=True)
    result=dict(source='Bybit linear public kline',interval='1',start=start,scored_start=scored,end_exclusive=end,thresholds=dict(THRESHOLDS),symbols={},errors={})
    for symbol in THRESHOLDS:
        path=cache/(symbol+'-'+str(start)+'-'+str(end)+'.json.gz')
        rows={}
        if path.exists():
            with gzip.open(path,'rt') as handle:rows={r['time']:r for r in json.load(handle)['rows']}
        cursor=min(rows)*1000-1 if rows else end*1000-1
        try:
            pages=0
            while cursor>=start*1000:
                url='https://api.bybit.com/v5/market/kline?'+urllib.parse.urlencode(dict(category='linear',symbol=symbol,interval='1',start=start*1000,end=cursor,limit=1000))
                for attempt in range(6):
                    try:
                        with urllib.request.urlopen(url,timeout=30) as response:data=json.load(response)
                        if data.get('retCode')!=0:raise RuntimeError(str(data))
                        batch=data['result']['list'];break
                    except Exception:
                        if attempt==5:raise
                        time.sleep(min(2**attempt,16))
                if not batch:break
                oldest=min(int(r[0]) for r in batch)
                if oldest>cursor:raise RuntimeError('Pagination did not advance')
                for r in batch:
                    stamp=int(r[0])//1000
                    if start<=stamp<end:rows[stamp]=dict(time=stamp,**dict(zip(('open','high','low','close','volume'),map(float,r[1:6]))))
                cursor=oldest-1;pages+=1
                if pages%5==0:
                    save(path,dict(rows=[rows[t] for t in sorted(rows)]));print(symbol,'saved',len(rows),flush=True)
                time.sleep(.13)
            ordered=[rows[t] for t in sorted(rows)]
            if not ordered or ordered[0]['time']!=start or ordered[-1]['time']!=end-60 or any(b['time']-a['time']!=60 for a,b in zip(ordered,ordered[1:])):
                raise RuntimeError('Incomplete minute history; no missing candles fabricated')
            result['symbols'][symbol]=dict(count=len(ordered),rows=ordered)
            print(symbol,'COMPLETE',len(ordered),flush=True)
        except Exception as error:
            result['errors'][symbol]=str(error);print(symbol,'INCOMPLETE:',error,flush=True)
        finally:save(path,dict(rows=[rows[t] for t in sorted(rows)]))
    save(output,result)
    print('DONE:',output,'complete:',len(result['symbols']),'unavailable:',len(result['errors']),flush=True)
    print('Run again with the SAME --end to resume incomplete symbols. Upload gzip for offline analysis.')

if __name__=='__main__':main()
