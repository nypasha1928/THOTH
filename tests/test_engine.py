import tempfile
import unittest
from pathlib import Path

from thoth.engine import Engine
from thoth.models import Settings, PaperOrder


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / 'test.sqlite')
        self.engine.control('start')

    def tearDown(self):
        self.tmp.cleanup()

    def enter(self, symbol='BTCUSDT', side='Long', signal='a'):
        return self.engine.order(PaperOrder(symbol=symbol, side=side, price=100, signal_id=signal))

    def test_dollar_stop(self):
        self.enter()
        s = self.engine.marks({'BTCUSDT': 97})
        self.assertEqual(s['positions'], [])
        self.assertAlmostEqual(s['balance'], 248.5)
        self.assertEqual(s['trades'][0]['reason'], 'Individual dollar stop')

    def test_daily_stop_closes_winners_and_blocks_restart(self):
        self.engine.configure(Settings(daily_loss_pct=.1, stop_dollars=20))
        self.enter()
        self.enter('ETHUSDT', signal='b')
        s = self.engine.marks({'BTCUSDT': 98, 'ETHUSDT': 101})
        self.assertEqual(s['status'], 'daily_stop')
        self.assertEqual(len(s['trades']), 2)
        self.assertEqual(s['positions'], [])
        self.assertTrue(any(t['pnl'] > 0 for t in s['trades']))
        with self.assertRaises(ValueError):
            self.engine.control('start')
        self.assertEqual(self.engine.control('pause')['status'], 'daily_stop')

    def test_consecutive_losses_pause(self):
        for i in range(3):
            self.enter(signal=str(i))
            self.engine.marks({'BTCUSDT': 97})
        s = self.engine.snapshot()
        self.assertEqual(s['status'], 'cooldown')
        self.assertIsNotNone(s['resume_at'])
        with self.assertRaises(ValueError):
            self.enter(signal='blocked')

    def test_duplicate_signal_and_persistence(self):
        self.enter()
        self.assertTrue(self.enter()['duplicate'])
        restored = Engine(self.engine.path).snapshot()
        self.assertEqual(len(restored['positions']), 1)
        self.assertEqual(restored['positions'][0]['margin'], 2.5)

    def test_reverse_closes_before_opening(self):
        self.enter()
        s = self.enter(side='Short', signal='b')
        self.assertEqual(len(s['trades']), 1)
        self.assertEqual(s['positions'][0]['side'], 'Short')

    def test_capacity(self):
        self.engine.configure(Settings(max_positions=1))
        self.enter()
        with self.assertRaises(ValueError):
            self.enter('ETHUSDT', signal='b')

    def test_settings_preserve_position_stop(self):
        self.enter()
        self.engine.configure(Settings(stop_dollars=10))
        self.assertEqual(self.engine.snapshot()['positions'][0]['stop_dollars'], 1)

    def test_profitable_trade_resets_streak(self):
        self.enter()
        self.engine.marks({'BTCUSDT': 97})
        self.enter(signal='b')
        self.engine.marks({'BTCUSDT': 102})
        s = self.engine.order(PaperOrder(symbol='BTCUSDT', side='Short', price=102, signal_id='c'))
        self.assertEqual(s['streak'], 0)

    def test_daily_stop_releases_next_utc_day(self):
        from unittest.mock import patch
        from datetime import timedelta
        from thoth.engine import utcnow
        self.engine.configure(Settings(daily_loss_pct=.1))
        self.enter()
        self.engine.marks({'BTCUSDT': 98})
        with patch('thoth.engine.utcnow', return_value=utcnow()+timedelta(days=1)):
            result = self.engine.snapshot()
            self.assertEqual(result['status'], 'running')
            self.assertEqual(result['daily_loss_pct'], 0)

    def test_cooldown_expires_and_resets_streak(self):
        from unittest.mock import patch
        from datetime import timedelta
        from thoth.engine import utcnow
        self.engine.configure(Settings(consecutive_losses=1))
        self.enter()
        self.engine.marks({'BTCUSDT': 97})
        with patch('thoth.engine.utcnow', return_value=utcnow()+timedelta(hours=2)):
            result = self.engine.snapshot()
            self.assertEqual(result['status'], 'running')
            self.assertEqual(result['streak'], 0)

    def test_overall_position_stop_ignores_winner_gains(self):
        self.engine.configure(Settings(daily_loss_pct=50,stop_dollars=10,portfolio_loss_enabled=True,portfolio_loss_pct=20))
        self.enter();self.enter('ETHUSDT',signal='b')
        result=self.engine.marks({'BTCUSDT':99,'ETHUSDT':110})
        self.assertEqual(result['status'],'portfolio_stop')
        self.assertEqual(result['positions'],[])
        self.assertTrue(any(t['pnl']>0 for t in result['trades']))
        self.assertEqual(self.engine.control('start')['status'],'running')

    def test_invalid_settings(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Settings(daily_loss_pct=0)
        with self.assertRaises(ValidationError):
            Settings(position_value=float('nan'))


if __name__ == '__main__':
    unittest.main()
