"""Compare the experimental Python port against TradingView CSV output.

Required columns: time, open, high, low, close, volume plus THOTH_* helper
plots from tools/reference_export.py. Uses the full exported history; no
warmup mismatches are silently discarded. Nonzero exit on any mismatch.
"""
import argparse
import csv
import json
import math
import hashlib
import sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thoth.indicator import evaluate
from thoth.models import Settings
from thoth.verification import source_fingerprint,strategy_fingerprint


def stamp(value):
    try:
        number=float(value)
        return int(number/1000 if number>100000000000 else number)
    except ValueError:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('CSV timestamps need a timezone')
        return int(parsed.timestamp())


def compare(records, cfg):
    fields=[family+'_'+direction+'_'+kind for family in ('long','short') for direction in ('support','resistance') for kind in ('major','minor')]
    required={'time','open','high','low','close','volume'}|{'THOTH_'+key for key in fields}|{'THOTH_long_signal','THOTH_short_signal'}
    if not records:raise ValueError('CSV contains no records')
    missing=required-set(records[0])
    if missing:raise ValueError('Missing CSV columns: '+', '.join(sorted(missing)))
    rows=[dict(time=stamp(r['time']),**{k:float(r[k]) for k in ('open','high','low','close','volume')}) for r in records]
    if rows!=sorted(rows,key=lambda r:r['time']):raise ValueError('CSV must be sorted oldest first')
    output=evaluate(rows,cfg,now=rows[-1]['time']+(60 if cfg.timeframe=='1' else 86400))
    mismatches=[]
    for original,actual in zip(records,output):
        for key in fields:
            raw=original['THOTH_'+key].strip()
            expected=None if raw.lower() in ('','nan','na') else float(raw)
            observed=actual['levels'].get(key,{}).get('value')
            if (expected is None)!=(observed is None) or (expected is not None and not math.isclose(expected,observed,rel_tol=1e-8,abs_tol=1e-8)):
                mismatches.append({'time':actual['time'],'field':key,'expected':expected,'actual':observed})
        for side in ('long','short'):
            expected=bool(float(original['THOTH_'+side+'_signal']))
            observed=actual['conflict'] or actual['signal']==side.title()
            if expected!=observed:mismatches.append({'time':actual['time'],'field':side+'_signal','expected':expected,'actual':observed})
    coverage={'long_signals':sum(b['signal']=='Long' or b['conflict'] for b in output),'short_signals':sum(b['signal']=='Short' or b['conflict'] for b in output),'level_types':sorted({key for b in output for key in b['levels']})}
    return {'bars':len(output),'mismatch_count':len(mismatches),'first_mismatches':mismatches[:30],'coverage':coverage}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv',type=Path)
    parser.add_argument('--settings',type=Path,help='JSON settings matching the TradingView inputs (including timeframe)')
    parser.add_argument('--report',type=Path,help='Write a reference-comparison receipt for this code/settings version')
    args=parser.parse_args()
    try:
        cfg=Settings(**json.loads(args.settings.read_text())) if args.settings else Settings()
        with args.csv.open() as file:result=compare(list(csv.DictReader(file)),cfg)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    result.update(format=1,source_fingerprint=source_fingerprint(),strategy_fingerprint=strategy_fingerprint(cfg),csv_sha256=hashlib.sha256(args.csv.read_bytes()).hexdigest(),created_at=datetime.now(timezone.utc).isoformat())
    if args.report:
        args.report.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with args.report.open('x') as report:report.write(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2,allow_nan=False))
    raise SystemExit(1 if result['mismatch_count'] else 0)
