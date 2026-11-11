import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings
from thoth.crypto_ob_comparison import CryptoOBComparison
from thoth.late_hour_comparison import ComparisonSettings

class FakeStrength:
    def __init__(self,symbol,checkpoint=None):
        self.last_time=(checkpoint or {}).get('last_time');self.count=(checkpoint or {}).get('count',0)
    def checkpoint(self):return dict(last_time=self.last_time,count=self.count)
    def step(self,row):
        self.last_time=row['time'];self.count+=1
        signal='Long' if row['close']>10 else 'Short' if row['close']<10 else None
        return dict(signal=signal,crypto_ob_strength=dict(block_width=1) if signal else None)

def row(t,c=10):return dict(time=t,open=10,high=max(10,c),low=min(10,c),close=c,volume=10)

class OBComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=360060.;self.worker=CryptoOBComparison(Path(self.tmp.name)/'observer.sqlite',clock=lambda:self.now)
        self.cfg=Settings(strategy='crypto_ob_strength_1h_close',symbols=['ETHUSDT'],coin_pnl_trailing_enabled=True,coin_pnl_trailing_activation_dollars=5,coin_pnl_trailing_drop_dollars=.1)
        self.rows=[row(360000-i*3600) for i in range(15,-1,-1)]
        self.mock=patch('thoth.crypto_ob_comparison.StrengthState',FakeStrength);self.mock.start();self.addCleanup(self.mock.stop)
    def send(self,rows=None):self.worker.process(('ETHUSDT',self.cfg,rows or self.rows,self.now))
    def test_rollback_and_closed_confirmation(self):
        original=json.dumps(self.rows);self.send()
        self.now+=5;self.rows[-1]=row(360000,11);self.send()
        report=self.worker.report();self.assertEqual(report['items'][0]['early']['signal'],'Long')
        self.assertEqual(report['paper']['intrabar']['closed_trades'],0)
        self.now=363605.;self.rows.append(row(363600,11));self.send()
        # Gap means no replayed closed trade, but matching evidence remains valid.
        old=next(x for x in self.worker.report()['items'] if x['bar']==360000)
        self.assertTrue(old['survived']);self.assertEqual(old['early_price_advantage_pct'],0)
        self.assertEqual(self.worker.report()['paper']['intrabar']['unverified_episodes'],1)
        self.assertEqual(json.loads(original)[0],self.rows[0])
    def test_persistence_disable_and_baseline(self):
        self.rows[-1]=row(360000,11);self.send();self.now+=5;self.send()
        self.assertIsNone(self.worker.report()['items'][0]['early'])
        second=CryptoOBComparison(self.worker.path,clock=lambda:self.now)
        self.assertEqual(second.report()['total'],1)
        self.worker.configure(ComparisonSettings(enabled=False));self.now+=5;self.send()
        self.assertFalse(CryptoOBComparison(self.worker.path).enabled)
    def test_failure_is_visible_and_no_live_dependency(self):
        self.now+=40
        with self.assertRaisesRegex(ValueError,'older'):self.worker.process(('ETHUSDT',self.cfg,self.rows,self.now-40))
        self.assertEqual(self.worker.report()['total'],0)
    def test_sampled_exits_and_fixed_trailing(self):
        settings=self.worker.settings(self.cfg);leg={}
        CryptoOBComparison.paper(leg,100,1,1,settings)
        CryptoOBComparison.paper(leg,111,None,2,settings)
        self.assertTrue(leg['position']['active'])
        events=CryptoOBComparison.paper(leg,110,None,3,settings)
        self.assertEqual(events[0]['reason'],'Sampled per-coin trailing');self.assertGreater(leg['closed_net'],0)
        self.assertIsNone(leg['position'])
    def test_normal_close_and_reversal(self):
        settings=self.worker.settings(self.cfg);leg={}
        CryptoOBComparison.paper(leg,100,1,1,settings)
        events=CryptoOBComparison.paper(leg,100,-1,2,settings)
        self.assertEqual([x['kind'] for x in events],['paper_close','paper_entry'])
        self.assertEqual(leg['position']['side'],-1)
    def test_confirmed_entry_requires_continuous_hour_boundary(self):
        self.now=363590.;self.send()
        self.now=363595.;self.rows[-1]=row(360000,11);self.send()
        self.now=363605.;self.rows.append(row(363600,10));self.send()
        with self.worker.db() as db:state=json.loads(db.execute('SELECT payload FROM states').fetchone()[0])
        self.assertEqual(state['paper']['closed']['position']['side'],1)
        self.assertTrue(next(x for x in self.worker.report()['items'] if x['bar']==360000)['survived'])

    def test_early_signal_can_disappear_and_same_direction_is_consumed(self):
        self.send();self.now+=5;self.rows[-1]=row(360000,11);self.send()
        self.now+=5;self.rows[-1]=row(360000,10);self.send()
        self.now+=5;self.rows[-1]=row(360000,11);self.send()
        self.assertEqual(len(self.worker.report()['items'][0]['direction_changes']),1)
        self.now=363605.;self.rows[-1]=row(360000,10);self.rows.append(row(363600));self.send()
        old=next(x for x in self.worker.report()['items'] if x['bar']==360000)
        self.assertFalse(old['survived']);self.assertIsNone(old['closed']['signal'])

    def test_settings_changes_are_not_paired(self):
        self.send();self.now+=5;self.rows[-1]=row(360000,11);self.send()
        self.cfg=self.cfg.model_copy(update={'coin_pnl_trailing_activation_dollars':2})
        self.now+=5;self.send();self.assertEqual(self.worker.report()['total'],2)
        self.assertEqual(len({x['identity'] for x in self.worker.report()['items']}),2)

    def test_real_order_block_replay_is_private(self):
        from test_crypto_ob_strength import candles
        rows=candles(30);self.now=rows[-1]['time']+60
        original=json.dumps(rows)
        self.mock.stop()
        self.worker.process(('ETHUSDT',self.cfg,rows,self.now))
        self.assertEqual(json.dumps(rows),original)
        self.assertEqual(self.worker.report()['total'],1)
        self.mock.start()

    def test_worker_errors_do_not_escape(self):
        self.worker.start();self.addCleanup(self.worker.stop)
        self.worker.inputs.put(('ETHUSDT',self.cfg,[],self.now));self.worker.inputs.join()
        self.assertIn('Current hourly',self.worker.report()['error'])

if __name__=='__main__':unittest.main()
