import unittest
from thoth.request_pacing import RequestPacer


class RequestPacingTests(unittest.TestCase):
    def test_shared_spacing_and_backoff_without_network_or_real_sleep(self):
        now=[10.];waits=[]
        def sleep(delay):
            waits.append(delay);now[0]+=delay
        pacer=RequestPacer(clock=lambda:now[0],sleep=sleep)
        pacer.wait();pacer.wait()
        self.assertAlmostEqual(waits[0],.2)
        pacer.backoff(2);pacer.wait()
        self.assertAlmostEqual(waits[1],2)
        now[0]+=3;pacer.wait()
        self.assertEqual(len(waits),2)

    def test_rate_limit_is_reported_without_replaying_write(self):
        import io,json
        from unittest.mock import patch
        from thoth.bybit_testnet import TestnetClient,BybitError
        calls=[]
        def transport(request,timeout):
            calls.append(request)
            return io.BytesIO(json.dumps({'retCode':10006,'retMsg':'fixture limit'}).encode())
        with patch('thoth.bybit_testnet.BYBIT_PACER') as pacer:
            client=TestnetClient('fixture','fixture',transport=transport)
            with self.assertRaises(BybitError) as error:
                client.request('POST','/v5/order/create',{},private=True)
            self.assertEqual(error.exception.code,10006)
            self.assertEqual(len(calls),1)
            pacer.wait.assert_called_once()
            pacer.backoff.assert_called_once_with(2)

    def test_endpoint_spacing_and_global_backoff_share_schedule(self):
        now=[0.];waits=[]
        def sleep(delay):waits.append(delay);now[0]+=delay
        pacer=RequestPacer(clock=lambda:now[0],sleep=sleep)
        pacer.wait('positions',.5);pacer.wait('positions',.5)
        self.assertAlmostEqual(waits[0],.5)
        pacer.wait('wallet',0);self.assertAlmostEqual(waits[1],.2)
        pacer.backoff(2);pacer.wait('positions',.5)
        self.assertAlmostEqual(waits[2],2)

    def test_read_retries_rate_rejection_with_server_reset_and_fresh_signature(self):
        import io,json
        from unittest.mock import patch
        from thoth.bybit_testnet import TestnetClient
        calls=[]
        def transport(request,timeout):
            calls.append(request)
            body={'retCode':10006,'time':100000,'retMsg':'fixture limit'} if len(calls)==1 else {'retCode':0,'result':{'list':[]}}
            response=io.BytesIO(json.dumps(body).encode());response.headers={'X-Bapi-Limit-Reset-Timestamp':'103000'}
            return response
        with patch('thoth.bybit_testnet.BYBIT_PACER') as pacer,patch('thoth.bybit_testnet.time.time',side_effect=[100,100,104]):
            client=TestnetClient('fixture','fixture',transport=transport)
            self.assertEqual(client.request('GET','/v5/position/list',{},private=True),{'list':[]})
            self.assertEqual(len(calls),2)
            pacer.backoff.assert_called_once_with(3.1)
            self.assertNotEqual(calls[0].get_header('X-bapi-timestamp'),calls[1].get_header('X-bapi-timestamp'))

    def test_reads_have_bounded_retries_but_other_errors_are_not_retried(self):
        import io,json
        from unittest.mock import patch
        from thoth.bybit_testnet import TestnetClient,BybitError
        for code,expected in [(10006,3),(10003,1),(10004,1)]:
            calls=[]
            def transport(request,timeout):
                calls.append(request);return io.BytesIO(json.dumps({'retCode':code}).encode())
            with patch('thoth.bybit_testnet.BYBIT_PACER'):
                with self.assertRaises(BybitError):TestnetClient('fixture','fixture',transport=transport).request('GET','/v5/position/list',{},private=True)
            self.assertEqual(len(calls),expected)

    def test_rate_quota_diagnostics_retain_only_numeric_allowlisted_fields(self):
        import io,json
        from unittest.mock import patch
        from thoth.bybit_testnet import TestnetClient,BybitError
        from thoth.diagnostics import failure_details
        def transport(request,timeout):
            response=io.BytesIO(json.dumps({'retCode':10006}).encode())
            response.headers={'X-Bapi-Limit':'2','X-Bapi-Limit-Status':'0','X-Bapi-Limit-Reset-Timestamp':'1792530000000','Authorization':'fixture must not be retained'}
            return response
        with patch('thoth.bybit_testnet.BYBIT_PACER'):
            with self.assertRaises(BybitError) as error:TestnetClient('fixture','fixture',transport=transport).request('GET','/v5/position/list',{},private=True)
        details=failure_details(error.exception,'account_monitor')
        self.assertEqual(details['rate_quota'],dict(limit=2,remaining=0,reset_ms=1792530000000))
        self.assertNotIn('Authorization',json.dumps(details))
