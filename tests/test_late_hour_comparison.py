"""Only synthetic candles and local journals; no exchange orders/network."""
import copy
import json
import queue
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings
from thoth.late_hour_comparison import LateHourComparison,ComparisonSettings,SourceState,decision,STRATEGY

BAR=360000.

def history(close=100.5,**extra):
    rows=[dict(time=BAR-(30-i)*3600,open=100,high=101,low=99,close=100,volume=1) for i in range(30)]
    rows.append(dict(time=BAR,open=100,high=101,low=99,close=close,volume=1)|extra)
    return rows

def raw(kind='bullish_formed',side='Long',edge=100):
    return dict(ready=True,signal=side,conflict=False,events=[dict(kind=kind,side=side)],entry_sources=['ob:'+kind],
        zones=dict(demand=[dict(high=edge,low=99,time=BAR-3600)],supply=[dict(high=101,low=edge,time=BAR-3600)]),
        broken_supply=[dict(high=edge,low=99,time=BAR-3600)],broken_demand=[dict(high=101,low=edge,time=BAR-3600)])

class LastFiveTests(unittest.TestCase):
    def setUp(self):self.cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT'],pivot_long=1,pivot_short=1)
    def sample(self,rows=None,ob=None,levels=None,at=BAR+3300,processed=None):
        rows=rows or history();state=SourceState(self.cfg);state.advance(rows,at)
        state.sr_report['levels']=levels or {}
        with patch('thoth.late_hour_comparison.select',return_value=ob or dict(ready=True,signal=None,conflict=False,events=[],entry_sources=[])):
            return decision(rows,self.cfg,at,at if processed is None else processed,state)
    def test_default_on_persisted_off_and_no_live_configuration_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            live=Path(tmp)/'live.sqlite';live.write_bytes(b'unchanged sentinel')
            observer=LateHourComparison(Path(tmp)/'comparison.sqlite',live)
            self.assertTrue(observer.report()['enabled'])
            observer.configure(ComparisonSettings(enabled=False))
            self.assertFalse(LateHourComparison(observer.path,live).enabled)
            self.assertEqual(live.read_bytes(),b'unchanged sentinel')
            self.assertFalse(hasattr(observer,'client'));self.assertFalse(hasattr(observer,'submit_order'))
    def test_all_four_ob_types_both_directions_and_window_edges(self):
        for kind,side in [('bullish_formed','Long'),('supply_breakout','Long'),('bearish_formed','Short'),('demand_breakdown','Short')]:
            rows=history(close=100.5 if side=='Long' else 99.5)
            for seconds in (3300,3599):
                with self.subTest(kind=kind,seconds=seconds):
                    before=copy.deepcopy(rows)
                    result=self.sample(rows,raw(kind,side),at=BAR+seconds)
                    self.assertEqual(result['signal'],side,result)
                    self.assertEqual(result['passed_sources'],['ob:'+kind]);self.assertEqual(rows,before)
        for at,processed in [(BAR+3299,BAR+3299),(BAR+3599,BAR+3600),(BAR+3300,BAR+3331)]:
            self.assertIsNone(self.sample(ob=raw(),at=at,processed=processed)['signal'])
    def test_major_only_sr_and_source_specific_filters_conflicts(self):
        level=lambda value:dict(value=value,pivot_time=BAR-3600)
        result=self.sample(levels={'long_resistance_major':level(100),'short_resistance_minor':level(100)})
        self.assertEqual(result['signal'],'Long');self.assertEqual(result['passed_sources'],['sr:long_resistance_major'])
        # OB too far away, but independently qualifying Major S/R still passes.
        result=self.sample(ob=raw(edge=98),levels={'long_resistance_major':level(100)})
        self.assertEqual(result['passed_sources'],['sr:long_resistance_major'])
        self.assertTrue(any('entry beyond' in ' '.join(c['reasons']) for c in result['source_checks']))
        self.assertIsNone(self.sample(ob=raw('bearish_formed','Short'),levels={'long_resistance_major':level(100)})['signal'])
    def test_wick_clearance_distance_and_no_ob_thickness_filter(self):
        self.assertIsNone(self.sample(history(close=100.1),raw())['signal']) # Upper wick 45%.
        self.assertIsNone(self.sample(ob=raw(edge=100.49))['signal']) # Almost on boundary.
        self.assertIsNone(self.sample(ob=raw(edge=98))['signal']) # Too far.
        thin=raw();thin['zones']['demand'][0]['low']=99.99999
        self.assertEqual(self.sample(ob=thin)['signal'],'Long')
        # ATR=2, so 0.05 and 0.50 ATR are inclusive, using a fixed snapshot ATR for precision.
        from thoth.signal_stretch import snapshot
        for edge in (100.4,99.5):
            m=snapshot(history(),'Long',['ob:bullish_formed'],ob=raw(edge=edge));m['atr14']=2
            with patch('thoth.late_hour_comparison.snapshot',return_value=m):self.assertEqual(self.sample(ob=raw(edge=edge))['signal'],'Long')
    def test_nonblocking_bounded_input_and_wrong_profile_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            o=LateHourComparison(Path(tmp)/'comp.sqlite',Path(tmp)/'live.sqlite')
            o.submit('BTCUSDT',self.cfg,history(),BAR+3299);self.assertTrue(o.inputs.empty())
            o.submit('BTCUSDT',Settings(strategy='order_block_sr_1h'),history(),BAR+3300);self.assertTrue(o.inputs.empty())
            for _ in range(65):o.submit('BTCUSDT',self.cfg,history(),BAR+3300)
            self.assertEqual(o.inputs.qsize(),64);self.assertEqual(o.dropped,1)
            o.configure(ComparisonSettings(enabled=False));o.submit('BTCUSDT',self.cfg,history(),BAR+3301)
            self.assertEqual(o.inputs.qsize(),64)
    def test_first_pass_persists_after_rejection_restart_and_closed_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+3300];path=Path(tmp)/'comparison.sqlite';live=Path(tmp)/'live.sqlite'
            o=LateHourComparison(path,live,clock=lambda:now[0])
            result=self.sample(ob=raw())
            with patch('thoth.late_hour_comparison.decision',return_value=result):o.process(('BTCUSDT',self.cfg,history(),now[0]))
            now[0]+=5
            rejected={**result,'signal':None,'reason':'Skip: opposing wick above 30%','observed_at':now[0]}
            with patch('thoth.late_hour_comparison.decision',return_value=rejected):o.process(('BTCUSDT',self.cfg,history(),now[0]))
            saved=o.report()['items'][0];self.assertEqual(saved['signal'],'Long');self.assertIn('wick',saved['latest']['reason'])
            o=LateHourComparison(path,live,clock=lambda:now[0]);now[0]=BAR+3601
            data=history()+[dict(time=BAR+3600,open=100.5,high=101,low=100,close=100.5,volume=1)]
            with patch('thoth.late_hour_comparison.select',return_value=raw()):o.process(('BTCUSDT',self.cfg,data,now[0]))
            row=o.report()['items'][0];self.assertTrue(row['closed']['same_direction']);self.assertEqual(row['closed']['same_sources'],['ob:bullish_formed'])
            # Only orders matching exact symbol, normal profile and signal-hour close are linked.
            with sqlite3.connect(live.with_suffix('.execution.sqlite')) as db:
                db.execute('CREATE TABLE execution(payload TEXT,phase TEXT,instrument TEXT,result TEXT)')
                for symbol,stamp,profile in [('BTCUSDT',BAR+3600,STRATEGY),('ETHUSDT',BAR+3600,STRATEGY),('BTCUSDT',BAR,STRATEGY),('BTCUSDT',BAR+3600,'order_block_sr_1h_inverted_close')]:
                    db.execute('INSERT INTO execution VALUES(?,?,?,?)',(json.dumps(dict(symbol=symbol,side='Buy',reduceOnly=False,orderLinkId='fixture')), 'protected',json.dumps(dict(thoth_strategy=profile,thoth_entry_diagnostics=dict(signal_bar_closed_at=stamp))),json.dumps(dict(entry_timing=dict(fill_price=100.6)))))
            row=o.report()['items'][0];self.assertEqual(len(row['live_orders']),1);self.assertEqual(row['live_orders'][0]['fill_price'],100.6)
    def test_native_replay_does_not_mutate_closed_state_with_forming_preview(self):
        from test_closed_inverted_ob_sr import history as actual,NOW
        data=actual();cfg=self.cfg
        state=SourceState(cfg);state.advance(data,NOW-10)
        before=state.ob.checkpoint()
        decision(data,cfg,NOW-10,NOW-10,state)
        self.assertEqual(state.ob.checkpoint(),before)
