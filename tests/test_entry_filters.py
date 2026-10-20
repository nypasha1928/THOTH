import json
import unittest
from thoth.models import Settings
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.verification import strategy_fingerprint
from test_indicator import fixture


class EntryFilterTests(unittest.TestCase):
    def test_optional_filters_independent_and_combined(self):
        rows=fixture(1600)
        raw=evaluate(rows,Settings(sma_enabled=False,pivot_long=3,pivot_short=1),now=96000)
        self.assertTrue(any(b['signal']=='Long' for b in raw))
        self.assertTrue(any(b['signal']=='Short' for b in raw))
        for price_filter,alignment_filter in [(False,False),(True,False),(False,True),(True,True)]:
            cfg=Settings(sma_enabled=price_filter,sma_7_50_filter_enabled=alignment_filter,pivot_long=3,pivot_short=1)
            bars=evaluate(rows,cfg,now=96000)
            state=IndicatorState(cfg);stream=[]
            for i,row in enumerate(rows):
                stream.append(state.step(row))
                if i==500:state=IndicatorState(cfg,json.loads(json.dumps(state.checkpoint())))
            self.assertEqual(stream,bars)
            for i,(unfiltered,bar) in enumerate(zip(raw,bars)):
                allowed=[]
                for side in ['Long','Short']:
                    passing=unfiltered['conflict'] or unfiltered['signal']==side
                    if price_filter:
                        avg=sum(r['close'] for r in rows[max(0,i-199):i+1])/200
                        passing=passing and i>=199 and (rows[i]['close']>avg if side=='Long' else rows[i]['close']<avg)
                    if alignment_filter:
                        fast=sum(r['close'] for r in rows[max(0,i-6):i+1])/7
                        slow=sum(r['close'] for r in rows[max(0,i-49):i+1])/50
                        passing=passing and i>=49 and (fast>slow if side=='Long' else fast<slow)
                    if passing:allowed.append(side)
                self.assertEqual(bar['signal'],allowed[0] if len(allowed)==1 else None)

    def test_short_sma_buffer_and_strategy_independence(self):
        rows=fixture(300)
        cfg=Settings(sma_enabled=False,sma_length=5,sma_7_50_filter_enabled=True)
        state=IndicatorState(cfg)
        self.assertEqual([state.step(r) for r in rows],evaluate(rows,cfg,now=18000))
        cross=Settings(strategy='sma_cross')
        filtered=cross.model_copy(update={'sma_7_50_filter_enabled':True,'sma_enabled':False})
        self.assertEqual(evaluate(rows,cross,now=18000),evaluate(rows,filtered,now=18000))
        self.assertNotEqual(strategy_fingerprint(Settings()),strategy_fingerprint(Settings(sma_7_50_filter_enabled=True)))
