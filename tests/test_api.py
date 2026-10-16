import _environment  # Isolate app import before any persistent state is initialized.
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from thoth.app import app
from thoth.engine import Engine


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch('thoth.app.engine', Engine(Path(self.tmp.name) / 'api.sqlite'))
        self.patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_order_mark_stop_roundtrip(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.post('/api/control/start').status_code, 200)
        result = self.client.post('/api/paper/orders', json={
            'symbol': 'BTCUSDT', 'side': 'Long', 'price': 100, 'signal_id': 'api-test'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['positions'][0]['qty'], .5)
        result = self.client.post('/api/paper/marks', json={'prices': {'BTCUSDT': 97}})
        self.assertEqual(result.json()['positions'], [])
        self.assertEqual(result.json()['balance'], 248.5)

    def test_validation_and_origin(self):
        self.assertEqual(self.client.put('/api/settings', json={'daily_loss_pct': -1}).status_code, 422)
        self.assertEqual(self.client.post('/api/control/start', headers={'Origin': 'https://attacker.example'}).status_code, 403)
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

    def test_watchlist(self):
        entries = self.client.get('/api/watchlist').json()
        self.assertEqual(len(entries), 234)
        self.assertEqual(sum(item['selectable'] for item in entries), 224)


class TestnetApiTests(unittest.TestCase):
    def setUp(self):
        from thoth.testnet import TestnetSupervisor
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch('thoth.app.testnet',TestnetSupervisor(Path(self.temp.name)/'testnet.sqlite'))
        self.patch.start();self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.patch.stop();self.temp.cleanup()

    def test_exchange_activation_disabled_without_server_flag(self):
        self.assertFalse(self.client.get('/api/testnet').json()['enabled_on_server'])
        response=self.client.post('/api/testnet/activate',json={'confirmation':'ENABLE TESTNET ORDERS'})
        self.assertEqual(response.status_code,409)
        self.assertIn('disabled',response.json()['detail'])
        self.assertFalse(self.client.get('/api/testnet').json()['entries'])

    def test_isolated_default_and_private_internal_fields(self):
        response=self.client.get('/api/testnet').json()
        self.assertEqual(response['config']['margin_mode'],'isolated')
        self.assertNotIn('account_binding',response)
        self.assertNotIn('seen_closures',response['risk'])
        self.assertFalse(response['live_supported'])
