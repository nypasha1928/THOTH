"""Simulated dollar take-profit tests, no network or real orders."""
import unittest
from unittest.mock import patch
from thoth.bybit_testnet import take_profit_price,BybitError
from thoth.models import TestnetSettings
import test_testnet as fixtures


class ProfitPriceTests(unittest.TestCase):
    def test_gross_dollar_target_long_short_and_tick_rounding(self):
        self.assertEqual(take_profit_price('Long',100,.5,1,.1),'102')
        self.assertEqual(take_profit_price('Short',100,.5,1,.1),'98')
        self.assertEqual(take_profit_price('Long',100,.3,1,.1),'103.4')
        self.assertEqual(take_profit_price('Short',100,.3,1,.1),'96.6')
        for args in [('Short',100,.5,50,.1),('Long',100,.5,.001,.1),('Long',100,0,1,.1),('Long',float('nan'),1,1,.1)]:
            with self.assertRaises(ValueError):take_profit_price(*args)


class TakeProfitTests(unittest.TestCase):
    setUp=fixtures.TestnetIntegration.setUp
    tearDown=fixtures.TestnetIntegration.tearDown
    create=fixtures.TestnetIntegration.create
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def enable(self,**kwargs):
        self.cfg=TestnetSettings(dedicated_account=True,take_profit_enabled=True,**kwargs)
        self.service.configure(self.cfg)

    def test_default_disabled_attaches_no_tp(self):
        self.enter()
        self.assertNotIn('takeProfit',next(iter(self.exchange.orders.values())))
        self.assertFalse(self.service.state['owned']['BTCUSDT']['take_profit']['enabled'])

    def test_both_entry_directions_attach_exchange_target_and_verify(self):
        self.enable();self.enter()
        owner=self.service.state['owned']['BTCUSDT']
        self.assertEqual(self.exchange.active['BTCUSDT']['takeProfit'],'102')
        self.assertTrue(owner['take_profit']['verified'])
        self.assertEqual(self.service.snapshot()['positions'][0]['take_profit']['exchange_price'],'102')
        # A second coin independently receives the short target.
        self.signal(self.now[0]-60,None,'ETHUSDT');self.now[0]+=60;self.signal(self.now[0]-60,'Short','ETHUSDT')
        link=next(o['orderLinkId'] for o in self.exchange.orders.values() if o['symbol']=='ETHUSDT')
        self.exchange.fill(link);self.service.monitor()
        self.assertEqual(self.exchange.active['ETHUSDT']['takeProfit'],'98')
        self.assertTrue(self.service.state['owned']['ETHUSDT']['take_profit']['verified'])

    def test_actual_fill_adjusts_tp_and_acknowledgement_is_not_verification(self):
        self.enable();self.activate();self.signal(self.now[0]-60,None)
        self.now[0]+=60;self.signal(self.now[0]-60,'Long')
        link=next(iter(self.exchange.orders));self.exchange.prices['BTCUSDT']=101
        self.exchange.fill(link);self.service.monitor()
        self.assertEqual(self.exchange.active['BTCUSDT']['takeProfit'],'103')
        self.assertFalse(self.service.state['owned']['BTCUSDT']['take_profit']['verified'])
        self.service.monitor()
        self.assertTrue(self.service.state['owned']['BTCUSDT']['take_profit']['verified'])
        self.assertEqual(self.exchange.active['BTCUSDT']['stopLoss'],'99')

    def test_changed_new_trade_settings_and_restart_keep_original_target(self):
        self.enable(take_profit_dollars=2);self.enter();self.service.pause()
        self.cfg=self.cfg.model_copy(update={'take_profit_enabled':False,'take_profit_dollars':5})
        self.service.configure(self.cfg)
        self.service=self.create();self.service.activate(self.service.confirmation,recovery=True)
        state=self.service.state['owned']['BTCUSDT']['take_profit']
        self.assertTrue(state['enabled']);self.assertEqual(state['dollars'],2)
        self.assertEqual(self.exchange.active['BTCUSDT']['takeProfit'],'104')
        self.assertFalse(self.service.state['entries'])

    def test_tp_failure_is_coin_local_and_keeps_stop_and_entries(self):
        self.enable();self.enter();self.exchange.active['BTCUSDT']['takeProfit']='0'
        original=self.exchange.request
        def request(method,path,params,private=False):
            if method=='POST' and 'takeProfit' in params:raise BybitError('private fixture',10001)
            return original(method,path,params,private)
        self.exchange.request=request;self.service.monitor()
        owner=self.service.state['owned']['BTCUSDT']
        self.assertFalse(owner['take_profit']['verified']);self.assertIsNotNone(owner['take_profit']['error'])
        self.assertIsNone(owner['close_link']);self.assertEqual(float(self.exchange.active['BTCUSDT']['stopLoss']),98)
        self.assertTrue(self.service.state['entries']);self.assertEqual(self.service.state['status'],'running')
        self.assertNotIn('private fixture',str(owner['take_profit']))
        self.now[0]+=11;self.exchange.request=original;self.service.monitor();self.service.monitor()
        self.assertTrue(self.service.state['owned']['BTCUSDT']['take_profit']['verified'])

    def test_gross_profit_fallback_closes_reduce_only_and_records_reason(self):
        self.enable();self.enter();self.exchange.prices['BTCUSDT']=103
        self.service.monitor()
        owner=self.service.state['owned']['BTCUSDT']
        self.assertIn('Take profit',owner['reason'])
        self.assertTrue(self.exchange.orders[owner['close_link']]['reduceOnly'])
        self.fill_closes();self.service.monitor()
        self.assertIn('Take profit',self.service.history()['items'][0]['reason'])

    def test_tp_update_does_not_send_stop_loss_or_remove_it(self):
        self.enable();self.enter();self.exchange.active['BTCUSDT']['takeProfit']='0'
        self.service.monitor()
        update=next(params for method,path,params in reversed(self.exchange.calls) if path=='/v5/position/trading-stop')
        self.assertNotIn('stopLoss',update);self.assertEqual(update['takeProfit'],'102')
        self.assertEqual(float(self.exchange.active['BTCUSDT']['stopLoss']),98)
