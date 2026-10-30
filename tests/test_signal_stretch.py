"""Candle diagnostics are measurements, never signal or execution gates."""
import copy
import json
import unittest
from thoth.signal_stretch import snapshot,with_fill


def rows(side='Long'):
    data=[dict(time=i*3600,open=100,high=110,low=90,close=100,volume=1) for i in range(14)]
    data[-1].update(high=130 if side=='Long' else 110,low=90 if side=='Long' else 70,close=120 if side=='Long' else 80)
    return data


class SignalStretchTests(unittest.TestCase):
    def test_atr_wick_signed_fill_and_every_reference_are_separate(self):
        data=rows();before=copy.deepcopy(data)
        sr=dict(levels={'long_resistance_major':dict(value=100,pivot_time=0),'short_resistance_major':dict(value=110,pivot_time=3600)})
        ob=dict(broken_supply=[dict(high=105,time=0),dict(high=115,time=3600)],zones=dict(demand=[dict(high=108,time=7200)]))
        sources=['sr:long_resistance_major','sr:short_resistance_major','ob:supply_breakout','ob:bullish_formed']
        m=snapshot(data,'Long',sources,sr,ob,14*3600+3)
        self.assertAlmostEqual(m['atr14'],(13*20+40)/14)
        self.assertAlmostEqual(m['range_atr14'],40/((13*20+40)/14))
        self.assertEqual(m['opposing_wick_pct'],25)
        self.assertEqual(m['qualification_after_close_seconds'],3)
        self.assertEqual(len(m['references']),5)
        self.assertEqual([r['price'] for r in m['references']],[100,110,105,115,108])
        self.assertEqual(m['references'][-1]['kind'],'formed_block_edge')
        self.assertEqual(m['references'][0]['close_distance_pct'],20)
        filled=with_fill(m,dict(fill_price=121,fill_observed_at=14*3600+9))
        self.assertAlmostEqual(filled['close_to_fill_pct'],100/120)
        self.assertEqual(filled['references'][0]['entry_distance_pct'],21)
        self.assertEqual(filled['close_to_fill_observed_seconds'],9)
        self.assertNotIn('fill_price',m);self.assertEqual(data,before)
        json.dumps(filled,allow_nan=False)

    def test_short_wick_broken_demand_and_retracement_signs(self):
        m=snapshot(rows('Short'),'Short',['ob:demand_breakdown'],ob=dict(broken_demand=[dict(low=90,time=0)]))
        self.assertEqual(m['opposing_wick_pct'],25)
        self.assertAlmostEqual(m['references'][0]['close_distance_pct'],1000/90)
        self.assertEqual(with_fill(m,dict(fill_price=79))['close_to_fill_pct'],1.25)
        better=with_fill(m,dict(fill_price=81))
        self.assertEqual(better['close_to_fill_pct'],-1.25)
        self.assertNotIn('close_to_fill_observed_seconds',better)
        inside=with_fill(m,dict(fill_price=91))
        self.assertLess(inside['references'][0]['entry_distance_pct'],0)

    def test_missing_history_zero_range_missing_fill_and_time_skew_are_unknown(self):
        data=[dict(time=i*3600,open=100,high=100,low=100,close=100) for i in range(14)]
        m=snapshot(data,'Long',[])
        self.assertIsNone(m['range_atr14']);self.assertIsNone(m['opposing_wick_pct'])
        self.assertIsNone(snapshot(data[:13],'Long',[])['atr14'])
        self.assertNotIn('close_to_fill_pct',with_fill(m,{}))
        self.assertNotIn('close_to_fill_observed_seconds',with_fill(m,dict(fill_observed_at=1)))
        self.assertIsNone(snapshot([dict(time=0,close=100)],'Long',[]))
        self.assertIsNone(with_fill(None,dict(fill_price=100)))
        self.assertIsNone(with_fill({'ohlc':None},{}))
        self.assertIsNone(snapshot([None],'Long',[]))
        gap=rows();gap[-1]['time']+=3600
        self.assertIsNone(snapshot(gap,'Long',[])['atr14'])
        # Equality is a measured zero, not unknown.
        self.assertEqual(with_fill(m,dict(fill_price=100))['close_to_fill_pct'],0)

    def test_full_block_widths_normalize_without_price_rounding_or_rule_changes(self):
        sr=dict(levels={'long_resistance_major':dict(value=100,pivot_time=0)})
        ob=dict(broken_supply=[dict(high=105,low=100,time=0),dict(high=115,low=108,time=3600)],zones=dict(demand=[dict(high=108,low=104,time=7200)]))
        original=copy.deepcopy(ob)
        sources=['sr:long_resistance_major','ob:supply_breakout','ob:bullish_formed']
        m=snapshot(rows(),'Long',sources,sr,ob)
        self.assertNotIn('block_width',m['references'][0])
        for r,width in zip(m['references'][1:],[5,7,4]):
            self.assertEqual(r['block_width'],width)
            self.assertAlmostEqual(r['block_width_atr14'],width/m['atr14'])
            self.assertEqual(r['block_high']-r['block_low'],width)
        self.assertEqual(ob,original)
        filled=with_fill(m,dict(fill_price=121))
        self.assertEqual(filled['references'][1]['block_width_atr14'],m['references'][1]['block_width_atr14'])
        # Currency rescaling (e.g. cheap coins) preserves the ATR ratio and keeps tiny widths nonzero.
        scaled=[{k:(v*.00001 if k in ('open','high','low','close') else v) for k,v in row.items()} for row in rows()]
        tiny=snapshot(scaled,'Long',['ob:bullish_formed'],ob=dict(zones=dict(demand=[dict(high=.00108,low=.00104,time=0)])))
        self.assertGreater(tiny['references'][0]['block_width'],0)
        self.assertAlmostEqual(tiny['references'][0]['block_width_atr14'],m['references'][-1]['block_width_atr14'])

    def test_missing_historical_boundary_never_becomes_zero_strength(self):
        m=snapshot(rows(),'Long',['ob:bullish_formed'],ob=dict(zones=dict(demand=[dict(high=105,time=0)])))
        self.assertNotIn('block_width_atr14',m['references'][0])
        self.assertNotIn('block_width',m['references'][0])
