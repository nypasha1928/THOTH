import unittest
from thoth.intrabar_exit import observe_exit,ExitDataError,CANDLE_PUBLICATION_GRACE
from thoth.timeframes import INTERVALS


def history(start,duration,price=100):
    return [dict(time=start-(50-i)*duration,close=100 if i<50 else price) for i in range(51)]


class CandlePublicationTests(unittest.TestCase):
    def test_grace_on_all_timeframes_is_bounded_and_keeps_observed_difference(self):
        for timeframe,duration in INTERVALS.items():
            start=1000*duration
            tracker=dict(time=start-duration,timeframe=timeframe,difference=.5)
            prior=history(start-duration,duration)
            self.assertIsNone(observe_exit(tracker,prior,timeframe,'Buy',start+.1))
            self.assertEqual(tracker['difference'],.5)
            self.assertEqual(tracker['waiting_for_candle'],start)
            self.assertNotIn('retry_after',tracker)
            self.assertIn('below',observe_exit(tracker,history(start,duration,99),timeframe,'Buy',start+6))
            with self.assertRaises(ExitDataError):observe_exit({},prior,timeframe,'Buy',start+CANDLE_PUBLICATION_GRACE+1)

    def test_grace_never_accepts_gaps_invalid_prices_short_history_or_older_bars(self):
        start=60000;duration=60
        old=history(start-duration,duration)
        broken=[dict(r) for r in old];broken[10]['time']-=duration
        invalid=[dict(r) for r in old];invalid[-1]['close']=float('nan')
        for data in [broken,invalid,old[:-1],history(start-2*duration,duration)]:
            with self.assertRaises(ExitDataError):observe_exit({},data,'1','Buy',start+1)

    def test_short_cross_between_adjacent_candles_is_retained(self):
        start=60000
        tracker=dict(time=start-60,timeframe='1',difference=-.5,checked_at=start-1)
        self.assertIn('above',observe_exit(tracker,history(start,60,101),'1','Sell',start+3))
