import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thoth.indicator import confirmed_pivot, evaluate, extract_levels
from thoth.models import Settings
from thoth.engine import Engine
from thoth.runner import Runner


def fixture(count=400, start=0):
    rows=[]
    for i in range(count):
        c=100+math.sin(i*.17)*5+math.sin(i*.031)*10
        rows.append(dict(time=start+i*60,open=c,high=c+1,low=c-1,close=c,volume=10))
    return rows


class IndicatorTests(unittest.TestCase):
    def test_pivot_waits_and_equal_right_delays(self):
        rows=[dict(time=i*60,high=v) for i,v in enumerate([1,4,3,2,1])]
        self.assertTrue(math.isnan(confirmed_pivot(rows,2,2,'high',True)[0]))
        self.assertEqual(confirmed_pivot(rows,2,1,'high',True)[0],4)
        rows[2]['high']=4
        self.assertTrue(math.isnan(confirmed_pivot(rows,2,1,'high',True)[0]))
        self.assertEqual(confirmed_pivot(rows,3,1,'high',True)[0],4)

    def test_no_future_leak_and_forming_exclusion(self):
        rows=fixture()
        config=Settings()
        full=evaluate(rows,config,now=400*60)
        prefix=evaluate(rows[:300],config,now=300*60)
        self.assertEqual(full[:300],prefix)
        forming=evaluate(rows[:301],config,now=300*60+30)
        self.assertEqual(prefix,forming)
        for bar in full:
            for key,level in bar['levels'].items():
                period=config.pivot_long if key.startswith('long_') else config.pivot_short
                self.assertGreaterEqual(bar['time']-level['pivot_time'], period*60)

    def test_filter_and_signal_directions(self):
        rows=fixture(700)
        output=evaluate(rows,Settings(sma_enabled=True),now=700*60)
        self.assertTrue(any(b['signal']=='Long' for b in output))
        self.assertTrue(any(b['signal']=='Short' for b in output))
        for prev,bar in zip(output,output[1:]):
            if not bar['signal']:continue
            self.assertIsNotNone(bar['sma'])
            family='resistance' if bar['signal']=='Long' else 'support'
            if bar['signal']=='Long':self.assertGreater(bar['close'],bar['sma'])
            else:self.assertLess(bar['close'],bar['sma'])
            crossed=[]
            for key,level in bar['levels'].items():
                old=prev['levels'].get(key)
                if old and family in key:
                    crossed.append(prev['close']<=old['value'] and bar['close']>level['value'] if family=='resistance' else prev['close']>=old['value'] and bar['close']<level['value'])
            self.assertTrue(any(crossed))

    def test_source_level_extraction_ignores_initial_untyped_levels(self):
        levels=extract_levels([('MH',200,1,60),('MLL',100,2,120),('mLH',150,3,180),('MHL',120,4,240)])
        self.assertEqual(levels,{'support_major':{'value':120,'pivot_time':240},'resistance_minor':{'value':150,'pivot_time':180}})

    def test_missing_data_does_not_create_cross_gap_signal(self):
        rows=fixture()
        del rows[300]
        output=evaluate(rows,Settings(),now=400*60)
        self.assertIsNone(next(x for x in output if x['time']==301*60)['signal'])


class AutomaticPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine=Engine(Path(self.temp.name)/'paper.sqlite')
        self.cfg=Settings(automatic_paper=True)
        self.engine.configure(self.cfg)
        self.engine.control('start')

    def tearDown(self):self.temp.cleanup()

    def cycle(self,t,signal='Long',price=100):
        self.engine.market_cycle(self.cfg.model_dump(),{'BTCUSDT':dict(time=t,mark=price,signal=signal)})

    def test_first_bar_warms_up_then_new_signal_trades_once_after_restart(self):
        self.cycle(100)
        self.assertEqual(self.engine.snapshot()['positions'],[])
        self.cycle(160)
        self.assertEqual(len(self.engine.snapshot()['positions']),1)
        self.engine=Engine(self.engine.path)
        self.cycle(160,signal='Short')
        self.assertEqual(self.engine.snapshot()['positions'][0]['side'],'Long')
        self.cycle(220,signal='Short')
        state=self.engine.snapshot()
        self.assertEqual(state['positions'][0]['side'],'Short')
        self.assertEqual(len(state['trades']),1)

    def test_settings_race_and_disabled_mode_do_not_trade(self):
        self.cycle(100)
        self.engine.configure(Settings(automatic_paper=False))
        self.cycle(160)
        self.assertEqual(self.engine.snapshot()['positions'],[])

    def test_stops_run_before_entries_even_when_paused(self):
        self.cycle(100);self.cycle(160)
        self.engine.control('pause')
        self.cycle(220,price=97)
        self.assertEqual(self.engine.snapshot()['positions'],[])
        self.assertEqual(self.engine.snapshot()['trades'][0]['reason'],'Individual dollar stop')

    def test_runner_fetch_failure_does_not_seed_or_trade(self):
        def fail(*args):raise TimeoutError('unavailable')
        runner=Runner(self.engine,fetch=fail)
        runner.cycle(paced=False)
        self.assertEqual(runner.health['status'],'degraded')
        self.assertEqual(self.engine.snapshot()['positions'],[])
        self.assertEqual(self.engine.snapshot().get('cursors',{}),{})

    def test_runner_full_closed_candle_pipeline(self):
        rows=fixture(700)
        output=evaluate(rows,self.cfg,now=700*60)
        index=next(i for i,b in enumerate(output) if i>250 and b['signal'])
        active=[rows[:index+1]]
        runner=Runner(self.engine,fetch=lambda *_:active[0])
        with patch('thoth.runner.time.time',return_value=rows[index]['time']+30):
            runner.cycle(paced=False)  # seed latest closed candle
        active[0]=rows[:index+2]
        with patch('thoth.runner.time.time',return_value=rows[index+1]['time']+30):
            runner.cycle(paced=False)
        s=self.engine.snapshot()
        self.assertEqual(runner.health['status'],'ok')
        self.assertTrue(s['positions'])
        self.assertEqual(s['positions'][0]['side'],output[index]['signal'])
        self.assertEqual(s['positions'][0]['origin'],'automatic')


if __name__=='__main__':unittest.main()
