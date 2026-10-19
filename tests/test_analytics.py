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
