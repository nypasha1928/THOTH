import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from thoth.database import connect
from thoth.models import Settings
from thoth.trend_pullback import ResearchSettings,TrendPullbackObservation,setup,advance,hourly


def bars():
    out=[]
    for i in range(401):
        c=90+i*.025;out.append(dict(time=i*1800,open=c,close=c,high=c+.15,low=c-.15,volume=100))
    out[-1].update(open=100,close=100.01,high=100.2,low=99.9)
    return out

class Rules(unittest.TestCase):
    def test_hourly_pairs_and_no_incomplete_future_hour(self):
        rows=bars();hours=hourly(rows)
        self.assertEqual(len(hours),200);self.assertEqual(hours[-1]['time']+3600,rows[-1]['time'])
        self.assertEqual(hours[-1]['close'],rows[-2]['close'])
        self.assertIsNone(setup(rows[:300],100,Settings(),ResearchSettings())['signal'])
    def test_long_short_confirmed_pullback_prior_levels_sizing_and_distance(self):
        for side in ('Long','Short'):
            rows=bars();key='long_support_major'
            if side=='Short':
                rows=[{**r,'open':200-r['open'],'close':200-r['close'],'high':200-r['low'],'low':200-r['high']} for r in rows];key='short_resistance_major'
            levels={key:dict(value=100,pivot_time=0),'long_support_minor':dict(value=100,pivot_time=0)}
            with patch('thoth.trend_pullback.IndicatorState.step',return_value={'levels':levels}) as step:
                result=setup(rows,rows[-1]['close'],Settings(),ResearchSettings())
                self.assertEqual(result['signal'],side,result)
                self.assertEqual(step.call_count,len(rows)-1)  # deciding candle cannot create its own reference
                self.assertTrue(result['source'].endswith('_major'));self.assertAlmostEqual(abs(result['entry']-result['stop'])*result['qty'],.4)
                self.assertIsNone(setup(rows,102 if side=='Long' else 98,Settings(),ResearchSettings())['signal'])
                self.assertIsNone(setup(rows,rows[-1]['close'],Settings(),ResearchSettings(max_notional_usdt=1))['signal'])
    def test_equal_hourly_sma_and_no_bounce_do_not_enter(self):
        rows=[{**r,'open':100,'close':100,'low':99,'high':101} for r in bars()]
        self.assertIsNone(setup(rows,100,Settings(),ResearchSettings())['signal'])
        with patch('thoth.trend_pullback.IndicatorState.step',return_value={'levels':{}}):
            self.assertIsNone(setup(bars(),100,Settings(),ResearchSettings())['signal'])
    def position(self,side='Long'):
        return dict(status='open',side=side,entry=100,qty=1,risk=1,stop=99 if side=='Long' else 101,opened_at=0,last_sample=0)
    def test_sampled_excursions_and_r_trailing_long_and_short(self):
        rules=ResearchSettings(fee_bps=0,slippage_bps=0)
        for side,sign in [('Long',1),('Short',-1)]:
            p=self.position(side);p=advance(p,100-sign*.25,1,rules)
            p=advance(p,100+sign*1.625,2,rules)
            self.assertTrue(p['breakeven_armed'] and p['trailing_armed'])
            p=advance(p,100+sign*1.125,3,rules)
            self.assertEqual(p['exit_reason'],'Simulated trailing');self.assertEqual(p['net_r'],1.125)
            self.assertEqual(p['mae_r'],-.25);self.assertEqual(p['mfe_r'],1.625)
            self.assertEqual(p['pre_profit_mae_r'],-.25);self.assertEqual(p['first_positive_at'],2)
    def test_fixed_breakeven_costs_and_structural_stop_gap(self):
        rules=ResearchSettings(fee_bps=5.5,slippage_bps=2)
        p=advance(self.position(),101.1,1,rules);p=advance(p,100.2,2,rules)
        self.assertEqual(p['exit_reason'],'Simulated breakeven');self.assertLess(p['net_usdt'],.2)
        self.assertGreater(p['fees_usdt'],0)
        p=advance(self.position(),98.5,3,rules)
        self.assertEqual(p['exit_reason'],'Structural stop');self.assertLess(p['net_r'],-1)
        self.assertEqual(advance(self.position(),100,4,rules,'Scheduled daily cutoff')['exit_reason'],'Scheduled daily cutoff')
    def test_settings_reject_invalid_assumptions(self):
        for update in ({'risk_usdt':0},{'fee_bps':float('nan')},{'breakeven_floor_r':1},{'slippage_bps':-1}):
            with self.assertRaises(ValueError):ResearchSettings(**update)

class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=[401*1800-5];self.path=Path(self.tmp.name)/'research.sqlite';self.history=Path(self.tmp.name)/'candles.sqlite'
        self.cfg=Settings(strategy='sr_rsi_30m_delayed',symbols=['BTCUSDT']);self.o=TrendPullbackObservation(self.path,clock=lambda:self.now[0])
        with connect(self.history) as db:
            db.execute('CREATE TABLE candles(symbol TEXT,interval TEXT,time INTEGER,payload TEXT)')
            for row in bars():db.execute('INSERT INTO candles VALUES(?,?,?,?)',('BTCUSDT','30',row['time'],json.dumps(row)))
    def tearDown(self):self.tmp.cleanup()
    def send(self,price=100):
        row=dict(time=int(self.now[0])//1800*1800,open=100,close=price,high=max(100,price)+1,low=min(100,price)-1,volume=100)
        self.o.process(('BTCUSDT',self.cfg,str(self.history),row,self.now[0]))
    def open(self):
        self.send();self.now[0]+=10
        decision=dict(signal='Long',reason='fixture entry',entry=100,qty=1,risk=1,stop=99)
        with patch('thoth.trend_pullback.setup',return_value=decision):self.send()
        return self.o.report()['items'][0]
    def test_live_pullback_profile_also_collects_virtual_observations(self):
        self.cfg=Settings(strategy='trend_major_pullback_30m_close',symbols=['BTCUSDT'])
        self.assertEqual(self.open()['position']['status'],'open')
        self.assertEqual(self.o.report()['items'][0]['live_strategy'],'trend_major_pullback_30m_close')

    def test_baseline_delayed_close_no_duplicate_and_persistent_result(self):
        record=self.open();self.assertEqual(record['position']['status'],'open')
        self.now[0]+=5;self.send(101.1);self.now[0]+=5;self.send(100.2)
        report=self.o.report();self.assertEqual(report['summary']['closed'],1);self.assertEqual(report['total'],2)
        result=report['items'][0]['position'];self.assertEqual(result['exit_reason'],'Simulated breakeven')
        recovered=TrendPullbackObservation(self.path,clock=lambda:self.now[0]);self.assertEqual(recovered.report()['summary']['closed'],1)
    def test_gap_restart_and_disable_are_unverified_without_invented_pnl(self):
        self.open();self.now[0]+=121;self.send(90)
        result=self.o.report()['items'][0]['position'];self.assertEqual(result['status'],'interrupted');self.assertNotIn('net_usdt',result)
        self.o=TrendPullbackObservation(self.path,clock=lambda:self.now[0]);self.assertEqual(self.o.report()['summary']['interrupted'],1)
    def test_restart_interrupts_open_and_settings_are_isolated(self):
        self.open();self.o=TrendPullbackObservation(self.path,clock=lambda:self.now[0])
        self.assertEqual(self.o.report()['summary']['interrupted'],1)
        before=self.cfg.model_dump();self.o.configure(ResearchSettings(enabled=False));self.assertEqual(before,self.cfg.model_dump())
        self.assertFalse(self.o.report()['enabled'])
    def test_bounded_queue_and_old_samples_rejected(self):
        latest=dict(time=0,open=100,close=100,high=101,low=99,volume=1)
        for _ in range(70):self.o.submit('BTCUSDT',self.cfg,self.history,latest,self.now[0])
        self.assertEqual(self.o.inputs.qsize(),64);self.assertEqual(self.o.dropped,6)
        with self.assertRaisesRegex(ValueError,'too late'):
            self.o.process(('BTCUSDT',self.cfg,str(self.history),latest,self.now[0]-31))

    def test_scheduled_entry_block_and_worker_exception_cannot_reach_live(self):
        self.send();self.now[0]+=10
        with patch('thoth.trend_pullback.schedule_reason',return_value='Scheduled entry pause'):
            self.send()
        self.assertEqual(self.o.report()['items'][0]['reason'],'Scheduled entry pause')
        self.assertEqual(self.o.report()['summary']['open'],0)
        from thoth.runner import Runner
        class FailingObserver:
            def submit(self,*args):raise RuntimeError('Synthetic research failure')
        runner=object.__new__(Runner);runner.trend_pullback_observer=FailingObserver();runner.history_path=str(self.history)
        # The source runner catches research failures; no client is even available here.
        runner.observe_trend_pullback('BTCUSDT',self.cfg,bars(),self.now[0])
