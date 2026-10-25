import unittest
from thoth.volume_delta import signal

class VolumeDeltaTests(unittest.TestCase):
    def rows(self,direction='Long',interval=60):
        return [dict(time=i*interval,open=100+i if direction=='Long' else 200-i,close=101+i if direction=='Long' else 199-i,volume=10) for i in range(60)]
    def test_minute_proxy_and_sma_alignment_both_sides(self):
        for side in ('Long','Short'):
            rows=self.rows(side);result=signal(rows,[],'1',rows[-1]['time']+20)
            self.assertEqual(result['signal'],side)
            self.assertEqual(result['normalized_delta'],1 if side=='Long' else -1)
    def test_filter_blocks_wrong_alignment_and_zero_volume_skips(self):
        rows=self.rows('Long');rows[-1].update(open=200,close=198)
        result=signal(rows,[],'1',rows[-1]['time']+20)
        self.assertIsNone(result['signal'])
        self.assertEqual(signal(rows,[],'1',rows[-1]['time']+20,sma_filter=False)['signal'],'Short')
        rows[-1]['volume']=0
        self.assertFalse(signal(rows,[],'1',rows[-1]['time']+20)['ready'])
    def test_lower_candle_volume_sign_is_not_parent_direction_and_gaps_fail(self):
        rows=self.rows('Long',300);start=rows[-1]['time']
        lower=[dict(time=start+i*60,open=100,close=99,volume=1) for i in range(3)]
        result=signal(rows,lower,'5',start+140)
        self.assertIsNone(result['signal'])
        self.assertEqual(signal(rows,lower,'5',start+140,bull_type='weak')['signal'],'Long')
        with self.assertRaisesRegex(ValueError,'Incomplete'):signal(rows,[lower[0],lower[2]],'5',start+140)
        mixed=[dict(time=start,open=100,close=101,volume=3),dict(time=start+60,open=100,close=99,volume=1),dict(time=start+120,open=100,close=100,volume=6)]
        result=signal(rows,mixed,'5',start+140)
        self.assertEqual((result['bull_volume'],result['bear_volume'],result['delta']),(3,1,2))
        self.assertAlmostEqual(result['normalized_delta'],.2)
        self.assertEqual(result['signal'],'Long')

    def test_daily_lower_pages_are_bounded_and_closed_history_reused(self):
        from thoth.volume_delta import lower_inputs
        rows=[dict(time=0)];all_rows=[dict(time=i*60,open=100,close=101,volume=1) for i in range(1440)]
        calls=[]
        def fetch(symbol,interval,limit=1000,end=None):
            calls.append(end)
            return [row for row in all_rows if end is None or row['time']*1000<=end][-limit:]
        first=lower_inputs(fetch,'BTCUSDT','D',rows,86390)
        self.assertEqual(len(first),1440);self.assertEqual(len(calls),2)
        second=lower_inputs(fetch,'BTCUSDT','D',rows,86395,first)
        self.assertEqual(len(second),1440);self.assertEqual(len(calls),3)

    def test_minute_reference_batch_stream_and_checkpoint_parity(self):
        from thoth.models import Settings
        from thoth.indicator import evaluate
        from thoth.indicator_stream import IndicatorState
        cfg=Settings(strategy='volume_delta_intrabar',timeframe='1')
        rows=self.rows()
        for row in rows:row.update(high=max(row['open'],row['close'])+1,low=min(row['open'],row['close'])-1)
        expected=evaluate(rows,cfg,now=rows[-1]['time']+60)
        state=IndicatorState(cfg);actual=[]
        for i,row in enumerate(rows):
            if i==30:state=IndicatorState(cfg,state.checkpoint())
            actual.append(state.step(row))
        self.assertEqual(actual,expected)
