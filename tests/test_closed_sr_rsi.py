"""Closed-candle six-RSI validation and delayed execution without Order Block."""
import math,tempfile,unittest
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.rsi import RSIState
from thoth.closed_sr_rsi import combine_closed,observe
from thoth.delayed_30m import RSI_STRATEGY
from thoth.runner import Runner
from thoth.testnet_feed import FeedAdapter
import test_delayed_30m as fixtures

KINDS={'static30_up':'Long','static70_down':'Short','ml_long_up':'Long','ml_long_down':'Short','ml_short_up':'Long','ml_short_down':'Short'}

def rows():
    output=[]
    for i in range(600):
        c=100+10*math.sin(i*.12)
        output.append(dict(time=i*1800,open=c,high=c+.1,low=c-.1,close=c,volume=100))
    return output

class ClosedSources(unittest.TestCase):
    def test_all_six_real_rsi_crossings_directions_and_resume(self):
        cfg=Settings(strategy=RSI_STRATEGY,rsi=dict(signal_rules='eight_crossings',calculation_mode='uploaded_pine',static_signals=False,ml_signals=False,volume_filter=True,vwap_filter=True,sma_filter=True,histogram_filter=True))
        self.assertEqual(cfg.rsi.signal_rules,'original_six');self.assertEqual(cfg.rsi.calculation_mode,'fixed')
        self.assertTrue(cfg.rsi.static_signals and cfg.rsi.ml_signals)
        self.assertFalse(any(getattr(cfg.rsi,k) for k in ('volume_filter','vwap_filter','sma_filter','histogram_filter')))
        state=RSIState(cfg.rsi);seen={};data=rows();resumed=None
        for i,row in enumerate(data):
            result=state.step(row,1800)
            if resumed is not None:self.assertEqual(resumed.step(row,1800),result)
            for event in result['events']:
                self.assertEqual(event['side'],KINDS[event['kind']]);seen[event['kind']]=event['side']
            if i==300:
                resumed=RSIState(cfg.rsi,state.checkpoint())
        self.assertEqual(seen,KINDS)

    def test_sr_independence_conflict_and_no_minor(self):
        cfg=Settings(strategy=RSI_STRATEGY);row=dict(time=0,open=100,high=101,low=99,close=100)
        neutral=dict(signal=None,entry_sources=[],levels={})
        for kind,side in KINDS.items():
            result=combine_closed(dict(events=[dict(kind=kind,side=side)],conflict=False),neutral,row,100,cfg)
            self.assertEqual(result['signal'],side);self.assertEqual(result['entry_sources'],['rsi:'+kind])
        sr={**neutral,'signal':'Long','entry_sources':['sr:long_resistance_major','sr:short_resistance_minor']}
        result=combine_closed(dict(events=[],conflict=False),sr,row,100,cfg)
        self.assertEqual(result['entry_sources'],['sr:long_resistance_major'])
        result=combine_closed(dict(events=[dict(kind='ml_short_down',side='Short')],conflict=False),sr,row,100,cfg)
        self.assertIsNone(result['signal']);self.assertTrue(result['signal_diagnostic']['conflict'])

    def test_forming_cross_never_emits_closed_cross_emits_once_restart_baselines(self):
        cfg=Settings(strategy=RSI_STRATEGY);data=rows();state=RSIState(cfg.rsi)
        signals=[state.step(row,1800) for row in data]
        at=next(i for i,x in enumerate(signals) if i>220 and x['events'])
        end=data[at]['time']+1800
        with tempfile.TemporaryDirectory() as tmp:
            now=[end-5];service,exchange,_,_=fixtures.DelayedExecutionTests().fixture(tmp,now)
            service.pause();service.configure(TestnetSettings(**{**service.state['config'],'strategy':RSI_STRATEGY}))
            feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
            neutral=lambda *a,**k:dict(signal=None,entry_sources=[],levels={},conflict=False)
            with patch('thoth.closed_sr_rsi.IndicatorState.step',side_effect=neutral),patch('thoth.order_block.OrderBlockState.step',side_effect=AssertionError('No Order Block')):
                runner.scan_cycle=1
                first=observe(runner,'BTCUSDT',cfg,data[:at+1],now[0],'30');self.assertIsNone(first['signal'])
                now[0]=end+5;runner.scan_cycle+=1
                second=observe(runner,'BTCUSDT',cfg,data[:at+2],now[0],'30')
                self.assertEqual(second['signal'],signals[at]['signal']);self.assertEqual(second['closed_bar'],data[at]['time'])
                self.assertEqual(second['signal_diagnostic']['rsi_events'],[e['kind'] for e in signals[at]['events']])
                now[0]+=5;runner.scan_cycle+=1
                self.assertIsNone(observe(runner,'BTCUSDT',cfg,data[:at+2],now[0],'30')['signal'])
                runner.observation_session='restart';now[0]+=5;runner.scan_cycle+=1
                self.assertIsNone(observe(runner,'BTCUSDT',cfg,data[:at+2],now[0],'30')['signal'])

    def test_new_profile_delays_rsi_and_uses_no_minute_or_order_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[fixtures.BASE+5];service,exchange,_,_=fixtures.DelayedExecutionTests().fixture(tmp,now)
            service.pause();service.configure(TestnetSettings(**{**service.state['config'],'strategy':RSI_STRATEGY}));service.activate(service.confirmation)
            cfg=Settings(**{k:v for k,v in service.state['config'].items() if k in Settings.model_fields}).model_dump()
            report=dict(signal='Long',closed_bar=fixtures.BASE-1800,entry_sources=['rsi:ml_long_up'],signal_diagnostic=dict(rsi_events=['ml_long_up'],rsi=60,thresholds={'ml_long':56.68}))
            with patch.object(exchange,'candles',side_effect=AssertionError('No minute reads at execution')):
                service.apply_signals(cfg,{'BTCUSDT':dict(time=now[0],signal=None,mark=100,observation_channel='delayed_capture',delayed_capture=report)})
                self.assertFalse(exchange.orders);now[0]=fixtures.BASE+419;service.monitor();self.assertFalse(exchange.orders)
                now[0]+=1;service.monitor();self.assertEqual(len(exchange.orders),1)
                self.assertEqual(list(exchange.orders.values())[0]['side'],'Buy')
                self.assertEqual(service.journal.records()[0]['instrument']['thoth_delayed_30m']['sources'],['rsi:ml_long_up'])
            minute=Runner(FeedAdapter(service),confirmation_only=True);minute.cycle();self.assertEqual(minute.health['status'],'not_required')

    def test_runner_feed_closed_rsi_capture_and_scheduled_execution(self):
        cfg=Settings(strategy=RSI_STRATEGY);data=rows();state=RSIState(cfg.rsi)
        results=[state.step(row,1800) for row in data]
        at=next(i for i,x in enumerate(results) if i>220 and x['events']);end=data[at]['time']+1800
        with tempfile.TemporaryDirectory() as tmp:
            now=[end-5];service,exchange,_,_=fixtures.DelayedExecutionTests().fixture(tmp,now)
            service.pause();service.configure(TestnetSettings(**{**service.state['config'],'strategy':RSI_STRATEGY}));service.activate(service.confirmation)
            adapter=FeedAdapter(service);runner=Runner(adapter,fetch=adapter.fetch)
            def fetch(symbol,interval,limit=1000,end=None):
                self.assertEqual(interval,'30');current=int(now[0])//1800*1800
                return [row for row in data if row['time']<=current][-limit:]
            with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.closed_sr_rsi.IndicatorState.step',return_value=dict(signal=None,entry_sources=[],levels={},conflict=False)),patch('thoth.order_block.OrderBlockState.step',side_effect=AssertionError('No OB calculation in new strategy')),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                runner.cycle(paced=False);self.assertFalse(service.state['delayed_30m'])
                now[0]=end+5;runner.cycle(paced=False)
                self.assertIn('BTCUSDT',service.state['delayed_30m'],runner.health)
                saved=service.state['delayed_30m']['BTCUSDT'];self.assertTrue(all(x.startswith('rsi:') for x in saved['sources']))
                self.assertEqual(saved['due_at'],end+420);self.assertFalse(exchange.orders)
                now[0]=end+420;service.monitor();self.assertEqual(len(exchange.orders),1)
                expected='Buy' if results[at]['signal']=='Long' else 'Sell'
                self.assertEqual(next(iter(exchange.orders.values()))['side'],expected)
