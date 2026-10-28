import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from thoth.watchlist_review import read


class WatchlistReviewTests(unittest.TestCase):
    def test_scope_saved_symbols_lifecycle_rejections_and_no_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE supervisor(id INTEGER,payload TEXT);CREATE TABLE trade_history(link TEXT,payload TEXT);CREATE TABLE signal_decisions(id INTEGER,payload TEXT);')
                state={'config':{'strategy':'order_block_sr_intrabar','symbols':['BTCUSDT','ETHUSDT','SOLUSDT']},'entries':True}
                db.execute('INSERT INTO supervisor VALUES(1,?)',(json.dumps(state),))
                trades=[{'symbol':'BTCUSDT','strategy':'order_block_sr_intrabar','pnl':2,'opened_at':10,'closed_at':20}, {'symbol':'BTCUSDT','strategy':'old','pnl':-3}, {'symbol':'REMOVEDUSDT','strategy':'order_block_sr_intrabar','pnl':100}]
                db.executemany('INSERT INTO trade_history VALUES(?,?)',[(str(i),json.dumps(r)) for i,r in enumerate(trades)])
                for i,reason in enumerate(['queued: Long','entry skipped: Requested leverage is unsupported']):
                    d={'symbol':'ETHUSDT','strategy':'order_block_sr_intrabar','reason':reason,'alert_key':'same'}
                    db.execute('INSERT INTO signal_decisions VALUES(?,?)',(i,json.dumps(d)))
            with sqlite3.connect(path.with_suffix('.execution.sqlite')) as db:
                db.execute('CREATE TABLE execution(link TEXT,payload TEXT,phase TEXT,stop_dollars REAL,instrument TEXT,result TEXT)')
                for i,reduce in enumerate([False,True]):
                    db.execute('INSERT INTO execution VALUES(?,?,?,?,?,?)',(str(i),json.dumps({'symbol':'ETHUSDT','reduceOnly':reduce}),'rejected',1,json.dumps({'thoth_strategy':'order_block_sr_intrabar'}),json.dumps({'exchange_code':110126})))
                instrument={'thoth_strategy':'order_block_sr_intrabar','thoth_entry_diagnostics':{'qualified_at':200,'previous_observed_at':20}}
                db.execute('INSERT INTO execution VALUES(?,?,?,?,?,?)',('timed',json.dumps({'symbol':'BTCUSDT','side':'Buy','reduceOnly':False}),'protected',1,json.dumps(instrument),json.dumps({'entry_timing':{'submit_started_at':205}})))
            before=path.read_bytes()
            result=read(path);coins={r['symbol']:r for r in result['items']}
            self.assertEqual(result['selected'],3);self.assertEqual(result['traded'],1)
            self.assertEqual(result['realized_pnl'],2)
            self.assertEqual(coins['BTCUSDT']['median_check_gap_seconds'],180)
            self.assertEqual(coins['BTCUSDT']['median_qualified_to_submit_seconds'],5)
            self.assertEqual(coins['ETHUSDT']['signal_events'],1)
            self.assertEqual(coins['ETHUSDT']['entry_check_failures'],1)
            self.assertEqual(coins['ETHUSDT']['rejected_orders'],1)
            self.assertEqual(coins['ETHUSDT']['rejection_codes'],{'110126':1})
            self.assertIsNone(coins['SOLUSDT']['median_check_gap_seconds'])
            self.assertEqual(coins['SOLUSDT']['review_note'],'No retained closed trades')
            self.assertEqual(read(path,'all')['realized_pnl'],-1)
            self.assertEqual(path.read_bytes(),before)
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO signal_decisions VALUES(2,?)',(json.dumps({'symbol':'ETHUSDT','strategy':'order_block_sr_intrabar','reason':'entry rejected by Bybit (code 110126)','alert_key':'second'}),))
            self.assertEqual(read(path)['items'][1]['rejection_events'],1)
