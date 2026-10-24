import json,unittest
from thoth.signal_sources import sources_from_decision,source_label,trade_records
from thoth.analytics import analyze
import sqlite3

class SignalSourcesTests(unittest.TestCase):
    def test_only_actual_contributing_sources_and_no_pnl_double_count(self):
        d=dict(signal='Long',diagnostic=dict(sr_signal='Long',rsi_signal=None,sr_levels=['short_resistance_minor'],rsi_events=['ml_short_down']))
        self.assertEqual(sources_from_decision(d),['sr:short_resistance_minor'])
        records=[dict(symbol='BTCUSDT',pnl=2,entry_sources=['rsi:ml_long_up','sr:long_resistance_major']),dict(symbol='ETHUSDT',pnl=-1)]
        result=analyze(records)
        self.assertEqual(sum(row['trades'] for row in result['by_source']),2)
        self.assertEqual(sum(row['realized_pnl'] for row in result['by_source']),1)
        self.assertIn('Source not recorded',[row['source'] for row in result['by_source']])
        self.assertEqual(source_label(['sr:short_support_minor']),'S/R short-term minor support')

    def test_source_filter_and_best_worst_time_groups_use_whole_realized_trade(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        def stamp(date):return datetime.fromisoformat(date).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
        sources=['sr:short_support_minor']
        records=[dict(symbol='BTCUSDT',pnl=2,entry_sources=sources,opened_at=stamp('2026-10-23T09:00:00')),
            dict(symbol='ETHUSDT',pnl=-1,entry_sources=sources,opened_at=stamp('2026-10-24T10:00:00')),
            dict(symbol='ETHUSDT',pnl=100,entry_sources=['rsi:ml_long_up'],opened_at=stamp('2026-10-24T10:00:00')),
            dict(symbol='BTCUSDT',pnl=-.5,entry_sources=sources)]
        result=analyze(records,source='sr:short_support_minor')
        self.assertEqual(result['totals']['trades'],3)
        self.assertEqual(result['totals']['realized_pnl'],.5)
        row=result['by_source_timing'][0]
        self.assertEqual(row['hours']['best']['key'],9)
        self.assertEqual(row['hours']['worst']['key'],10)
        self.assertEqual(row['weekdays']['best']['label'],'Friday')
        self.assertEqual(row['weekdays']['worst']['label'],'Saturday')
        self.assertEqual(row['included_trades'],2)
        self.assertEqual(row['observed_days'],2)
        self.assertTrue(row['hours']['best']['limited_sample'])
        self.assertEqual(len(result['source_options']),2)
        self.assertEqual(analyze(records,time_basis='exit',source='sr:short_support_minor')['by_source_timing'][0]['hours']['best'],None)

    def test_old_sources_recovered_only_by_exact_execution_link(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE signal_decisions(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO signal_decisions VALUES(1,?)',(json.dumps(dict(alert_key='exact',signal='Short',diagnostic=dict(sr_signal='Short',sr_levels=['long_support_major']))),))
            payload=json.dumps(dict(symbol='BTCUSDT',pnl=-1))
            records=trade_records(db,[('exact',payload),('other-trade-same-coin',payload)])
        self.assertEqual(records[0]['entry_sources'],['sr:long_support_major'])
        self.assertEqual(records[1]['entry_sources'],[])
        self.assertEqual(records[1]['entry_source_label'],'Source not recorded')
