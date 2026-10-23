import unittest
from thoth.analytics import analyze


class AnalysisTests(unittest.TestCase):
    def test_coin_and_strategy_totals_and_breakeven_denominator(self):
        records=[dict(symbol='ETHUSDT',strategy='sr_breakout',pnl=2,opened_at=10,closed_at=70),
                 dict(symbol='ETHUSDT',strategy='sma_cross',pnl=-1,closed_at=80),
                 dict(symbol='ETHUSDT',strategy='sma_cross',pnl=0,closed_at=90),
                 dict(symbol='BTCUSDT',strategy='sr_breakout',pnl=3,closed_at=100)]
        result=analyze(records);totals=result['totals']
        self.assertEqual((totals['trades'],totals['wins'],totals['losses'],totals['breakeven']),(4,2,1,1))
        self.assertEqual(totals['realized_pnl'],4)
        self.assertEqual(totals['win_rate'],50)
        self.assertEqual(totals['profit_factor'],5)
        self.assertEqual(totals['average_hold_seconds'],60)
        eth=next(r for r in result['by_symbol'] if r['symbol']=='ETHUSDT')
        self.assertAlmostEqual(eth['win_rate'],100/3)
        self.assertEqual(eth['realized_pnl'],1)
        self.assertEqual(len(result['by_symbol_strategy']),3)
        self.assertEqual(sum(r['realized_pnl'] for r in result['by_strategy']),4)

    def test_no_history_no_losses_and_missing_strategy_are_explicit(self):
        self.assertIsNone(analyze([])['totals']['win_rate'])
        result=analyze([dict(symbol='BTCUSDT',pnl=1,closed_at=30)])
        self.assertIsNone(result['totals']['profit_factor'])
        self.assertIsNone(result['totals']['average_hold_seconds'])
        self.assertEqual(result['by_strategy'][0]['strategy'],'unknown')


from datetime import datetime
from thoth.analytics import timing_analysis


def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def trade(opened,pnl,closed=None):
    return dict(symbol='ETHUSDT',strategy='sr_breakout',opened_at=stamp(opened),closed_at=stamp(closed or opened),pnl=pnl)


class TimingAnalysisTests(unittest.TestCase):
    def test_entry_hour_weekday_and_calendar_date_use_new_york_not_utc(self):
        records=[trade('2026-10-20T03:30:00Z',2,'2026-10-20T04:30:00Z'),
            trade('2026-10-20T13:00:00Z',-3,'2026-10-20T14:00:00Z'),
            trade('2026-10-21T13:00:00Z',1,'2026-10-21T14:00:00Z')]
        timing=analyze(records)['timing']
        self.assertEqual(timing['timezone'],'America/New_York')
        self.assertEqual(timing['ranking']['hour']['best']['key'],23)
        self.assertEqual(timing['ranking']['hour']['worst']['key'],9)
        self.assertEqual(timing['ranking']['weekday']['best']['label'],'Monday')
        self.assertEqual(timing['ranking']['weekday']['worst']['label'],'Tuesday')
        self.assertEqual(timing['ranking']['date']['best']['key'],'2026-10-19')
        self.assertEqual(timing['ranking']['date']['worst']['key'],'2026-10-20')
        self.assertEqual(timing['hours'][9]['observed_days'],2)
        self.assertEqual(timing['hours'][9]['win_rate'],50)
        self.assertEqual(timing['hours'][9]['average_pnl'],-1)
        for key in ('hours','weekdays','dates'):
            self.assertEqual(sum(r['trades'] for r in timing[key]),3)
            self.assertEqual(sum(r['realized_pnl'] for r in timing[key]),0)
        exit_timing=analyze(records,'exit')['timing']
        self.assertEqual(exit_timing['ranking']['hour']['best']['key'],0)
        self.assertEqual(exit_timing['ranking']['hour']['worst']['key'],10)
        self.assertEqual(exit_timing['first_date'],'2026-10-20')

    def test_missing_entry_never_falls_back_to_exit_and_totals_stay_complete(self):
        records=[dict(symbol='ETHUSDT',pnl=1,closed_at=stamp('2026-10-20T13:00:00Z'))]
        for value in (None,True,float('nan'),float('inf'),1e20):records.append(dict(symbol='ETHUSDT',pnl=1,opened_at=value))
        result=analyze(records)
        self.assertEqual(result['totals']['trades'],6)
        self.assertEqual(result['timing']['included_trades'],0)
        self.assertEqual(result['timing']['excluded_trades'],6)
        self.assertIsNone(result['timing']['ranking']['hour']['best'])
        self.assertEqual(analyze(records,'exit')['timing']['included_trades'],1)
        with self.assertRaises(ValueError):timing_analysis(records,'unknown')

    def test_empty_unobserved_hours_negative_results_and_ties(self):
        empty=analyze([])['timing']
        self.assertEqual(len(empty['hours']),24);self.assertEqual(len(empty['weekdays']),7)
        self.assertEqual(empty['dates'],[])
        self.assertIsNone(empty['first_date'])
        records=[trade('2026-10-20T13:00:00Z',-1),trade('2026-10-20T14:00:00Z',-1)]
        ranking=analyze(records)['timing']['ranking']['hour']
        self.assertEqual(ranking['best']['realized_pnl'],-1)  # Empty $0 buckets cannot win.
        self.assertEqual(ranking['best']['tied_buckets'],2)
        self.assertEqual(ranking['worst']['tied_buckets'],2)

    def test_dst_and_distinct_day_counts(self):
        rows=[trade('2026-01-22T13:30:00Z',1),trade('2026-10-22T13:30:00Z',2),
            trade('2026-11-01T05:30:00Z',3),trade('2026-11-01T06:30:00Z',4),
            trade('2026-03-08T06:59:00Z',5),trade('2026-03-08T07:00:00Z',6)]
        timing=analyze(rows)['timing']
        self.assertEqual(timing['hours'][8]['trades'],1)
        self.assertEqual(timing['hours'][9]['trades'],1)
        self.assertEqual(timing['hours'][1]['trades'],3)
        self.assertEqual(timing['hours'][1]['observed_days'],2)
        self.assertEqual(timing['hours'][2]['trades'],0)
        self.assertEqual(timing['hours'][3]['trades'],1)
        self.assertTrue(timing['hours'][1]['limited_sample'])

    def test_filters_reach_timing_api_and_source_database_is_unchanged(self):
        import tempfile,json,sqlite3
        from pathlib import Path
        from thoth.testnet import TestnetSupervisor
        import _environment
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from thoth.app import app
        with tempfile.TemporaryDirectory() as d:
            service=TestnetSupervisor(Path(d)/'history.sqlite')
            records=[{**trade('2026-10-20T13:00:00Z',3),'symbol':'ETHUSDT'},
                {**trade('2026-10-20T14:00:00Z',-2),'symbol':'BTCUSDT'},
                {**trade('2026-10-20T15:00:00Z',4),'strategy':'sma_7_50'}]
            for i,row in enumerate(records):service._record_trade(str(i),row)
            before=service.path and Path(service.path).read_bytes()
            with patch('thoth.app.live',service):
                client=TestClient(app)
                response=client.get('/api/live/analysis?symbol=ETHUSDT&strategy=sr_breakout&time_basis=exit')
                self.assertEqual(response.status_code,200,response.text)
                data=response.json();self.assertEqual(data['totals']['trades'],1)
                self.assertEqual(data['timing']['included_trades'],1)
                self.assertEqual(data['timing']['basis'],'exit')
                self.assertEqual(data['timing']['ranking']['hour']['best']['realized_pnl'],3)
                self.assertEqual(client.get('/api/live/analysis?time_basis=madeup').status_code,422)
            self.assertEqual(Path(service.path).read_bytes(),before)
