"""Synthetic hourly RSI entry -> minute exit and bootstrap integration."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import TestnetSettings,Settings
from thoth.runner import Runner
from thoth.engine import Engine
from thoth.testnet_feed import FeedAdapter
import test_testnet as fixtures
from test_ml_rsi_draft import candles


class RSIAccountTests(unittest.TestCase):
    setUp=fixtures.TestnetIntegration.setUp
    tearDown=fixtures.TestnetIntegration.tearDown
    create=fixtures.TestnetIntegration.create

    def test_actual_hourly_feed_opens_both_directions_then_minute_exit_after_restart(self):
        for direction in ('Long','Short'):
            with self.subTest(direction=direction):
                self.path=Path(self.temp.name)/(direction+'.sqlite');self.exchange=fixtures.Exchange(lambda:self.now[0]);self.service=self.create()
                self.cfg=TestnetSettings(strategy='rsi_hourly_intrabar',symbols=['BTCUSDT'],dedicated_account=True,rsi=dict(ml_signals=False,volume_filter=False,vwap_filter=False,sma_filter=False,max_data=50))
                self.service.configure(self.cfg)
                start=int(self.now[0])//3600*3600;self.now[0]=start+10
                self.service.activate(self.service.confirmation)
                prices=[100+(-.1 if direction=='Long' else .1)*i for i in range(80)]
                rows=candles(prices+[prices[-1]],start=start-80*3600)
                runner=Runner(FeedAdapter(self.service),fetch=lambda *a:rows)
                with patch('thoth.runner.time.time',side_effect=lambda:self.now[0]):
                    runner.cycle(paced=False);self.assertFalse(self.exchange.orders)
                    rows[-1].update(open=100,close=100,high=101,low=99);self.now[0]+=5
                    runner.cycle(paced=False)
                    entries=[o for o in self.exchange.orders.values() if not o['reduceOnly']]
                    self.assertEqual(len(entries),1);self.assertEqual(entries[0]['side'],'Buy' if direction=='Long' else 'Sell')
                    self.exchange.fill(entries[0]['orderLinkId']);self.service.monitor()
                    self.now[0]+=5;runner.cycle(paced=False)
                    self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
                self.service=self.create();self.service.activate(self.service.confirmation,recovery=True);self.now[0]+=6
                def minute_rows(symbol,interval,limit):
                    self.assertEqual(interval,'1')
                    minute=int(self.now[0])//60*60
                    return [dict(time=minute-(50-i)*60,close=100 if i<50 else 99 if direction=='Long' else 101) for i in range(51)]
                with patch.object(self.exchange,'candles',side_effect=minute_rows):self.service.monitor()
                owner=self.service.state['owned']['BTCUSDT']
                self.assertEqual((owner['strategy'],owner['entry_timeframe'],owner['exit_timeframe']),('rsi_hourly_intrabar','60','1'))
                self.assertIsNotNone(owner['close_link']);self.assertFalse(self.service.state['entries'])
                self.exchange.fill(owner['close_link']);self.service.monitor()
                trade=self.service.history()['items'][0]
                self.assertEqual((trade['strategy'],trade['entry_timeframe'],trade['exit_timeframe']),('rsi_hourly_intrabar','60','1'))


class RSIBootstrapTests(unittest.TestCase):
    def test_backfill_is_bounded_then_durable_history_avoids_repeated_pages(self):
        with tempfile.TemporaryDirectory() as d:
            engine=Engine(Path(d)/'offline.sqlite');runner=Runner(engine)
            cfg=TestnetSettings(strategy='rsi_hourly_intrabar',rsi={'max_data':50})
            rows=candles([100+i%5 for i in range(150)]);calls=[]
            def fetch(symbol,interval,limit=1000,end=None):
                calls.append(end)
                available=[row for row in rows if end is None or row['time']*1000<=end]
                return available[-25:]
            service=SimpleNamespace(path=engine.path,load=lambda:{'config':cfg.model_dump()},client=SimpleNamespace(candles=fetch))
            adapter=FeedAdapter(service)
            cfg.rsi.ml_signals=False;adapter.fetch('ETHUSDT','60');self.assertEqual(len(calls),1)
            cfg.rsi.ml_signals=True;calls.clear();initial=adapter.fetch('ETHUSDT','60')
            self.assertGreaterEqual(len(initial),90);self.assertLessEqual(len(calls),5)
            self.assertEqual(len({row['time'] for row in initial}),len(initial))
            runner.history('ETHUSDT','60',initial)
            calls.clear();latest=adapter.fetch('ETHUSDT','60')
            self.assertEqual(len(calls),1);self.assertEqual(latest,rows[-25:])

    def test_chart_rsi_is_available_without_changing_saved_strategy_or_timeframe(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        rows=candles([100+i%9 for i in range(100)])
        cfg=Settings(strategy='sr_breakout',timeframe='1').model_dump()
        with patch('thoth.app.live.snapshot',return_value={'config':cfg}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=100*3600):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=60&include_rsi=true')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['rsi_analysis']),100)
        self.assertEqual(cfg['strategy'],'sr_breakout');self.assertEqual(cfg['timeframe'],'1')
