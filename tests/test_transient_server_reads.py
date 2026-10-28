"""Explicit GET server-error retries; no live credentials or exchange calls."""
import io
import json
import unittest
from unittest.mock import patch
from thoth.bybit_testnet import TestnetClient,BybitError
import test_testnet as fixtures


class ServerReadRetryTests(unittest.TestCase):
    def test_wallet_read_rebuilt_and_signed_after_server_error(self):
        seen=[]
        def transport(request,timeout):
            seen.append(request)
            body={'retCode':10016,'retMsg':'fixture server error'} if len(seen)==1 else {'retCode':0,'result':{'list':[{'totalEquity':'42'}]}}
            return io.BytesIO(json.dumps(body).encode())
        client=TestnetClient('fixture','fixture',transport=transport)
        with patch('thoth.bybit_testnet.BYBIT_PACER'),patch('thoth.bybit_testnet.time.sleep') as wait,patch('thoth.bybit_testnet.time.time',side_effect=[100.,101.]):
            result=client.request('GET','/v5/account/wallet-balance',{'accountType':'UNIFIED'},private=True)
        self.assertEqual(result['list'][0]['totalEquity'],'42');self.assertEqual(len(seen),2)
        self.assertNotEqual(seen[0].get_header('X-bapi-timestamp'),seen[1].get_header('X-bapi-timestamp'))
        self.assertNotEqual(seen[0].get_header('X-bapi-sign'),seen[1].get_header('X-bapi-sign'))
        wait.assert_called_once_with(.25)

    def test_exhaustion_is_bounded_and_writes_and_transport_errors_are_not_retried(self):
        client=TestnetClient('fixture','fixture',armed=True)
        for method,path,code,expected in [('GET','/v5/account/wallet-balance',10016,3),('POST','/v5/order/create',10016,1),('POST','/v5/position/trading-stop',10016,1),('GET','/v5/account/wallet-balance',10004,1)]:
            with self.subTest(method=method,path=path,code=code),patch.object(client,'_request_once',side_effect=BybitError('fixture',code)) as read,patch('thoth.bybit_testnet.time.sleep') as wait:
                with self.assertRaises(BybitError):client.request(method,path,{},private=True)
                self.assertEqual(read.call_count,expected)
                self.assertEqual([c.args[0] for c in wait.call_args_list],[.25,.5] if expected==3 else [])
        with patch.object(client,'_request_once',side_effect=TimeoutError) as read:
            with self.assertRaises(TimeoutError):client.request('GET','/v5/account/wallet-balance',{},private=True)
            self.assertEqual(read.call_count,1)


class MonitorServerReadTests(unittest.TestCase):
    setUp=fixtures.TestnetIntegration.setUp
    tearDown=fixtures.TestnetIntegration.tearDown
    create=fixtures.TestnetIntegration.create
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter

    def run_monitor(self,failures):
        original=self.exchange.request;wallet_calls=[]
        def once(method,path,params,private=False):
            if path=='/v5/account/wallet-balance':
                wallet_calls.append(path)
                if len(wallet_calls)<=failures:
                    error=BybitError('fixture',10016);error.thoth_endpoint=path;error.thoth_method=method;raise error
            return original(method,path,params,private)
        def request(method,path,params,private=False):return TestnetClient.request(self.exchange,method,path,params,private)
        with patch.object(self.exchange,'request',side_effect=request),patch.object(self.exchange,'_request_once',side_effect=once),patch('thoth.bybit_testnet.time.sleep'):
            try:self.service.monitor()
            except Exception as error:self.service._monitor_failure(error)
        return len(wallet_calls)

    def test_transient_wallet_rejection_recovers_before_lock(self):
        self.enter();self.assertEqual(self.run_monitor(1),2)
        self.assertTrue(self.service.state['entries']);self.assertEqual(self.service.state['status'],'running')
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_repeated_wallet_errors_lock_and_later_success_does_not_auto_enable(self):
        self.enter();self.assertEqual(self.run_monitor(3),3)
        self.assertFalse(self.service.state['entries']);self.assertEqual(self.service.state['status'],'safety_lock')
        self.assertEqual(float(self.exchange.active['BTCUSDT']['stopLoss']),98)
        self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'safety_lock')
