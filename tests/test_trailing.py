import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.trailing import new_trailing,advance
from thoth.models import TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.bybit_testnet import dec
import test_testnet as fixtures
Exchange=fixtures.Exchange


class TrailingMathTests(unittest.TestCase):
    def test_long_activation_margin_vs_price_and_distance_basis(self):
        config=dict(trailing_enabled=True,trailing_activation_pct=10,trailing_distance_pct=1,trailing_distance_basis='price')
        state=new_trailing(config,100,.5,2.5,'Buy')
        self.assertEqual(dec(state['activation_price']),dec('100.5'))
        state,target=advance(state,'Buy',100,.5,2.5,100.4,98,.1)
        self.assertIsNone(target)
        state,target=advance(state,'Buy',100,.5,2.5,101,98,.1)
        self.assertEqual(dec(target),100)
        state['stop']=target
        state,target=advance(state,'Buy',100,.5,2.5,100.7,98,.1)
        self.assertEqual(dec(target),100)
        self.assertEqual(dec(state['peak']),101)
        price=new_trailing({**config,'trailing_activation_basis':'price'},100,.5,2.5,'Buy')
        self.assertEqual(dec(price['activation_price']),110)
        margin=new_trailing({**config,'trailing_distance_basis':'margin','trailing_distance_pct':10},100,.5,2.5,'Buy')
        _,target=advance(margin,'Buy',100,.5,2.5,101,98,.1)
        self.assertEqual(dec(target),dec('100.5'))

    def test_short_stop_only_tightens_and_pending_intent_is_retained(self):
        state=new_trailing(dict(trailing_enabled=True,trailing_activation_pct=10,trailing_distance_pct=1,trailing_distance_basis='price'),100,.5,2.5,'Sell')
        self.assertEqual(dec(state['activation_price']),dec('99.5'))
        state,target=advance(state,'Sell',100,.5,2.5,99,102,.1)
        self.assertEqual(dec(target),dec('99.9'))
        state['pending_stop']='98'
        _,target=advance(state,'Sell',100,.5,2.5,99.2,102,.1)
        self.assertEqual(dec(target),98)


class TrailingIntegrationTests(unittest.TestCase):
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1792152000.]
        self.exchange=Exchange(lambda:self.now[0]);self.path=Path(self.temp.name)/'live-fixture.sqlite'
        self.service=self.create()
        self.cfg=TestnetSettings(dedicated_account=True,trailing_enabled=True,trailing_activation_pct=10,trailing_distance_pct=.5)
        self.service.configure(self.cfg)

    def create(self):
        return TestnetSupervisor(self.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])

    def tearDown(self):self.temp.cleanup()

    def test_tightened_stop_is_verified_persists_and_never_resets_to_dollar_stop(self):
        self.enter();self.exchange.prices['BTCUSDT']=101;self.service.monitor()
        first=dec(self.exchange.active['BTCUSDT']['stopLoss'])
        self.assertGreater(first,98)
        self.exchange.prices['BTCUSDT']=102;self.service.monitor()
        second=dec(self.exchange.active['BTCUSDT']['stopLoss']);self.assertGreater(second,first)
        restarted=self.create();restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        self.exchange.prices['BTCUSDT']=101.8;restarted.monitor()
        self.assertEqual(dec(self.exchange.active['BTCUSDT']['stopLoss']),second)
        self.assertFalse(restarted.state['entries'])
        self.assertNotIn('BTCUSDT',restarted.state['blocked_symbols'])

    def test_retracement_requests_reduce_only_close_even_with_entries_paused(self):
        self.enter();self.exchange.prices['BTCUSDT']=102;self.service.monitor();self.service.pause()
        self.exchange.prices['BTCUSDT']=101;self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual(len(closes),1)
        self.assertEqual(self.service.state['owned']['BTCUSDT']['reason'],'Trailing stop retracement')

    def test_short_trailing_lowers_stop(self):
        self.enter(side='Short');self.exchange.prices['BTCUSDT']=99;self.service.monitor()
        first=dec(self.exchange.active['BTCUSDT']['stopLoss'])
        self.exchange.prices['BTCUSDT']=98;self.service.monitor()
        self.assertLess(dec(self.exchange.active['BTCUSDT']['stopLoss']),first)
        self.assertNotIn('BTCUSDT',self.service.state['blocked_symbols'])

    def test_ambiguous_stop_write_is_recovered_from_readback(self):
        self.enter();original=self.exchange.request
        def request(method,path,params,private=False):
            result=original(method,path,params,private)
            if path=='/v5/position/trading-stop':raise TimeoutError()
            return result
        self.exchange.prices['BTCUSDT']=101
        with patch.object(self.exchange,'request',side_effect=request):self.service.monitor()
        trailing=self.service.load()['owned']['BTCUSDT']['trailing']
        self.assertIsNone(trailing['pending_stop'])
        self.assertEqual(trailing['stop'],self.exchange.active['BTCUSDT']['stopLoss'])
        self.assertTrue(self.service.state['entries'])

    def test_write_and_read_timeout_leave_durable_intent_then_reconcile(self):
        self.enter();original=self.exchange.request;written=[False]
        def request(method,path,params,private=False):
            if path=='/v5/position/list' and written[0]:raise TimeoutError()
            result=original(method,path,params,private)
            if path=='/v5/position/trading-stop':written[0]=True;raise TimeoutError()
            return result
        self.exchange.prices['BTCUSDT']=101
        with patch.object(self.exchange,'request',side_effect=request):
            with self.assertRaises(TimeoutError):self.service.monitor()
        self.assertIsNotNone(self.service.load()['owned']['BTCUSDT']['trailing']['pending_stop'])
        restarted=self.create();restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        self.assertIsNone(restarted.state['owned']['BTCUSDT']['trailing']['pending_stop'])
        self.assertNotIn('BTCUSDT',restarted.state['blocked_symbols'])

    def test_rejected_trailing_update_closes_only_affected_symbol(self):
        self.enter();self.exchange.fail_stop=True;self.exchange.prices['BTCUSDT']=101
        self.service.monitor()
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.assertIn('BTCUSDT',self.service.state['blocked_symbols'])
        self.assertTrue(self.service.state['entries'])

    def test_existing_trade_keeps_frozen_settings(self):
        self.enter();self.service.pause()
        self.service.configure(self.cfg.model_copy(update={'trailing_enabled':False,'trailing_distance_pct':5}))
        self.exchange.prices['BTCUSDT']=101;self.service.monitor()
        self.assertTrue(self.service.state['owned']['BTCUSDT']['trailing']['enabled'])
        self.assertEqual(self.service.state['owned']['BTCUSDT']['trailing']['distance_pct'],.5)
        self.assertGreater(dec(self.exchange.active['BTCUSDT']['stopLoss']),98)

    def test_position_flat_during_stop_readback_settles_without_extra_close(self):
        self.enter();original=self.exchange.request
        def request(method,path,params,private=False):
            result=original(method,path,params,private)
            if path=='/v5/position/trading-stop':
                position=self.exchange.active.pop('BTCUSDT')
                pnl=(101-float(position['avgPrice']))*float(position['size'])
                self.exchange.balance+=pnl
                self.exchange.closed.append(dict(orderId='fixture-exchange-stop',symbol='BTCUSDT',closedSize=position['size'],closedPnl=str(pnl),execType='Trade',createdTime=str(int(self.now[0]*1000))))
            return result
        self.exchange.prices['BTCUSDT']=101
        with patch.object(self.exchange,'request',side_effect=request):self.service.monitor()
        self.service.monitor()
        self.assertEqual(self.service.state['owned'],{})
        self.assertEqual(self.service.history()['total'],1)
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_ownership_change_during_trailing_write_never_submits_close(self):
        self.enter();original=self.exchange.request
        def request(method,path,params,private=False):
            result=original(method,path,params,private)
            if path=='/v5/position/trading-stop':self.exchange.active['BTCUSDT']['side']='Sell'
            return result
        self.exchange.prices['BTCUSDT']=101
        with patch.object(self.exchange,'request',side_effect=request):self.service.monitor()
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'safety_lock')
