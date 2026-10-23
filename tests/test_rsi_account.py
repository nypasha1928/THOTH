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

    def test_selected_timeframe_feed_opens_both_directions_then_minute_exit_after_restart(self):
        from thoth.timeframes import INTERVALS
        for mode,direction,interval in ((m,d,t) for m in ('corrected','uploaded_pine','fixed') for d in ('Long','Short') for t in (INTERVALS if m=='fixed' else ['60'])):
            with self.subTest(direction=direction,mode=mode,interval=interval):
                duration=INTERVALS[interval]
                self.path=Path(self.temp.name)/(mode+direction+interval+'.sqlite');self.exchange=fixtures.Exchange(lambda:self.now[0]);self.service=self.create()
                self.cfg=TestnetSettings(strategy='rsi_hourly_intrabar',timeframe=interval,symbols=['BTCUSDT'],dedicated_account=True,rsi=dict(calculation_mode=mode,ml_signals=mode!='corrected',static_signals=mode!='fixed',smooth=mode!='fixed',volume_filter=False,vwap_filter=False,sma_filter=False,max_data=50))
                self.service.configure(self.cfg)
                start=int(self.now[0])//duration*duration;self.now[0]=start+10
                self.service.activate(self.service.confirmation)
                prices=[100+(-.1 if direction=='Long' else .1)*i for i in range(80)]
                rows=candles(prices+[prices[-1]])
                for i,row in enumerate(rows):row['time']=start+(i-80)*duration
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
                self.assertEqual((owner['strategy'],owner['entry_timeframe'],owner['exit_timeframe']),('rsi_hourly_intrabar',interval,'1'))
                self.assertIsNotNone(owner['close_link']);self.assertFalse(self.service.state['entries'])
                self.exchange.fill(owner['close_link']);self.service.monitor()
                trade=self.service.history()['items'][0]
                self.assertEqual((trade['strategy'],trade['entry_timeframe'],trade['exit_timeframe']),('rsi_hourly_intrabar',interval,'1'))


class RSIBootstrapTests(unittest.TestCase):
    def test_backfill_is_bounded_then_durable_history_avoids_repeated_pages(self):
        with tempfile.TemporaryDirectory() as d:
            engine=Engine(Path(d)/'offline.sqlite');runner=Runner(engine)
            cfg=TestnetSettings(strategy='rsi_hourly_intrabar',rsi={'calculation_mode':'corrected','max_data':50})
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

    def test_chart_signal_families_are_independent_of_entry_family_switches(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        rows=candles([100-i for i in range(20)]+[82+i for i in range(50)])
        cfg=Settings(strategy='rsi_hourly_intrabar',rsi={'calculation_mode':'corrected','max_data':50,'static_signals':False,'ml_signals':True,'volume_filter':False,'vwap_filter':False,'sma_filter':False}).model_dump()
        with patch('thoth.app.live.snapshot',return_value={'config':cfg}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=70*3600):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=60&include_rsi=true')
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(any(event['kind']=='static30_up' for row in response.json()['rsi_analysis'] for event in row['crossings']))
        self.assertFalse(any(event['kind'].startswith('static') for row in response.json()['rsi_analysis'] for event in row['events']))
        self.assertFalse(cfg['rsi']['static_signals'])
        last=response.json()['rsi_analysis'][-1]
        self.assertEqual(len(last['cluster_centers']),3)
        self.assertEqual(last['rsi_cluster'],min(range(3),key=lambda i:abs(last['rsi']-last['cluster_centers'][i])))

    def test_uploaded_mode_chart_reports_source_threshold_issue_without_corrections(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        rows=candles([100-i for i in range(20)]+[82+i for i in range(50)])
        cfg=Settings(rsi={'calculation_mode':'uploaded_pine','volume_filter':False,'vwap_filter':False,'sma_filter':False}).model_dump()
        with patch('thoth.app.live.snapshot',return_value={'config':cfg}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=70*3600):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=60&include_rsi=true')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertIn('Uploaded Pine array behavior',data['rsi_status'])
        self.assertTrue(any(e['kind']=='static30_up' for row in data['rsi_analysis'] for e in row['events']))
        for row in data['rsi_analysis']:
            self.assertEqual(row['calculation_mode'],'uploaded_pine')
            self.assertIsNone(row['thresholds']['ml_short'])
            self.assertIsNone(row['thresholds']['ml_long'])
            self.assertFalse(any(e['kind'].startswith('ml_') for e in row['events']))
