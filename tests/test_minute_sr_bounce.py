"""Exact uploaded S/R Bounce/Rejection conditions, scoped to the minute gate."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings
from thoth.staged_entry import HOUR_GATE
from thoth.runner import Runner
from thoth.sr_bounce import evaluate,observe
from thoth.signal_sources import source_label

NOW=1793275200.
CFG=Settings(strategy=HOUR_GATE)


def candle(stamp=NOW,**changes):
    return dict(time=stamp,open=100,high=102,low=98,close=101,volume=0)|changes


def level(key,value):
    return {key:dict(value=value,pivot_time=NOW-600)}


class NativeBounceTests(unittest.TestCase):
    def test_all_native_support_and_resistance_families(self):
        for term in ('long','short'):
            for grade in ('major',):
                support=term+'_support_'+grade;resistance=term+'_resistance_'+grade
                result=evaluate(candle(),100,level(support,99),CFG)
                self.assertEqual(result['signal'],'Long')
                self.assertEqual(result['entry_sources'],['sr_bounce:'+support])
                result=evaluate(candle(close=99),100,level(resistance,101),CFG)
                self.assertEqual(result['signal'],'Short')
                self.assertEqual(result['entry_sources'],['sr_rejection:'+resistance])

    def test_exact_touch_strict_close_and_previous_bar_close_not_open_or_previous_tick(self):
        sup=level('long_support_major',99);res=level('short_resistance_major',101)
        self.assertEqual(evaluate(candle(low=99),100,sup,CFG)['signal'],'Long')
        self.assertIsNone(evaluate(candle(close=99),100,sup,CFG)['signal'])
        self.assertIsNone(evaluate(candle(close=100),100,sup,CFG)['signal'])
        self.assertIsNone(evaluate(candle(close=100.5),101,sup,CFG)['signal'])
        self.assertEqual(evaluate(candle(open=97,low=96),100,sup,CFG)['signal'],'Long') # Pine has NO open-side requirement
        self.assertEqual(evaluate(candle(high=101,close=99),100,res,CFG)['signal'],'Short')
        self.assertIsNone(evaluate(candle(close=101),100,res,CFG)['signal'])
        self.assertIsNone(evaluate(candle(close=100),100,res,CFG)['signal'])
        self.assertEqual(evaluate(candle(open=103,high=104,close=99),100,res,CFG)['signal'],'Short')
        self.assertIsNone(evaluate(candle(close=102),100,res,CFG)['signal']) # breakout alone is not rejection
        self.assertIsNone(evaluate(candle(close=98),100,sup,CFG)['signal']) # breakdown alone is not bounce

    def test_forming_entry_emits_once_after_baseline_without_waiting_for_close(self):
        tracker={};levels=level('long_support_major',99)
        rows=[candle(NOW-60,close=100),candle(low=100,close=100)]
        self.assertIsNone(observe(tracker,rows,levels,CFG,NOW+10,30)['signal'])
        rows[-1]=candle(low=99,close=101)
        self.assertEqual(observe(tracker,rows,levels,CFG,NOW+15,30)['signal'],'Long')
        self.assertIsNone(observe(tracker,rows,levels,CFG,NOW+20,30)['signal'])
        self.assertIsNone(observe({},rows,levels,CFG,NOW+25,0)['signal'])
        rows[-1]=candle(low=98,close=102)

    def test_new_pivot_and_recovery_do_not_replay_existing_condition(self):
        levels=level('short_resistance_major',101);tracker={}
        rows=[candle(NOW-60,close=100),candle(close=99)]
        self.assertIsNone(observe(tracker,rows,levels,CFG,NOW+10,30)['signal'])
        self.assertIsNone(observe(tracker,rows,levels,CFG,NOW+15,30)['signal'])
        changed=level('short_resistance_major',100.5)
        self.assertIsNone(observe(tracker,rows,changed,CFG,NOW+20,30)['signal'])
        self.assertIsNone(observe(tracker,rows,changed,CFG,NOW+25,30)['signal'])
        self.assertIsNone(observe(tracker,rows,changed,CFG,NOW+26,0)['signal'])

    def test_minute_observer_scope_hourly_breakout_and_other_strategy_unchanged(self):
        for strategy,expected,prefix in [(HOUR_GATE,'Short','sr_rejection:'),('ob_sr_minute_hourly',None,None)]:
            with self.subTest(strategy=strategy),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=strategy)
                runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                levels=level('long_resistance_major',101)
                runner.analysis=lambda *a,**k:([dict(time=NOW-60,levels=levels)],200)
                rows=[candle(NOW-(59-i)*60,close=100,high=100,low=100) for i in range(60)]
                runner.confirmation_symbol_cycles['PAXGUSDT']=1
                runner.order_block_sr_branch('PAXGUSDT',cfg,rows,NOW+10)
                rows[-1]=candle(high=102,close=99)
                runner.confirmation_symbol_cycles['PAXGUSDT']=2
                result=runner.order_block_sr_branch('PAXGUSDT',cfg,rows,NOW+15)
                self.assertEqual(result['signal'],expected)
                if prefix:self.assertEqual(result['entry_sources'],[prefix+'long_resistance_major'])
        # The source module defining CLOSED 1h permission is untouched and still opens a breakout side.
        from thoth.closed_order_block_sr import evaluate as hourly
        from test_closed_inverted_ob_sr import history,NOW as HOUR_NOW,sr_result
        with patch('thoth.order_block.reference',return_value=[dict(ready=True,signal=None,entry_sources=[],conflict=False)]):
            result=hourly(history(),CFG,HOUR_NOW,sr_result('Long'))
            self.assertEqual(result['signal'],'Long');self.assertTrue(all(s.startswith('sr:') for s in result['entry_sources']))

    def test_labels_distinguish_minute_bounce_and_rejection(self):
        self.assertEqual(source_label(['htf:1:sr_bounce:long_support_major']),'1-minute S/R Bounce · long-term major support')
        self.assertEqual(source_label(['htf:1:sr_rejection:short_resistance_minor']),'1-minute S/R Rejection · short-term minor resistance')


class ClosedBounceExitTests(unittest.TestCase):
    def test_native_bounce_rejection_close_required_and_breakout_not_used(self):
        for side,key,value,row in [
            ('Long','long_support_major',99,candle(low=98,close=101)),
            ('Short','short_resistance_major',101,candle(high=102,close=99)),
            (None,'long_resistance_major',101,candle(close=102)),
        ]:
            with self.subTest(side=side),tempfile.TemporaryDirectory() as tmp:
                runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                levels=level(key,value)
                runner.analysis=lambda symbol,cfg,rows,now,**kwargs:([dict(time=int(now)//60*60-60,levels=levels,signal='Long',entry_sources=['sr:long_resistance_major'])],200)
                rows=[candle(NOW-(59-i)*60,high=100,low=100,close=100) for i in range(60)]
                rows[-1]=row
                runner.confirmation_symbol_cycles['PAXGUSDT']=1
                self.assertIsNone(runner.closed_minute_exit_observation('PAXGUSDT',CFG,rows,NOW+55)['exit_signal'])
                rows.append(candle(NOW+60,close=100))
                runner.confirmation_symbol_cycles['PAXGUSDT']=2
                out=runner.closed_minute_exit_observation('PAXGUSDT',CFG,rows,NOW+61)
                self.assertEqual(out['exit_signal'],side,out)
                if side:self.assertTrue(any('sr_bounce:' in s or 'sr_rejection:' in s for s in out['exit_sources']))
