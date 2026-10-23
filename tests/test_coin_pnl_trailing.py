"""Symbol-isolated trailing integration; no exchange/network access."""
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
import test_testnet as fixtures
from thoth.models import TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.bybit_testnet import dec

class CoinTrailingTests(unittest.TestCase):
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1792152000.]
        self.exchange=fixtures.Exchange(lambda:self.now[0]);self.path=Path(self.temp.name)/'coin.sqlite'
        self.service=self.create()
        self.cfg=TestnetSettings(dedicated_account=True,coin_pnl_trailing_enabled=True,
            coin_pnl_trailing_activation_dollars=.5,coin_pnl_trailing_drop_dollars=.025,daily_loss_pct=50,sma_exit_enabled=False)
        self.service.configure(self.cfg)
    def create(self):
        return TestnetSupervisor(self.path,enabled=True,password_configured=True,
            client_factory=lambda *a,**kw:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])
    def tearDown(self):self.temp.cleanup()

    def test_coin_only_close_retains_entries_and_history_reason(self):
        self.enter();self.enter('ETHUSDT')
        self.exchange.prices.update(BTCUSDT=101,ETHUSDT=100)
        self.service.monitor()
        state=self.service.load()['owned']['BTCUSDT']['coin_pnl_trailing']
        self.assertTrue(state['active']);self.assertEqual(dec(state['threshold']),dec('.475'))
        self.exchange.prices['BTCUSDT']=100.96;self.service.monitor()
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.exchange.prices['BTCUSDT']=100.94;self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual({o['symbol'] for o in closes},{'BTCUSDT'})
        self.assertTrue(self.service.state['entries']);self.assertFalse(self.service.snapshot()['closing'])
        self.fill_closes()
        self.assertIn('ETHUSDT',self.service.state['owned'])
        self.assertEqual(self.service.history()['items'][0]['reason'],'Per-coin Trailing Stop')
        self.signal(self.now[0]-60,None,'SOLUSDT');self.now[0]+=60
        self.signal(self.now[0]-60,'Long','SOLUSDT')
        self.assertTrue(any(o['symbol']=='SOLUSDT' and not o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_peak_and_close_obligation_survive_restart_and_disable(self):
        self.enter(side='Short');self.exchange.prices['BTCUSDT']=98;self.service.monitor()
        self.assertEqual(dec(self.service.load()['owned']['BTCUSDT']['coin_pnl_trailing']['peak']),dec('1'))
        self.service.pause();self.service.configure(self.cfg.model_copy(update={'max_positions':4}))
        self.assertEqual(dec(self.service.load()['owned']['BTCUSDT']['coin_pnl_trailing']['peak']),dec('1'))
        self.exchange.prices['BTCUSDT']=98.06
        with patch.object(self.service,'_close',side_effect=RuntimeError('timeout')):
            with self.assertRaises(RuntimeError):self.service.monitor()
        self.assertEqual(self.service.load()['owned']['BTCUSDT']['signal_exit'],'Per-coin Trailing Stop')
        self.service=self.create();self.service.configure(self.cfg.model_copy(update={'coin_pnl_trailing_enabled':False}))
        self.service.activate('ENABLE TESTNET ORDERS',recovery=True);self.service.monitor()
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_combined_can_coexist_and_still_closes_all(self):
        self.cfg=self.cfg.model_copy(update={'pnl_trailing_enabled':True,'pnl_trailing_activation_dollars':.5,'pnl_trailing_drop_dollars':.025})
        self.service.configure(self.cfg)
        self.enter();self.enter('ETHUSDT')
        self.exchange.prices.update(BTCUSDT=101,ETHUSDT=101);self.service.monitor()
        self.exchange.prices.update(BTCUSDT=100.94,ETHUSDT=101);self.service.monitor()
        self.assertEqual({o['symbol'] for o in self.exchange.orders.values() if o['reduceOnly']},{'BTCUSDT','ETHUSDT'})
        self.assertTrue(self.service.state['entries'])

    def test_timeframe_changes_require_flat_reconciled_positions(self):
        self.enter();self.service.pause()
        with self.assertRaisesRegex(ValueError,'strategy or timeframe'):
            self.service.configure(self.cfg.model_copy(update={'timeframe':'5'}))

    def test_dollar_settings_reject_nonpositive_and_nonfinite_values(self):
        for key in ('coin_pnl_trailing_activation_dollars','coin_pnl_trailing_drop_dollars'):
            for value in (0,-1,float('nan'),float('inf')):
                with self.assertRaises(ValueError):TestnetSettings(**{key:value})

    def test_coin_exit_does_not_wait_for_wallet_read(self):
        self.enter();self.exchange.prices['BTCUSDT']=101;self.service.monitor()
        self.exchange.prices['BTCUSDT']=100.94
        with patch.object(self.service,'wallet',side_effect=RuntimeError('wallet timeout')):
            with self.assertRaises(RuntimeError):self.service.monitor()
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.assertTrue(self.service.load()['owned']['BTCUSDT']['coin_pnl_trailing']['triggered'])
