import _environment
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from thoth.app import app
from thoth.models import Settings
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.intrabar_exit import observe_exit
from thoth.market import chart_history
from thoth.timeframes import interval_seconds
from thoth.runner import Runner
from thoth.engine import Engine
from test_indicator import fixture


class TimeframeTests(unittest.TestCase):
    def test_chart_and_indicator_replay_use_actual_interval_and_exclude_forming(self):
        client=TestClient(app)
        for interval,seconds in [('5',300),('60',3600),('240',14400)]:
            rows=fixture(300)
            for i,row in enumerate(rows):row['time']=i*seconds
            cfg=Settings(timeframe=interval)
            expected=evaluate(rows,cfg,now=299*seconds+seconds//2)
            self.assertEqual(len(expected),299)
            stream=IndicatorState(cfg)
            self.assertEqual([stream.step(row) for row in rows[:-1]],expected)
            with tempfile.TemporaryDirectory() as d:
                runner=Runner(Engine(Path(d)/'offline.sqlite'))
                history=runner.history('ETHUSDT',interval,rows)
                self.assertEqual(runner.analysis('ETHUSDT',cfg,history,299*seconds+seconds//2)[0],expected)
            with patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows) as fetch:
                response=client.get('/api/chart/ETHUSDT?interval='+interval)
                self.assertEqual(response.status_code,200,response.text)
                fetch.assert_called_once_with('ETHUSDT',interval)
            with patch('thoth.market._history_page',return_value=rows):
                self.assertEqual(chart_history('ETHUSDT',interval,1000,'live'),rows)

    def test_sma_exit_uses_forming_five_minute_hourly_and_four_hour_candle(self):
        for interval,seconds in [('5',300),('60',3600),('240',14400)]:
            now=1000*seconds+10
            rows=[dict(time=(950+i)*seconds,close=100 if i<50 else 99) for i in range(51)]
            self.assertIn('below',observe_exit({},rows,interval,'Buy',now))
            self.assertEqual(interval_seconds(interval),seconds)

    def test_entry_freshness_is_two_actual_candles_not_two_days(self):
        from thoth.testnet import TestnetSupervisor
        from thoth.models import TestnetSettings
        from test_testnet import Exchange
        for interval,seconds in [('5',300),('60',3600),('240',14400)]:
            with tempfile.TemporaryDirectory() as directory:
                now=1792454400.
                exchange=Exchange(lambda:now)
                service=TestnetSupervisor(Path(directory)/'account.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now)
                cfg=TestnetSettings(timeframe=interval,dedicated_account=True)
                service.configure(cfg);service.activate('ENABLE TESTNET ORDERS')
                saved=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
                service.apply_signals(saved,{'BTCUSDT':dict(time=now-3*seconds,signal=None)})
                service.apply_signals(saved,{'BTCUSDT':dict(time=now-2*seconds-1,signal='Long')})
                self.assertFalse(exchange.orders)
                service.apply_signals(saved,{'BTCUSDT':dict(time=now-seconds,signal='Long')})
                self.assertEqual(len(exchange.orders),1)
