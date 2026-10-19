import _environment  # Isolate app import before any persistent state is initialized.
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from thoth.app import app
from thoth.production import ProductionSupervisor


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch('thoth.app.live', ProductionSupervisor(Path(self.tmp.name) / 'live.sqlite'))
        self.patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_removed_modes_and_controls_are_unavailable(self):
        self.assertEqual(self.client.get('/').status_code,200)
        routes={r.path for r in app.routes}
        for path in ('/api/state','/api/settings','/api/control/{action}','/api/paper/orders','/api/paper/marks','/api/testnet','/api/testnet/activate'):
            self.assertNotIn(path,routes)
        for path in ('/api/paper/orders','/api/testnet/activate','/api/control/start'):
            self.assertIn(self.client.post(path,json={}).status_code,(404,405))
        for environment in ('paper','testnet'):
            self.assertEqual(self.client.get('/api/chart/ETHUSDT?environment='+environment).status_code,422)
            self.assertEqual(self.client.get('/api/quote/ETHUSDT?environment='+environment).status_code,422)
            self.assertEqual(self.client.post('/api/connection/verify?environment='+environment).status_code,422)
        html=self.client.get('/').text
        for removed in ('viewMode','paperPanel','testnetDialog','Paper simulation','Bybit testnet'):
            self.assertNotIn(removed,html)
        self.assertIn('LIVE · REAL FUNDS',html)

    def test_validation_and_origin(self):
        self.assertEqual(self.client.put('/api/live/settings', json={'daily_loss_pct': -1}).status_code, 422)
        self.assertEqual(self.client.post('/api/live/activate', headers={'Origin': 'https://attacker.example'}).status_code, 403)
        self.assertEqual(self.client.get('/', headers={'Host': 'attacker.example'}).status_code, 400)

    def test_chart_contract_and_outage(self):
        rows = [{'time': i*60, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 5} for i in range(201)]
        with patch('thoth.app.candles', return_value=rows):
            data = self.client.get('/api/chart/BTCUSDT').json()
            self.assertEqual(len(data['candles']), 201)
            self.assertEqual(len(data['sma']), 2)
            self.assertEqual(data['sma'][0]['value'], 100)
            self.assertIn('pending', data['indicator_status'])
        with patch('thoth.app.candles', side_effect=TimeoutError):
            self.assertEqual(self.client.get('/api/chart/BTCUSDT').status_code, 502)

    def test_public_quote_contract_and_redacted_failure(self):
        with patch('thoth.app.quote',return_value={'symbol':'ETHUSDT','environment':'live','price':2430.12,'observed_at':1792300001.}) as request:
            result=self.client.get('/api/quote/ETHUSDT?environment=live')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['price'],2430.12)
            request.assert_called_once_with('ETHUSDT','live')
        self.assertEqual(self.client.get('/api/quote/ETHUSDT?environment=wrong').status_code,422)
        with patch('thoth.app.quote',side_effect=RuntimeError('private upstream body')):
            result=self.client.get('/api/quote/ETHUSDT')
            self.assertEqual(result.status_code,502)
            self.assertNotIn('private upstream',result.text)

    def test_extended_chart_replay_does_not_touch_execution_history(self):
        from unittest.mock import Mock
        rows=[dict(time=i*60,open=100,high=101,low=99,close=100,volume=5) for i in range(2000)]
        worker=Mock()
        with patch('thoth.app.chart_history',return_value=rows) as fetch,patch('thoth.app.live_feed',worker):
            result=self.client.get('/api/chart/ETHUSDT?lookback=2000')
            self.assertEqual(result.status_code,200)
            self.assertEqual(len(result.json()['candles']),2000)
            self.assertTrue(result.json()['historical_replay'])
            fetch.assert_called_once_with('ETHUSDT','1',2000,'live')
            worker.history.assert_not_called();worker.analysis.assert_not_called()
        self.assertEqual(self.client.get('/api/chart/ETHUSDT?lookback=5001').status_code,422)

    def test_lifespan_runs_only_live_worker_and_preserves_historical_files(self):
        import thoth.app as module
        from thoth.models import TestnetSettings
        directory=Path(self.tmp.name)
        historical={directory/'paper.sqlite':b'old simulation archive',directory/'testnet.sqlite':b'old exchange archive'}
        for path,data in historical.items():path.write_bytes(data)
        module.live.configure(TestnetSettings(symbols=['HBARUSDT'],position_value=45,dedicated_account=True))
        with TestClient(app) as client:
            result=client.get('/api/live').json()
            self.assertEqual(result['config']['symbols'],['HBARUSDT'])
            self.assertEqual(result['config']['position_value'],45)
            self.assertFalse(result['entries'])
            self.assertIs(module.live_feed.engine.supervisor,module.live)
            self.assertEqual(module.live_feed.history_path,str(directory/'live.candles.sqlite'))
            self.assertFalse(hasattr(module,'testnet'))
            self.assertFalse(hasattr(module,'engine'))
        self.assertFalse(module.live_feed.thread.is_alive())
        self.assertFalse(module.live.thread.is_alive())
        for path,data in historical.items():self.assertEqual(path.read_bytes(),data)
        self.assertFalse((directory/'paper.candles.sqlite').exists())
        self.assertFalse((directory/'testnet.candles.sqlite').exists())

    def test_watchlist(self):
        entries = self.client.get('/api/watchlist').json()
        self.assertEqual(len(entries), 234)
        self.assertEqual(sum(item['selectable'] for item in entries), 224)


class LiveApiTests(unittest.TestCase):
    def setUp(self):
        from thoth.production import ProductionSupervisor
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch('thoth.app.live',ProductionSupervisor(Path(self.temp.name)/'live.sqlite'))
        self.patch.start();self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.patch.stop();self.temp.cleanup()

    def test_exchange_activation_disabled_without_server_flag(self):
        self.assertFalse(self.client.get('/api/live').json()['enabled_on_server'])
        response=self.client.post('/api/live/activate',json={'confirmation':'ENABLE LIVE ORDERS'})
        self.assertEqual(response.status_code,409)
        self.assertIn('disabled',response.json()['detail'])
        self.assertFalse(self.client.get('/api/live').json()['entries'])

    def test_isolated_default_and_private_internal_fields(self):
        response=self.client.get('/api/live').json()
        self.assertEqual(response['config']['margin_mode'],'isolated')
        self.assertNotIn('account_binding',response)
        self.assertNotIn('seen_closures',response['risk'])
        self.assertTrue(response['live_supported'])
