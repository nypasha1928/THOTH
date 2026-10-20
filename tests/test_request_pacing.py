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
