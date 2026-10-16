import hashlib
import hmac
import io
import json
import unittest
from unittest.mock import patch
from thoth.bybit_testnet import TestnetClient, BybitError, AmbiguousOrder, order_payload, stop_price

INSTRUMENT={'symbol':'BTCUSDT','status':'Trading','contractType':'LinearPerpetual','settleCoin':'USDT',
            'lotSizeFilter':{'qtyStep':'.001','minOrderQty':'.001','minNotionalValue':'5','maxMktOrderQty':'100'},
            'priceFilter':{'tickSize':'.1'}}


class TestnetTests(unittest.TestCase):
    def test_rounding_does_not_enlarge_notional(self):
        p=order_payload('BTCUSDT','Long',50,30000,INSTRUMENT,'signal')
        self.assertEqual(p['qty'],'0.001')
        self.assertEqual(p['side'],'Buy')
        self.assertLessEqual(float(p['qty'])*30000,50)
        self.assertLessEqual(len(p['orderLinkId']),36)
        with self.assertRaises(ValueError):order_payload('BTCUSDT','Long',.5,30000,INSTRUMENT,'tiny')

    def test_dollar_stop_rounds_towards_entry(self):
        self.assertEqual(stop_price('Long',100,.5,1,.1),'98')
        self.assertEqual(stop_price('Short',100,.5,1,.1),'102')
        self.assertEqual(stop_price('Long',100,3,1,.1),'99.7')
        with self.assertRaises(ValueError):stop_price('Long',100,100,1,.1)

    def test_auth_signature_and_testnet_only_destination(self):
        seen=[]
        def transport(req,timeout):
            seen.append(req)
            return io.BytesIO(json.dumps({'retCode':0,'result':{'list':[]}}).encode())
        c=TestnetClient('fixture-key','fixture-secret',transport=transport)
        with patch('thoth.bybit_testnet.time.time',return_value=100):
            c.reconcile('BTCUSDT','fixture-link')
        req=seen[0]
        self.assertTrue(req.full_url.startswith('https://api-testnet.bybit.com/v5/'))
        query=req.full_url.split('?')[1]
        expected=hmac.new(b'fixture-secret',('100000fixture-key5000'+query).encode(),hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header('X-bapi-sign'),expected)

    def test_submission_is_disabled_by_default(self):
        with self.assertRaises(BybitError):TestnetClient().submit({})

    def test_timeout_is_not_blindly_retried(self):
        c=TestnetClient('key','secret',armed=True)
        payload=order_payload('BTCUSDT','Long',50,30000,INSTRUMENT,'fixture')
        with patch.object(c,'reconcile',return_value=None),patch.object(c,'request',side_effect=TimeoutError) as request:
            with self.assertRaises(AmbiguousOrder):c.submit(payload)
            self.assertEqual(request.call_count,1)

    def test_close_is_reduce_only_and_acceptance_is_not_fill(self):
        c=TestnetClient('key','secret',armed=True)
        close=c.close_payload('BTCUSDT','Long','.001','close-fixture')
        self.assertTrue(close['reduceOnly'])
        self.assertEqual(close['side'],'Sell')
        with patch.object(c,'reconcile',return_value=None),patch.object(c,'request',return_value={'orderId':'fixture'}):
            self.assertEqual(c.submit(close)['state'],'accepted_not_filled')
