import io,json,unittest
from unittest.mock import patch
from thoth.market import quote,_quote


class QuoteTests(unittest.TestCase):
    def setUp(self):_quote.cache_clear()

    def test_public_environment_cache_and_no_credentials(self):
        body={'retCode':0,'result':{'list':[{'symbol':'ETHUSDT','lastPrice':'2430.12'}]}}
        with patch('thoth.market.urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(body).encode())) as request,patch('thoth.market.time',return_value=1792300001.):
            self.assertEqual(quote('ETHUSDT','live')['price'],2430.12)
            quote('ETHUSDT','live');self.assertEqual(request.call_count,1)
            quote('ETHUSDT','testnet');self.assertEqual(request.call_count,2)
            self.assertTrue(request.call_args.args[0].startswith('https://api-testnet.bybit.com/v5/market/tickers?'))
            self.assertNotIn('X-BAPI',str(request.call_args))
            with patch('thoth.market.time',return_value=1792300006.):quote('ETHUSDT','live')
            self.assertEqual(request.call_count,3)

    def test_mismatched_and_nonfinite_quotes_are_rejected(self):
        for symbol,value in [('BTCUSDT','1'),('ETHUSDT','NaN'),('ETHUSDT','0')]:
            body={'retCode':0,'result':{'list':[{'symbol':symbol,'lastPrice':value}]}}
            with patch('thoth.market.urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(body).encode())):
                with self.assertRaises(ValueError):quote('ETHUSDT','live')

    def test_clock_advances_even_when_price_quote_is_cached(self):
        now=[1792300001.]
        body={'retCode':0,'time':(now[0]-.1)*1000,'result':{'list':[{'symbol':'ETHUSDT','lastPrice':'2430.12'}]}}
        with patch('thoth.market.urlopen',side_effect=lambda *a,**k:io.BytesIO(json.dumps(body).encode())) as request,patch('thoth.market.time',side_effect=lambda:now[0]):
            first=quote('ETHUSDT','live');now[0]+=2;second=quote('ETHUSDT','live')
            self.assertEqual(request.call_count,1)
            self.assertEqual(second['observed_at'],first['observed_at'])
            self.assertEqual(second['server_time'],first['server_time']+2)
            self.assertAlmostEqual(second['exchange_clock_offset'],-.1,places=5)
