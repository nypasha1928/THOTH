"""Fixed-floor math and supervised coin-only exits; synthetic exchange only."""
import unittest
from unittest.mock import patch
import test_coin_pnl_trailing as fixtures
from thoth.breakeven import advance
from thoth.models import TestnetSettings

class Rules(unittest.TestCase):
    def test_fixed_floor_inclusive_and_no_trailing(self):
        state=advance({},'.19',True,'.2','.1',1);self.assertFalse(state.get('active'))
        state=advance(state,'.2',True,'.2','.1',2);self.assertTrue(state['active'])
        state=advance(state,'2',True,'.2','.1',3);self.assertEqual(state['action'],'0.1')
        state=advance(state,'.11',True,'.2','.1',4);self.assertFalse(state.get('triggered'))
        state=advance(state,'.1',True,'.2','.1',5);self.assertTrue(state['triggered'])
        self.assertTrue(advance(state,'1',False,'.2','.1',6)['triggered'])
    def test_invalid_config(self):
        for args in ({'coin_breakeven_trigger_dollars':0},{'coin_breakeven_action_dollars':-.1},{'coin_breakeven_action_dollars':.2},{'coin_breakeven_trigger_dollars':float('nan')}):
            with self.assertRaises(ValueError):TestnetSettings(**args)

class Integration(unittest.TestCase):
    setUp=fixtures.CoinTrailingTests.setUp
    create=fixtures.CoinTrailingTests.create
    tearDown=fixtures.CoinTrailingTests.tearDown
    activate=fixtures.CoinTrailingTests.activate
    signal=fixtures.CoinTrailingTests.signal
    enter=fixtures.CoinTrailingTests.enter
    fill_closes=fixtures.CoinTrailingTests.fill_closes

    def test_long_and_short_isolated_restart_and_history(self):
        self.cfg=self.cfg.model_copy(update={'coin_pnl_trailing_enabled':False,'coin_breakeven_enabled':True})
        self.service.configure(self.cfg);self.enter();self.enter('ETHUSDT',side='Short')
        self.exchange.prices.update(BTCUSDT=101,ETHUSDT=99);self.service.monitor()
        self.assertTrue(self.service.load()['owned']['BTCUSDT']['coin_breakeven']['active'])
        self.service=self.create();self.service.activate('ENABLE TESTNET ORDERS',recovery=True);self.activate()
        self.exchange.prices.update(BTCUSDT=102,ETHUSDT=98);self.service.monitor()
        self.exchange.prices.update(BTCUSDT=100.19,ETHUSDT=99);self.service.monitor()
        self.assertEqual({o['symbol'] for o in self.exchange.orders.values() if o['reduceOnly']},{'BTCUSDT'})
        self.fill_closes();trade=self.service.history()['items'][0]
        self.assertEqual(trade['reason'],'Per-coin Breakeven');self.assertTrue(trade['coin_breakeven']['triggered'])
        self.assertTrue(self.service.state['entries']);self.assertIn('ETHUSDT',self.service.state['owned'])
        self.exchange.prices['ETHUSDT']=99.81;self.service.monitor()
        self.assertEqual({o['symbol'] for o in self.exchange.orders.values() if o['reduceOnly']},{'BTCUSDT','ETHUSDT'})

    def test_trigger_survives_failure_disable_and_wallet_failure(self):
        self.cfg=self.cfg.model_copy(update={'coin_pnl_trailing_enabled':False,'coin_breakeven_enabled':True})
        self.service.configure(self.cfg);self.enter();self.exchange.prices['BTCUSDT']=101;self.service.monitor()
        self.exchange.prices['BTCUSDT']=100.19
        with patch.object(self.service,'_close',side_effect=RuntimeError('fixture timeout')):
            with self.assertRaises(RuntimeError):self.service.monitor()
        self.service.pause();self.service.configure(self.cfg.model_copy(update={'coin_breakeven_enabled':False}))
        self.assertTrue(self.service.load()['owned']['BTCUSDT']['coin_breakeven']['triggered'])
        with patch.object(self.service,'wallet',side_effect=RuntimeError('wallet timeout')):
            with self.assertRaises(RuntimeError):self.service.monitor()
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_disabled_default_and_settings_reset(self):
        self.enter();self.exchange.prices['BTCUSDT']=101;self.service.monitor()
        self.assertFalse(self.service.load()['owned']['BTCUSDT']['coin_breakeven'].get('active'))
        self.service.pause();self.cfg=self.cfg.model_copy(update={'coin_breakeven_enabled':True})
        self.service.configure(self.cfg);self.service.monitor()
        self.assertTrue(self.service.load()['owned']['BTCUSDT']['coin_breakeven']['active'])
        self.service.configure(self.cfg.model_copy(update={'coin_breakeven_trigger_dollars':1}))
        self.service.monitor()
        self.assertFalse(self.service.load()['owned']['BTCUSDT']['coin_breakeven'].get('active'))
