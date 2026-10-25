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
        self.feed_patch=patch('thoth.app.live_feed',None);self.feed_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.feed_patch.stop()
        self.tmp.cleanup()

    def test_order_block_5m_reference_is_read_only_and_strategy_filter_available(self):
        from test_order_block import formation_rows
        response=self.client.put('/api/live/settings',json={'strategy':'sr_order_block_gate'})
        self.assertEqual(response.status_code,200,response.text)
        before=self.client.get('/api/live').json()['config']
        self.assertEqual(before['timeframe'],'5');self.assertFalse(before['sma_exit_enabled'])
        rows=formation_rows()
        with patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=rows[-1]['time']+20):
            response=self.client.get('/api/chart/BTCUSDT?interval=5&include_order_block=true')
            self.assertEqual(response.status_code,200,response.text)
            body=response.json();self.assertEqual(body['chart_signal_strategy'],'sr_breakout')
            self.assertIn({'kind':'bullish_formed','side':'Long'},body['order_block_analysis'][-1]['events'])
            self.assertIn('trading confirmation remains 5m',body['order_block_status'])
            hidden=self.client.get('/api/chart/BTCUSDT?interval=5&include_order_block=false').json()
            self.assertEqual(hidden['order_block_analysis'],[])
        self.assertEqual(self.client.get('/api/live').json()['config'],before)
        self.assertEqual(self.client.get('/api/live/analysis?strategy=sr_order_block_gate').status_code,200)
        self.assertFalse(self.client.get('/api/live').json()['entries'])

    def test_order_block_boxes_use_every_chart_interval_without_changing_trading(self):
        from test_order_block import formation_rows
        from thoth.timeframes import interval_seconds
        response=self.client.put('/api/live/settings',json={'strategy':'sr_order_block_gate'})
        self.assertEqual(response.status_code,200)
        before=self.client.get('/api/live').json()['config']
        for interval,label in [('1','1m'),('5','5m'),('60','1h'),('240','4h'),('D','Daily')]:
            rows=formation_rows()
            for i,row in enumerate(rows):row['time']=i*interval_seconds(interval)
            with self.subTest(interval=interval),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=rows[-1]['time']+20):
                response=self.client.get('/api/chart/BTCUSDT',params={'interval':interval,'include_order_block':'true'})
                self.assertEqual(response.status_code,200,response.text)
                result=response.json();last=result['order_block_analysis'][-1]
                self.assertEqual(last['zones']['demand'][0]['high'],106)
                self.assertEqual(last['zones']['demand'][0]['low'],97)
                self.assertIn('Order Block '+label+' chart references',result['order_block_status'])
                self.assertIn('trading confirmation remains 5m',result['order_block_status'])
        self.assertEqual(self.client.get('/api/live').json()['config'],before)
        self.assertEqual(before['timeframe'],'5')
        self.assertFalse(self.client.get('/api/live').json()['entries'])

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

    def test_entire_watchlist_saves_and_single_letter_coins_are_accessible(self):
        symbols=[row['symbol'] for row in self.client.get('/api/watchlist').json() if row['selectable']]
        response=self.client.put('/api/live/settings',json={'symbols':symbols})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/live').json()['config']['symbols'],symbols)
        rows=[dict(time=i*60,open=100,high=101,low=99,close=100,volume=5) for i in range(201)]
        for symbol in ('HUSDT','WUSDT','SUSDT','FUSDT'):
            with self.subTest(symbol=symbol), patch('thoth.app.candles',return_value=rows), patch('thoth.app.quote',return_value={'price':100}):
                self.assertEqual(self.client.get('/api/chart/'+symbol).status_code,200)
                self.assertEqual(self.client.get('/api/quote/'+symbol).status_code,200)
                self.assertEqual(self.client.get('/api/live/history',params={'symbol':symbol}).status_code,200)
                self.assertEqual(self.client.get('/api/live/analysis',params={'symbol':symbol}).status_code,200)
        for symbol in ('USDT','badUSDT','H/USDT'):
            self.assertEqual(self.client.put('/api/live/settings',json={'symbols':[symbol]}).status_code,422)

    def test_signal_decisions_are_filtered_and_bounded(self):
        import json
        from thoth.app import live
        with live.db() as db:
            for symbol in ('JCTUSDT','ETHUSDT','JCTUSDT'):
                payload=dict(symbol=symbol,reason='skipped: recovery baseline')
                db.execute('INSERT INTO signal_decisions(time,symbol,payload) VALUES(?,?,?)',(1,symbol,json.dumps(payload)))
        result=self.client.get('/api/live/signal-decisions?symbol=JCTUSDT&limit=1')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['items'],[dict(symbol='JCTUSDT',reason='skipped: recovery baseline')])
        self.assertEqual(self.client.get('/api/live/signal-decisions?limit=201').status_code,422)

    def test_signal_alerts_paginate_and_filter_saved_decisions(self):
        import json
        from thoth.app import live
        with live.db() as db:
            for i in range(55):
                payload=dict(symbol='JCTUSDT' if i%2 else 'ETHUSDT',time=i,signal='Long',reason='queued')
                db.execute('INSERT INTO signal_decisions(time,symbol,payload) VALUES(?,?,?)',(i,payload['symbol'],json.dumps(payload)))
        first=self.client.get('/api/live/signal-alerts').json()
        self.assertEqual(first['total'],55);self.assertEqual(len(first['items']),50)
        self.assertEqual(first['items'][0]['time'],54)
        second=self.client.get('/api/live/signal-alerts?offset=50').json()
        self.assertEqual(len(second['items']),5)
        filtered=self.client.get('/api/live/signal-alerts?symbol=JCTUSDT').json()
        self.assertEqual(filtered['total'],27)
        self.assertTrue(all(x['symbol']=='JCTUSDT' for x in filtered['items']))
        self.assertEqual(self.client.get('/api/live/signal-alerts?limit=101').status_code,422)
        self.assertEqual(self.client.get('/api/live/signal-alerts?offset=-1').status_code,422)

    def test_alert_watch_refresh_finds_old_visible_alert_after_pagination(self):
        import json
        from thoth.app import live
        with live.db() as db:
            for i in range(60):
                payload=dict(symbol='JCTUSDT',time=i,signal='Long',reason='queued',alert_key='fixture-'+str(i))
                db.execute('INSERT INTO signal_decisions(time,symbol,payload) VALUES(?,?,?)',(i,'JCTUSDT',json.dumps(payload)))
        data=self.client.get('/api/live/signal-alerts?watch_ids=1,2').json()
        self.assertEqual([row['id'] for row in data['updates']],[2,1])
        self.assertFalse(any(row['id']==1 for row in data['items']))
        self.assertEqual(self.client.get('/api/live/signal-alerts?watch_ids=bad').status_code,422)

    def test_analysis_source_filter_applies_to_totals_and_time_tables(self):
        from thoth.app import live
        for identity,source,pnl in [('one','sr:short_support_minor',-1),('two','rsi:ml_long_up',2)]:
            live._record_trade(identity,dict(symbol='ETHUSDT',pnl=pnl,opened_at=1792846800,closed_at=1792850400,entry_sources=[source]))
        response=self.client.get('/api/live/analysis',params={'source':'sr:short_support_minor'})
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['totals']['trades'],1)
        self.assertEqual(data['totals']['realized_pnl'],-1)
        self.assertEqual(data['timing']['included_trades'],1)
        self.assertEqual(len(data['by_source_timing']),1)
        self.assertEqual(len(data['source_options']),2)

    def test_volume_delta_chart_does_not_fabricate_higher_timeframe_history(self):
        from thoth.app import live
        from thoth.models import TestnetSettings
        live.configure(TestnetSettings(strategy='volume_delta_intrabar',timeframe='5'))
        start=300*10000
        rows=[dict(time=start-(59-i)*300,open=100,high=111,low=99,close=100 if i<59 else 110,volume=10) for i in range(60)]
        lower=[dict(time=start,open=100,high=102,low=99,close=101,volume=10)]
        with patch('thoth.app.candles',return_value=rows),patch('thoth.app.chart_history',return_value=lower),patch('time.time',return_value=start+20):
            response=self.client.get('/api/chart/BTCUSDT?interval=5')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertTrue(all(row['signal'] is None for row in data['analysis'][:-1]))
        self.assertEqual(data['analysis'][-1]['signal'],'Long')
        self.assertIn('no SMA exit',data['indicator_status'])
        with patch('thoth.app.candles',return_value=rows),patch('thoth.app.chart_history',side_effect=ValueError),patch('time.time',return_value=start+20):
            data=self.client.get('/api/chart/BTCUSDT?interval=5').json()
        self.assertTrue(all(row['signal'] is None for row in data['analysis']))
        self.assertIn('no signal fabricated',data['indicator_status'])

    def test_volume_delta_overlay_independent_of_saved_trading_strategy(self):
        start=300*10000
        rows=[dict(time=start-(59-i)*300,open=100,high=111,low=99,close=100 if i<59 else 110,volume=10) for i in range(60)]
        lower=[dict(time=start,open=100,high=102,low=99,close=101,volume=10)]
        with patch('thoth.app.candles',return_value=rows),patch('thoth.app.chart_history',return_value=lower) as fetch,patch('time.time',return_value=start+20):
            without=self.client.get('/api/chart/BTCUSDT?interval=5').json()
            self.assertEqual(without['volume_delta_analysis'],[]);fetch.assert_not_called()
            data=self.client.get('/api/chart/BTCUSDT?interval=5&include_volume_delta=true').json()
        self.assertEqual(data['chart_signal_strategy'],'sr_breakout')
        self.assertEqual(data['volume_delta_analysis'][-1]['signal'],'Long')
        self.assertTrue(all(row['signal'] is None for row in data['volume_delta_analysis'][:-1]))
        self.assertIn('closed reference bars covered',data['volume_delta_status'])
        from thoth.app import live
        self.assertEqual(live.snapshot()['config']['strategy'],'sr_breakout')

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

    def test_stale_saved_history_does_not_blank_coin_charts_or_reset_checkpoints(self):
        import sqlite3
        from types import SimpleNamespace
        from thoth.runner import Runner
        runner=Runner(SimpleNamespace(path=str(Path(self.tmp.name)/'feed.sqlite')))
        old=[dict(time=i*60,open=100,high=101,low=99,close=100,volume=5) for i in range(201)]
        fresh=[{**row,'time':row['time']+100000*60} for row in old]
        from thoth.models import Settings
        for symbol in ('AVAXUSDT','SEIUSDT'):
            runner.history(symbol,'1',old)
            runner.analysis(symbol,Settings(timeframe='1'),old,old[-1]['time']+60)
        with sqlite3.connect(runner.history_path) as db:
            before=db.execute('SELECT * FROM checkpoints ORDER BY symbol').fetchall()
        with patch('thoth.app.live_feed',runner),patch('thoth.app.candles',return_value=fresh):
            for symbol in ('AVAXUSDT','SEIUSDT'):
                response=self.client.get('/api/chart/'+symbol+'?include_rsi=true')
                self.assertEqual(response.status_code,200,response.text)
                result=response.json()
                self.assertEqual(result['candles'],fresh)
                self.assertEqual(len(result['analysis']),len(fresh))
                self.assertEqual(len(result['rsi_analysis']),len(fresh))
                self.assertIn('Trading history recovery is separate',result['history_notice'])
        with sqlite3.connect(runner.history_path) as db:
            self.assertEqual(db.execute('SELECT * FROM checkpoints ORDER BY symbol').fetchall(),before)
        # Execution continuity remains strict; display fallback cannot invent bars.
        with self.assertRaisesRegex(ValueError,'backfill required'):
            runner.history('AVAXUSDT','1',fresh)
        broken=[fresh[0],fresh[2]]
        with patch('thoth.app.live_feed',runner),patch('thoth.app.candles',return_value=broken):
            self.assertEqual(self.client.get('/api/chart/AVAXUSDT').status_code,502)

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

    def test_history_api_paginates_filters_and_has_no_trading_side_effect(self):
        import thoth.app as module
        for i in range(205):
            module.live._record_trade(str(i),dict(symbol='ETHUSDT' if i%2 else 'BTCUSDT',closed_at=i,pnl=i/10,reason='Fixture'))
        result=self.client.get('/api/live/history?symbol=ETHUSDT&limit=2&offset=1')
        self.assertEqual(result.status_code,200)
        data=result.json()
        self.assertEqual(data['total'],102)
        self.assertEqual(len(data['items']),2)
        self.assertGreater(data['items'][0]['closed_at'],data['items'][1]['closed_at'])
        self.assertIsNone(module.live.client)
        self.assertEqual(self.client.get('/api/live/history?limit=101').status_code,422)
        self.assertEqual(self.client.get('/api/live/history?offset=-1').status_code,422)

    def test_analysis_uses_all_history_and_filters_without_exchange_requests(self):
        import thoth.app as module
        for i in range(205):
            module.live._record_trade(str(i),dict(symbol='ETHUSDT',strategy='sr_breakout' if i%2 else 'sma_cross',closed_at=i,pnl=1 if i%2 else -1))
        module.live._record_trade('other',dict(symbol='BTCUSDT',strategy='sr_breakout',closed_at=300,pnl=5))
        data=self.client.get('/api/live/analysis?symbol=ETHUSDT&strategy=sr_breakout').json()
        self.assertEqual(data['totals']['trades'],102)
        self.assertEqual(data['totals']['realized_pnl'],102)
        self.assertIsNone(module.live.client)
        self.assertEqual(self.client.get('/api/live/analysis').json()['totals']['trades'],206)
        self.assertEqual(self.client.get('/api/live/analysis?strategy=bad').status_code,422)
        self.assertEqual(self.client.get('/api/live/analysis?symbol=bad').status_code,422)

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
