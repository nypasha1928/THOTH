"""Create a live-readiness receipt only from real, protected-and-closed testnet trades.

Run locally on the server after actual testnet validation. Simulator records do
not qualify. This does not enable live orders; all other gates still apply.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thoth.verification import source_fingerprint,strategy_fingerprint


def acceptance(path,destination):
    path=Path(path).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        state=json.loads(db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()[0])
    if state['management'] and state['entries']:raise ValueError('Pause entries before recording acceptance')
    if state['owned']:raise ValueError('Testnet positions and their results must be fully settled')
    source=source_fingerprint();strategy=strategy_fingerprint(state['config'])
    trades=[t for t in state['closures'] if t.get('real_exchange') is True and t.get('protection_confirmed') is True and t.get('source_fingerprint')==source and t.get('strategy_fingerprint')==strategy]
    if not state.get('real_testnet_exchange') or not trades:raise ValueError('No real protected-and-closed testnet trades qualify')
    with sqlite3.connect(path.with_suffix('.execution.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        unresolved=db.execute("SELECT COUNT(*) FROM execution WHERE phase NOT IN ('closed','cancelled','rejected')").fetchone()[0]
    if unresolved:raise ValueError('Resolve all testnet execution records first')
    receipt=dict(environment='testnet',source_fingerprint=source,strategy_fingerprint=strategy,protected_and_closed=len(trades),unresolved_orders=0,real_exchange=True,created_at=datetime.now(timezone.utc).isoformat())
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('database',type=Path);parser.add_argument('receipt',type=Path);args=parser.parse_args()
    print(json.dumps(acceptance(args.database,args.receipt),indent=2))
