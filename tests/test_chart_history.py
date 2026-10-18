import io,json,unittest
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
from thoth.market import chart_history,_history_page


class ChartHistoryTests(unittest.TestCase):
    def setUp(self):_history_page.cache_clear()

    def test_paginated_history_is_sorted_exclusive_bounded_and_public(self):
        calls=[]
        def response(url,**kwargs):
            calls.append(url);params=parse_qs(urlparse(url).query)
            end=int(params.get('end',[str(3000*60000)])[0])
            rows=[[str(i*60000),'100','101','99','100','5'] for i in range(3000) if i*60000<=end][-1000:]
            return io.BytesIO(json.dumps({'retCode':0,'result':{'list':list(reversed(rows))}}).encode())
        with patch('thoth.market.urlopen',side_effect=response),patch('time.sleep'):
            rows=chart_history('ETHUSDT','1',2500,'testnet')
        self.assertEqual(len(rows),2500);self.assertEqual(rows[0]['time'],500*60)
        self.assertEqual(rows[-1]['time'],2999*60)
        self.assertEqual(len(calls),3)
        self.assertTrue(all(url.startswith('https://api-testnet.bybit.com/') for url in calls))
        self.assertEqual(parse_qs(urlparse(calls[1]).query)['end'],[str(2000*60000-1)])

    def test_stopped_cursor_and_gaps_fail_instead_of_fabricating_bars(self):
        full=[dict(time=i*60,open=100,high=101,low=99,close=100,volume=5) for i in range(1000)]
        with patch('thoth.market._history_page',return_value=full),patch('time.sleep'):
            with self.assertRaisesRegex(ValueError,'advance'):chart_history('ETHUSDT','1',2000,'live')
        with patch('thoth.market._history_page',return_value=[full[0],full[2]]):
            with self.assertRaisesRegex(ValueError,'gap'):chart_history('ETHUSDT','1',2000,'live')

    def test_short_available_history_and_limits(self):
        row=dict(time=60,open=100,high=101,low=99,close=100,volume=5)
        with patch('thoth.market._history_page',return_value=[row]) as fetch:
            self.assertEqual(chart_history('ETHUSDT','1',10000,'live'),[row])
            self.assertEqual(fetch.call_count,1)
        for count in (0,10001):
            with self.assertRaises(ValueError):chart_history('ETHUSDT','1',count,'live')
