"""Reference-comparison receipts invalidate when code or strategy changes."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
STRATEGY_FIELDS=('strategy','timeframe','pivot_long','pivot_short','major','minor','sma_enabled','sma_length','sma_7_50_filter_enabled')


def source_fingerprint():
    h=hashlib.sha256()
    for relative in ('thoth/indicator.py','thoth/indicator_core.py','thoth/indicator_stream.py','thoth/sma_cross.py','reference/support_resistance.pine'):
        path=ROOT/relative
        h.update(relative.encode());h.update(path.read_bytes())
    return h.hexdigest()



def execution_fingerprint():
    h=hashlib.sha256()
    for path in sorted((ROOT/'thoth').glob('*.py')):
        h.update(path.name.encode());h.update(path.read_bytes())
    return h.hexdigest()

def strategy_fingerprint(cfg):
    values=cfg.model_dump() if hasattr(cfg,'model_dump') else cfg
    return hashlib.sha256(json.dumps({k:values.get(k,'sr_breakout') if k=='strategy' else values[k] for k in STRATEGY_FIELDS},sort_keys=True).encode()).hexdigest()


def reference_status(cfg,directory):
    result={'verified':False,'reason':'TradingView reference comparison has not been supplied','bars':0}
    try:
        paths=Path(directory).glob('*.json')
        for path in paths:
            if path.is_symlink():continue
            report=json.loads(path.read_text())
            if report.get('format')!=1:continue
            if report.get('strategy_fingerprint')!=strategy_fingerprint(cfg):continue
            if report.get('source_fingerprint')!=source_fingerprint():
                result['reason']='Indicator code changed; repeat the reference comparison';continue
            if report.get('mismatch_count')!=0:
                result['reason']='Reference comparison contains mismatches';continue
            coverage=report.get('coverage',{})
            values=cfg.model_dump() if hasattr(cfg,'model_dump') else cfg
            minimum=max(500,values['sma_length'],2*values['pivot_long']+1,2*values['pivot_short']+1)
            if report.get('bars',0)<minimum or coverage.get('long_signals',0)<1 or coverage.get('short_signals',0)<1 or not {family+'_'+direction+'_'+kind for family in ('long','short') for direction in ('support','resistance') for kind in ('major','minor')}.issubset(set(coverage.get('level_types',[]))):
                result['reason']='Reference comparison lacks enough bars, levels or both signal directions';continue
            return {'verified':True,'reason':'Matching supplied reference dataset; not a profitability guarantee',
                    'bars':report['bars'],'created_at':report.get('created_at')}
    except (OSError,ValueError,KeyError,TypeError):
        result['reason']='Reference comparison receipt is invalid or unreadable'
    return result
