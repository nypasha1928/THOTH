import tempfile,unittest
from pathlib import Path
from thoth.models import Settings,TestnetSettings
from thoth.inverse_entry_delay import apply_delay
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange

class InverseDelayTests(unittest.TestCase):
    def cfg(self,delay=60):return Settings(strategy='sr_inverse_intrabar',inverse_direction_confirmed=True,inverse_entry_delay_seconds=delay)
    def test_wait_release_and_cancel_for_both_actual_directions(self):
        for raw,side,level,price in [('Long','Short','long_resistance_major',102),('Short','Long','short_support_minor',98)]:
            with self.subTest(raw=raw):
                cfg=self.cfg();tracker={};levels={level:dict(value=100,pivot_time=1)}
                def sample(now,signal=None,mark=price,**kwargs):
                    obs=dict(time=now,entry_bar=0,mark=mark,source_signal=raw if signal else None,signal=signal,entry_sources=['inverse_sr:'+level] if signal else [])
                    return apply_delay(tracker,obs,cfg,levels,now,continuous=kwargs.get('continuous',True),entries_allowed=kwargs.get('entries_allowed',True))
                self.assertIsNone(sample(10,side)['signal'])
                self.assertEqual(tracker['delayed_entry']['ready_at'],70)
                self.assertIsNone(sample(69)['signal'])
                released=sample(70)
                self.assertEqual((released['signal'],released['time'],released['source_signal']),(side,70,raw))
                self.assertEqual(released['inverse_signal_detected_at'],10)
                self.assertIsNone(sample(71)['signal'])
                for cancellation in ('pullback','restart','pause','level'):
                    tracker.clear();sample(10,side)
                    if cancellation=='level':levels[level]['pivot_time']=2
                    result=sample(70,mark=100 if cancellation=='pullback' else price,
                        continuous=cancellation!='restart',entries_allowed=cancellation!='pause')
                    self.assertIsNone(result['signal']);self.assertNotIn('delayed_entry',tracker)
                    self.assertIn('cancelled',result['inverse_delay_event'])
                tracker.clear();sample(10,side)
                result=apply_delay(tracker,dict(time=70,entry_bar=14400,mark=price,signal=None),cfg,levels,70,continuous=True,entries_allowed=True)
                self.assertIsNone(result['signal']);self.assertNotIn('delayed_entry',tracker)

    def test_delay_bounds_default_minute_and_zero_disables(self):
        self.assertEqual(Settings(strategy='sr_inverse_intrabar',inverse_direction_confirmed=True).inverse_entry_delay_seconds,60)
        self.assertEqual(self.cfg(0).inverse_entry_delay_seconds,0)
        for value in (-1,3601):
            with self.assertRaises(ValueError):self.cfg(value)
        obs=dict(signal='Short')
        self.assertIs(apply_delay({},obs,self.cfg(0),{},1,continuous=False,entries_allowed=False),obs)

    def test_selected_filters_are_rechecked_before_delayed_entry(self):
        cfg=self.cfg().model_copy(update={'sma_enabled':True,'sma_length':2})
        tracker={};levels={'long_resistance_major':dict(value=100,pivot_time=1)}
        start=dict(time=10,entry_bar=0,mark=102,source_signal='Long',signal='Short',entry_sources=['inverse_sr:long_resistance_major'])
        apply_delay(tracker,start,cfg,levels,10,continuous=True,entries_allowed=True)
        result=apply_delay(tracker,dict(time=70,entry_bar=0,mark=102,signal=None),cfg,levels,70,continuous=True,entries_allowed=True,rows=[dict(close=110),dict(close=102)])
        self.assertIsNone(result['signal'])
        self.assertIn('filters',result['inverse_delay_event'])

    def test_runner_waits_without_order_then_rechecks_and_submits_fresh_signal(self):
        with tempfile.TemporaryDirectory() as d:
            now=[14400*100+10.];start=int(now[0])//14400*14400
            exchange=Exchange(lambda:now[0]);path=Path(d)/'fixture.sqlite'
            service=TestnetSupervisor(path,enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy='sr_inverse_intrabar',timeframe='240',inverse_direction_confirmed=True,inverse_entry_delay_seconds=60,dedicated_account=True,symbols=['BTCUSDT'])
            service.configure(cfg);service.activate(service.confirmation)
            engine=FeedAdapter(service);runner=Runner(engine)
            self.assertTrue(engine.entry_delay_allowed('BTCUSDT'))
            service.state['risk']['symbols']['BTCUSDT']={'streak':3,'pause_until':now[0]+60};service.save()
            self.assertFalse(engine.entry_delay_allowed('BTCUSDT'))
            service.state['risk']['symbols'].pop('BTCUSDT');service.save()
            self.assertTrue(engine.entry_delay_allowed('BTCUSDT'))
            plain=Settings(**{k:v for k,v in cfg.model_dump().items() if k in Settings.model_fields})
            levels=[dict(time=start-14400,levels={'long_resistance_major':dict(value=101,pivot_time=0)})]
            def process(price):
                obs=runner.intrabar_observation('BTCUSDT',plain,[dict(time=start,close=price)],levels,now[0])
                engine.market_cycle(plain.model_dump(),{'BTCUSDT':obs});return obs
            process(100);now[0]+=5;process(102)
            self.assertFalse(exchange.orders)
            now[0]+=29;process(103);self.assertFalse(exchange.orders)
            now[0]+=31;result=process(103)
            self.assertEqual(result['signal'],'Short')
            self.assertEqual(result['time'],now[0])
            self.assertEqual(next(iter(exchange.orders.values()))['side'],'Sell')
            self.assertTrue(any('wait started' in e['message'] for e in service.state['events']))
            self.assertTrue(any('wait completed' in e['message'] for e in service.state['events']))
