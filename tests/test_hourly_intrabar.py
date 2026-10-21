"""Synthetic hourly entry/minute exit checks; never connects to Bybit."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
from thoth.models import Settings,TestnetSettings
from thoth.intrabar_entry import observe_breakout
from thoth.runner import Runner
from thoth.engine import Engine
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
import test_testnet as fixtures


class IntrabarBreakoutTests(unittest.TestCase):
    def setUp(self):
        self.cfg=Settings(strategy='sr_hourly_intrabar')
        self.levels={'long_resistance_major':dict(value=101,pivot_time=0),
                     'short_resistance_minor':dict(value=101,pivot_time=0),
                     'long_support_major':dict(value=99,pivot_time=0)}
        self.tracker={}

    def sample(self,price,now=360010,levels=None,tracker=None):
        start=int(now)//3600*3600
        rows=[dict(time=start-(50-i)*3600,close=100.) for i in range(51)]
        rows[-1]['close']=price
        return observe_breakout(self.tracker if tracker is None else tracker,rows,self.levels if levels is None else levels,self.cfg,now)

    def test_long_and_short_trigger_before_hour_close_without_duplicate_reentry(self):
        self.assertIsNone(self.sample(100)['signal'])
        self.assertEqual(self.sample(102,360015)['signal'],'Long')
        self.assertEqual(len(self.tracker['fired']),2)  # Both families, one signal.
        self.sample(100,360020)
        self.assertIsNone(self.sample(102,360025)['signal'])
        self.assertEqual(self.sample(98,360030)['signal'],'Short')

    def test_minor_only_and_major_only_are_respected(self):
        self.cfg.major=False
        self.sample(100)
        self.assertEqual(self.sample(102,360015)['signal'],'Long')
        self.assertEqual(self.tracker['fired'],['short_resistance_minor'])
        self.cfg.major=True;self.cfg.minor=False;self.tracker={}
        self.sample(100)
        self.assertEqual(self.sample(98,360015)['signal'],'Short')

    def test_new_level_or_stale_gap_cannot_manufacture_crossing(self):
        self.sample(100)
        changed=deepcopy(self.levels)
        for level in changed.values():level['pivot_time']=3600
        self.assertIsNone(self.sample(102,360015,changed)['signal'])
        self.assertIsNone(self.sample(98,360060,changed)['signal'])
        self.assertEqual(self.sample(102,360065,changed)['signal'],'Long')

    def test_invalid_or_missing_forming_bar_is_rejected(self):
        with self.assertRaises(ValueError):observe_breakout({},[dict(time=356400,close=102)],self.levels,self.cfg,360010)
        with self.assertRaises(ValueError):self.sample(float('nan'))

    def test_intrabar_high_does_not_replay_a_cross_already_over(self):
        self.sample(102)
        self.assertIsNone(self.sample(102,360015)['signal'])

    def test_filters_are_optional_and_crossings_are_consumed_when_filtered(self):
        self.cfg.sma_enabled=True;self.cfg.sma_length=200
        self.sample(100)
        self.assertIsNone(self.sample(102,360015)['signal'])
        self.cfg.sma_enabled=False
        self.assertIsNone(self.sample(102,360020)['signal'])

    def test_strategy_fixes_entry_hour_and_requires_minute_exit(self):
        cfg=Settings(strategy='sr_hourly_intrabar',timeframe='240',sma_exit_enabled=False)
        self.assertEqual(cfg.timeframe,'60');self.assertTrue(cfg.sma_exit_enabled)
        self.assertTrue(cfg.major and cfg.minor)
        with self.assertRaises(ValueError):Settings(strategy='sr_hourly_intrabar',major=False,minor=False)

    def test_tracker_and_consumed_cross_survive_runner_restart(self):
        with tempfile.TemporaryDirectory() as d:
            engine=Engine(Path(d)/'offline.sqlite');runner=Runner(engine)
            result=[dict(time=356400,levels=self.levels)]
            def rows(price):return [dict(time=360000,close=price)]
            self.assertIsNone(runner.intrabar_observation('ETHUSDT',self.cfg,rows(100),result,360010)['signal'])
            runner=Runner(engine)
            self.assertEqual(runner.intrabar_observation('ETHUSDT',self.cfg,rows(102),result,360015)['signal'],'Long')
            runner=Runner(engine)
            self.assertIsNone(runner.intrabar_observation('ETHUSDT',self.cfg,rows(100),result,360020)['signal'])
            self.assertIsNone(runner.intrabar_observation('ETHUSDT',self.cfg,rows(102),result,360025)['signal'])
            with self.assertRaises(ValueError):runner.intrabar_observation('ETHUSDT',self.cfg,rows(102),[],360030)

    def test_runner_fetches_hourly_and_emits_live_observation_not_closed_signal(self):
        with tempfile.TemporaryDirectory() as d:
            class Account:
                path=str(Path(d)/'account.sqlite')
                def snapshot(account):
                    cfg=self.cfg.model_copy(update={'automatic_paper':True,'symbols':['ETHUSDT']})
                    return dict(settings=cfg.model_dump(),status='running',positions=[])
                def market_cycle(account,cfg,observations):account.output=observations
            account=Account();now=[360010];price=[100.]
            def fetch(symbol,interval):
                self.assertEqual(interval,'60')
                return [dict(time=i*3600,open=100.,high=max(100.,price[0]),low=min(100.,price[0]),close=price[0] if i==100 else 100.) for i in range(101)]
            runner=Runner(account,fetch=fetch)
            # Confirmed-level fixture isolates the live entry plumbing from pivot discovery.
            confirmed=[dict(time=356400,levels=self.levels,signal='Short')]
            with patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch.object(runner,'analysis',return_value=(confirmed,300)):
                runner.cycle(paced=False)
                self.assertIsNone(account.output['ETHUSDT']['signal'])
                now[0]+=5;price[0]=102
                runner.cycle(paced=False)
                self.assertEqual(account.output['ETHUSDT']['signal'],'Long')
                self.assertEqual(account.output['ETHUSDT']['time'],360015)
                self.assertEqual(account.output['ETHUSDT']['exit_timeframe'],'1')
                now[0]+=5;runner.cycle(paced=False)
                self.assertIsNone(account.output['ETHUSDT']['signal'])

    def test_minute_chart_remains_independent_of_hourly_execution(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        from test_indicator import fixture
        rows=fixture(300)
        with patch('thoth.app.live.snapshot',return_value={'config':self.cfg.model_dump()}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=300*60):
            response=TestClient(app).get('/api/chart/ETHUSDT?interval=1')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['analysis']),300)
        self.assertIn('S/R reference arrows',response.json()['indicator_status'])

    def test_sr_reference_arrows_are_visible_on_every_chart_without_changing_execution(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        from test_indicator import fixture
        config=self.cfg.model_dump()
        for interval,duration in [('1',60),('5',300),('60',3600),('240',14400),('D',86400)]:
            rows=fixture(1000)
            for i,row in enumerate(rows):row['time']=i*duration
            display=Settings(**{**config,'strategy':'sr_breakout','timeframe':interval})
            expected=evaluate(rows,display,now=1000*duration)
            self.assertTrue(any(r['signal'] for r in expected))
            with patch('thoth.app.live.snapshot',return_value={'config':config}),patch('thoth.app.live_feed',None),patch('thoth.app.candles',return_value=rows),patch('time.time',return_value=1000*duration):
                response=TestClient(app).get('/api/chart/ETHUSDT?interval='+interval)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['analysis'],expected)
            self.assertIn('not historical live intrabar triggers',response.json()['indicator_status'])
        self.assertEqual(config['strategy'],'sr_hourly_intrabar')
        self.assertEqual(config['timeframe'],'60')

    def test_historical_ohlc_does_not_claim_reconstructed_intrabar_signals(self):
        from test_indicator import fixture
        rows=fixture(300)
        for i,row in enumerate(rows):row['time']=i*3600
        batch=evaluate(rows,self.cfg,now=300*3600)
        stream=IndicatorState(self.cfg)
        self.assertEqual([stream.step(r) for r in rows],batch)
        self.assertTrue(all(r['signal'] is None for r in batch))


class HourlyPositionTests(unittest.TestCase):
    setUp=fixtures.TestnetIntegration.setUp
    tearDown=fixtures.TestnetIntegration.tearDown
    create=fixtures.TestnetIntegration.create
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter

    def test_hourly_position_uses_minute_exits_both_directions_after_restart(self):
        for side in ('Long','Short'):
            with self.subTest(side=side):
                # Separate durable supervisor/journal for each direction.
                self.path=Path(self.temp.name)/(side+'.sqlite')
                self.exchange=fixtures.Exchange(lambda:self.now[0]);self.service=self.create()
                self.cfg=TestnetSettings(strategy='sr_hourly_intrabar',dedicated_account=True)
                self.service.configure(self.cfg);self.activate()
                self.signal(self.now[0]-5,None);self.now[0]+=5;self.signal(self.now[0],side)
                link=next(o['orderLinkId'] for o in self.exchange.orders.values() if not o['reduceOnly'])
                self.exchange.fill(link);self.service.monitor()
                owner=self.service.state['owned']['BTCUSDT']
                self.assertEqual((owner['strategy'],owner['entry_timeframe'],owner['exit_timeframe']),('sr_hourly_intrabar','60','1'))
                metadata=self.service.journal.records()[0]['instrument']
                self.assertEqual(metadata['thoth_exit_timeframe'],'1')
                self.service=self.create()  # Risk worker restores management, never entry permission.
                self.service.activate(self.service.confirmation,recovery=True)
                self.now[0]+=6
                def candles(symbol,interval,limit):
                    self.assertEqual(interval,'1');self.assertEqual(limit,51)
                    start=int(self.now[0])//60*60
                    return [dict(time=start-(50-i)*60,close=100 if i<50 else (99 if side=='Long' else 101)) for i in range(51)]
                with patch.object(self.exchange,'candles',side_effect=candles):self.service.monitor()
                closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
                self.assertEqual(len(closes),1)
                self.assertIn('below' if side=='Long' else 'above',self.service.state['owned']['BTCUSDT']['reason'])
                self.exchange.fill(closes[0]['orderLinkId']);self.service.monitor()
                trade=self.service.history()['items'][0]
                self.assertEqual((trade['strategy'],trade['entry_timeframe'],trade['exit_timeframe']),('sr_hourly_intrabar','60','1'))
                self.assertFalse(self.service.state['entries'])

    def test_intrabar_signal_expires_in_thirty_seconds(self):
        self.cfg=TestnetSettings(strategy='sr_hourly_intrabar',dedicated_account=True)
        self.service.configure(self.cfg);self.activate()
        self.signal(self.now[0]-100,None)
        self.signal(self.now[0]-40,'Long')
        self.assertFalse(self.exchange.orders)
