import math
import unittest
from thoth.models import Settings
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.verification import strategy_fingerprint


def candles(prices):
    return [dict(time=i*60,open=p,high=p+1,low=p-1,close=p,volume=10) for i,p in enumerate(prices)]


class SMACrossTests(unittest.TestCase):
    def test_confirmed_crosses_both_ways_without_sr_filter(self):
        cfg=Settings(strategy='sma_cross',sma_enabled=True,sma_length=3,major=False,minor=False)
        rows=candles([100.]*200+[110.]*10+[80.]*10)
        result=evaluate(rows,cfg,now=rows[-1]['time']+60)
        self.assertEqual(result[200]['signal'],'Long')
        self.assertEqual(sum(b['signal']=='Long' for b in result),1)
        self.assertEqual(sum(b['signal']=='Short' for b in result),1)
        self.assertTrue(all(not b['levels'] for b in result))
        self.assertIsNone(evaluate(rows,cfg,now=rows[200]['time']+59)[-1]['signal'])
        self.assertEqual(evaluate(rows[:200],cfg,now=200*60)[-1]['sma'],100)

    def test_no_first_observation_or_trend_entry(self):
        rows=candles([100.+i*.1 for i in range(300)])
        result=evaluate(rows,Settings(strategy='sma_cross'),now=300*60)
        self.assertTrue(all(b['signal'] is None for b in result))

    def test_gap_restarts_warmup_and_cannot_generate_false_cross(self):
        rows=candles([100.]*200+[110.])
        rows[-1]['time']+=60
        self.assertIsNone(evaluate(rows,Settings(strategy='sma_cross'),now=999999)[-1]['signal'])

    def test_restart_matches_batch_and_state_remains_bounded(self):
        cfg=Settings(strategy='sma_cross')
        rows=candles([100.+8*math.sin(i/30) for i in range(2500)])
        expected=evaluate(rows,cfg,now=2500*60)
        state=IndicatorState(cfg);actual=[]
        for i,row in enumerate(rows):
            if i%173==0:state=IndicatorState(cfg,state.checkpoint())
            actual.append(state.step(row))
        self.assertEqual(actual,expected)
        self.assertLessEqual(len(state.cross.closes),200)
        self.assertEqual(state.buffer,[])
        self.assertNotEqual(strategy_fingerprint(cfg),strategy_fingerprint(Settings()))
