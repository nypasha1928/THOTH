"""Offline prototype only: no API client, credentials, UI activation or orders."""
import unittest
from dataclasses import replace
from tools.ml_rsi_draft import DraftConfig,evaluate_default,cross_events,corrected_clusters


def candles(closes,start=0):
    return [dict(time=start+i*3600,open=value,high=value+1,low=value-1,close=value,volume=100+i) for i,value in enumerate(closes)]


class RSIDraftTests(unittest.TestCase):
    def test_wilder_rsi_known_seed_and_default_ema_smoothing(self):
        prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]
        raw=evaluate_default(candles(prices),DraftConfig(smooth=False))
        self.assertIsNone(raw[13]['rsi'])
        self.assertAlmostEqual(raw[14]['rsi'],70.46413502109705)
        smoothed=evaluate_default(candles(prices))
        self.assertAlmostEqual(smoothed[14]['rsi'],raw[14]['rsi'])
        self.assertAlmostEqual(smoothed[15]['rsi'],.4*raw[15]['rsi']+.6*raw[14]['rsi'])

    def test_all_six_signal_directions_and_strict_threshold_crossing(self):
        thresholds=dict(static30=30,static70=70,ml_short=40,ml_long=60)
        up=cross_events(20,80,thresholds,thresholds)
        down=cross_events(80,20,thresholds,thresholds)
        self.assertEqual(up,[('static30_up','Long'),('ml_short_up','Long'),('ml_long_up','Long')])
        self.assertEqual(down,[('static70_down','Short'),('ml_short_down','Short'),('ml_long_down','Short')])
        self.assertEqual(cross_events(20,30,thresholds,thresholds),[])
        self.assertEqual(cross_events(None,80,thresholds,thresholds),[])

    def test_rolling_rsi_without_ml_has_no_invented_ml_events(self):
        rows=candles([100-i for i in range(20)]+[82+i for i in range(30)])
        cfg=DraftConfig(smooth=False,volume_filter=False,vwap_filter=False,sma_filter=False)
        values=evaluate_default(rows,cfg)
        self.assertTrue(any(e['kind']=='static30_up' for row in values for e in row['events']))
        self.assertTrue(all(row['thresholds']['ml_long'] is None for row in values))
        self.assertFalse(any(e['kind'].startswith('ml_') for row in values for e in row['events']))

    def test_volume_gate_and_static_volume_bypass_are_independent(self):
        rows=candles([100-i for i in range(20)]+[82+i for i in range(30)])
        cfg=DraftConfig(smooth=False,vwap_filter=False,sma_filter=False,volume_multiplier=5)
        self.assertFalse(any(row['events'] for row in evaluate_default(rows,cfg)))
        bypass=evaluate_default(rows,replace(cfg,volume_for_static=False))
        self.assertTrue(any(row['events'] for row in bypass))

    def test_daily_utc_vwap_resets_and_weights_by_volume(self):
        rows=candles([100,200,300],start=23*3600)
        rows[1]['volume']=100;rows[2]['volume']=300
        result=evaluate_default(rows)
        self.assertEqual([r['vwap'] for r in result],[100,200,275])

    def test_corrected_ml_is_explicit_and_finite_for_constant_clusters(self):
        self.assertEqual(corrected_clusters([50]*5,100),{'short':50,'long':50})
        rows=candles([100+i%7 for i in range(100)])
        result=evaluate_default(rows,DraftConfig(experimental_ml=True,max_data=20))
        self.assertIsNotNone(result[-1]['thresholds']['ml_long'])
        self.assertLessEqual(result[-1]['thresholds']['ml_short'],result[-1]['thresholds']['ml_long'])

    def test_tentative_forming_recalculation_does_not_modify_prior_bars(self):
        rows=candles([100+i%7 for i in range(80)])
        cfg=DraftConfig(experimental_ml=True,max_data=20)
        original=evaluate_default(rows,cfg)
        self.assertEqual(evaluate_default(rows[:60],cfg),original[:60])
        rows[-1].update(open=110,close=110,high=111,low=109)
        revised=evaluate_default(rows,cfg)
        self.assertEqual(original[:-1],revised[:-1])
        self.assertNotEqual(original[-1]['rsi'],revised[-1]['rsi'])

    def test_bad_candles_and_gaps_are_rejected(self):
        rows=candles([100,101]);rows[1]['time']+=3600
        with self.assertRaises(ValueError):evaluate_default(rows)
        rows=candles([100]);rows[0]['volume']=-1
        with self.assertRaises(ValueError):evaluate_default(rows)
        with self.assertRaises(ValueError):DraftConfig(max_iterations=0)
