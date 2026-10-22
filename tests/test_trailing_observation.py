import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from thoth.trailing_observation import TrailingObservation,ObservationSettings,advance_dynamic


def config(**kw):return ObservationSettings(**kw).model_dump()


def snapshot(at,pnl=1,trigger=False,positions=None):
    positions=positions if positions is not None else [dict(symbol='BTCUSDT',link='a',qty=1.,entry=100.,side='Buy')]
    return dict(at=at,positions=positions,marks={p['symbol']:100+pnl/len(positions) for p in positions},activation=1.,fixed_drop=.1,
        fixed=dict(active=bool(positions),triggered=trigger,triggered_at=at if trigger else None,current=str(pnl),peak='1.5',threshold='1.4'))


class DynamicMathTests(unittest.TestCase):
    def test_threshold_never_falls_when_volatility_widens(self):
        s={};prior=-999
        for i,value in enumerate([1,1.5,1.49,1.51,1.6,1.61,1.62,1.63,1.64,1.65]):
            s=advance_dynamic(s,value,1,100+i*5,config())
            self.assertGreaterEqual(s['threshold'],prior);prior=s['threshold']
        self.assertFalse(s['warming_up'])
        s=advance_dynamic(s,2.5,1,150,config())
        self.assertGreaterEqual(s['threshold'],prior)
        self.assertTrue(.1<=s['distance']<=.4)
        old=deepcopy(s);s=advance_dynamic(s,s['threshold'],1,155,config())
        self.assertTrue(s['triggered']);self.assertNotEqual(old,s)

    def test_gap_is_flagged_and_duplicate_or_old_sample_is_ignored(self):
        s=advance_dynamic({},1,1,100,config())
        self.assertEqual(advance_dynamic(s,3,1,100,config()),s)
        self.assertTrue(advance_dynamic(s,1.1,1,160,config())['gap'])
        with self.assertRaises(ValueError):ObservationSettings(minimum=.5,maximum=.1)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=[100.]
        self.live=self.root/'live.sqlite'
        with sqlite3.connect(self.live) as db:db.execute('CREATE TABLE trade_history(link TEXT PRIMARY KEY,payload TEXT)')
        self.prices={'BTCUSDT':101.4};self.fetches=0
        def fetch():self.fetches+=1;return dict(self.prices)
        self.observer=TrailingObservation(self.root/'observer.sqlite',self.live,fetch,lambda:self.now[0])
    def tearDown(self):self.temp.cleanup()

    def test_eleven_positions_batch_continuation_and_confirmed_actual_are_separate(self):
        positions=[dict(symbol='COIN'+str(i)+'USDT',link=str(i),qty=1.,entry=100.,side='Buy') for i in range(11)]
        first=snapshot(100,1.5,positions=positions);before=deepcopy(first)
        self.observer.process(first);self.assertEqual(first,before)
        self.observer.process(snapshot(105,1.4,True,positions))
        self.now[0]=120;self.prices={p['symbol']:100+2/11 for p in positions};self.observer.tick()
        self.assertEqual(self.fetches,1)
        row=self.observer.report()['items'][0]
        self.assertEqual(row['position_count'],11);self.assertFalse(row['dynamic'].get('triggered'))
        # Confirmed actual net record cannot be invented from the gross trigger.
        self.assertNotIn('realized',row)
        with sqlite3.connect(self.live) as db:
            for p in positions:db.execute('INSERT INTO trade_history VALUES(?,?)',(p['link'],json.dumps(dict(pnl=.1,closed_at=110,reason='Open P&L trailing stop'))))
        self.now[0]=135;self.prices={p['symbol']:100+1.5/11 for p in positions};self.observer.tick()
        row=self.observer.report()['items'][0]
        self.assertAlmostEqual(row['realized'],1.1)
        self.assertTrue(row['dynamic']['triggered'])
        self.assertAlmostEqual(row['dynamic']['result_gross'],1.5)
        self.assertGreater(row['assumed_fees'],0)
        self.assertAlmostEqual(row['gross_quote_difference'],.1)
        self.assertLess(row['estimate_after_assumed_fees_before_funding'],row['dynamic']['result_gross'])

    def test_restart_retains_cycle_settings_and_protected_threshold(self):
        self.observer.process(snapshot(100,1.5));old=self.observer.report()['items'][0]
        self.observer.configure(ObservationSettings(maximum=.8))
        restarted=TrailingObservation(self.root/'observer.sqlite',self.live,lambda:self.prices,lambda:105)
        restarted.process(snapshot(105,1.6))
        row=restarted.report()['items'][0]
        self.assertEqual(row['settings']['maximum'],.4)
        self.assertGreaterEqual(row['dynamic']['threshold'],old['dynamic']['threshold'])

    def test_basket_change_and_sampling_gap_suppress_advantage_claim(self):
        self.observer.process(snapshot(100,1.5))
        self.observer.process(snapshot(105,1.4,positions=[]))
        self.now[0]=200;self.observer.tick()
        row=self.observer.report()['items'][0]
        self.assertTrue(row['basket_changed']);self.assertTrue(row['warnings'])
        self.assertIsNone(row['gross_quote_difference'])

    def test_failure_disable_and_queue_are_isolated(self):
        self.observer.process(snapshot(100,1.5));self.observer.process(snapshot(105,1.4,True))
        before=self.live.read_bytes()
        with patch.object(self.observer,'fetch',side_effect=RuntimeError('down')):self.observer.tick()
        self.assertIn('unavailable',self.observer.report()['error'])
        self.assertEqual(self.live.read_bytes(),before)
        self.observer.configure(ObservationSettings(enabled=False))
        calls=self.fetches;self.now[0]+=60;self.observer.tick();self.assertEqual(calls,self.fetches)

    def test_hook_has_no_network_writes_and_cannot_change_fixed_state(self):
        from types import SimpleNamespace
        active={'BTCUSDT':dict(side='Buy',size='1',avgPrice='100',markPrice='101')}
        fixed=snapshot(100)['fixed'];before=deepcopy(fixed)
        with patch.object(self.observer,'fetch',side_effect=AssertionError('Network in risk callback')):
            self.observer.submit(active,SimpleNamespace(pnl_trailing_enabled=True,pnl_trailing_activation_dollars=1,pnl_trailing_drop_dollars=.1),fixed,{'BTCUSDT':{'link':'a'}},100)
        self.assertEqual(fixed,before);self.assertEqual(self.observer.inputs.qsize(),1)
        for _ in range(300):self.observer.submit(active,SimpleNamespace(pnl_trailing_enabled=True,pnl_trailing_activation_dollars=1,pnl_trailing_drop_dollars=.1),fixed,{'BTCUSDT':{'link':'a'}},100)
        self.assertEqual(self.observer.inputs.qsize(),256);self.assertGreater(self.observer.dropped,0)

    def test_live_risk_hook_failure_cannot_interrupt_fixed_close_all(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from test_portfolio_trailing import PortfolioTrailingIntegration
        fixture=PortfolioTrailingIntegration()
        fixture.setUp()
        try:
            fixture.service.trailing_observer=SimpleNamespace(submit=Mock(side_effect=RuntimeError('observation unavailable')))
            fixture.enter();fixture.exchange.prices['BTCUSDT']=103;fixture.service.monitor()
            self.assertEqual(float(fixture.service.state['pnl_trailing']['peak']),1.5)
            fixture.exchange.prices['BTCUSDT']=102.8;fixture.service.monitor()
            self.assertTrue(fixture.service.state['entries'])
            self.assertTrue(fixture.service.snapshot()['closing'])
            self.assertTrue(any(o['reduceOnly'] for o in fixture.exchange.orders.values()))
            fixture.fill_closes();self.assertFalse(fixture.service.snapshot()['closing'])
            self.assertTrue(fixture.service.state['entries'])
        finally:fixture.tearDown()

    def test_observation_api_can_only_change_observation_settings(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        with patch('thoth.app.trailing_observation',self.observer),patch('thoth.app.live.configure') as configure,patch('thoth.app.live.close_all') as close:
            client=TestClient(app)
            result=client.put('/api/live/trailing-comparison/settings',json=config(maximum=.8))
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['executor'],'fixed')
            self.assertEqual(result.json()['settings']['maximum'],.8)
            self.assertEqual(client.put('/api/live/trailing-comparison/settings',json={**config(),'executor':'dynamic'}).status_code,422)
            self.assertEqual(client.get('/api/live/trailing-comparison?limit=200').status_code,422)
            configure.assert_not_called();close.assert_not_called()
