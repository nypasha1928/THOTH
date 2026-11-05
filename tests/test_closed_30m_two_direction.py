"""Closed 30m/1m sources and two-direction execution; synthetic data/exchange only."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.closed_two_direction import STRATEGY,observe,combine_closed
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.timeframes import interval_seconds
from thoth.order_block import select as real_select
from test_testnet import Exchange

BASE=1790000000//1800*1800

def rows(now,interval,count=200):
    duration=interval_seconds(interval);start=int(now)//duration*duration
    return [dict(time=start-(count-i-1)*duration,open=100,high=101,low=99,close=100,volume=0) for i in range(count)]


def event(now):
    bar=int(now)//1800*1800-1800
    return dict(id='closed-30:'+str(bar),time=now,closed_bar=bar,timeframe='30',signal='Long',sources=['htf:30:ob:bullish_formed'])

class ClosedThirtyTests(unittest.TestCase):
    def test_profile_major_only_fixed_timeframes_no_filters_and_risk_unchanged(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',major=False,minor=True,sma_enabled=True,sma_exit_enabled=True,stop_dollars=.4,max_positions=7,leverage=20)
        self.assertEqual(interval_seconds('30'),1800)
        self.assertEqual((cfg.timeframe,cfg.opposite_action,cfg.loss_streak_scope),('1','reverse','per_coin'))
        self.assertTrue(cfg.major);self.assertFalse(cfg.minor or cfg.sma_enabled or cfg.sma_exit_enabled or cfg.order_block.filter_breaks)
        self.assertEqual((cfg.stop_dollars,cfg.max_positions,cfg.leverage),(.4,7,20))
    def test_all_four_order_blocks_only_emit_after_selected_bar_closes_and_once(self):
        for interval in ('1','30'):
            duration=interval_seconds(interval)
            for kind,side in [('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')]:
                with self.subTest(interval=interval,kind=kind),tempfile.TemporaryDirectory() as tmp:
                    cfg=Settings(strategy=STRATEGY);r=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=interval=='1')
                    def selected(result,settings,**kw):
                        return real_select({**result,'events':[kind] if result['time']==BASE else []},settings,**kw)
                    def check(now,cycle):
                        r.scan_cycle=cycle;r.confirmation_symbol_cycles['BTCUSDT']=cycle
                        return observe(r,'BTCUSDT',cfg,rows(now,interval),now,interval)
                    with patch('thoth.closed_two_direction.select',side_effect=selected):
                        self.assertIsNone(check(BASE+duration-5,1)['signal'])
                        self.assertIsNone(check(BASE+duration-2,2)['signal'])
                        closed=check(BASE+duration+1,3)
                        self.assertEqual(closed['signal'],side);self.assertEqual(closed['entry_sources'],['ob:'+kind])
                        self.assertEqual(closed['closed_at'],BASE+duration)
                        self.assertIsNone(check(BASE+duration+3,4)['signal'])
    def test_major_long_and_short_term_breakouts_bounces_rejections_exclude_every_minor(self):
        cfg=Settings(strategy=STRATEGY);empty=dict(events=[],conflict=False)
        for term in ('long','short'):
            for grade in ('major','minor'):
                for level_type,side,mode,price,previous,low,high in [
                    ('resistance','Long','sr:',102,100,100,102),('support','Short','sr:',98,100,98,100),
                    ('support','Long','sr_bounce:',100.5,100,98,101),('resistance','Short','sr_rejection:',100,102,99,103)]:
                    key=term+'_'+level_type+'_'+grade;value=99 if level_type=='support' else 101
                    sr=dict(levels={key:dict(value=value,pivot_time=0)},signal=side if mode=='sr:' else None,entry_sources=['sr:'+key] if mode=='sr:' else [],conflict=False)
                    out=combine_closed(empty,sr,dict(close=price,low=low,high=high),previous,cfg)
                    self.assertEqual(out,dict(signal=side,entry_sources=[mode+key]) if grade=='major' else dict(signal=None,entry_sources=[]))
    def fixture(self,tmp,now):
        e=Exchange(lambda:now[0]);e.fixture_now=now
        s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        s.configure(TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,max_positions=1));s.activate(s.confirmation)
        cfg=Settings(**{k:v for k,v in s.state['config'].items() if k in Settings.model_fields}).model_dump()
        def send(side=None,parent=None,ended='auto'):
            data=dict(time=now[0],signal=None,mark=100,primary_events=parent or [],volume_signal=side,volume_sources=['htf:1:ob:bullish_formed'] if side else [],observation_channel='hour_permission' if parent else 'minute_signal')
            if ended is not None:data['minute_closed_at']=int(now[0])//60*60 if ended=='auto' else ended
            s.apply_signals(cfg,{'BTCUSDT':data})
        send();now[0]=BASE+6;send(parent=[event(now[0])])
        return s,e,cfg,send
    def fill(self,s,e):
        order=list(e.orders.values())[-1];e.fill(order['orderLinkId']);e.fixture_now[0]+=.1;s.monitor();return order
    def test_closed_minute_entry_reverse_third_exit_and_no_forming_or_prepermission_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Short',ended=None);self.assertFalse(e.orders)
            now[0]+=2;send('Short');self.assertFalse(e.orders) # Minute closed before permission.
            now[0]=BASE+61;send('Short');self.assertEqual(self.fill(s,e)['side'],'Sell')
            now[0]+=5;send('Long');self.assertEqual(len(e.orders),1) # Same closed bar predates observed fill.
            now[0]=BASE+121;send('Long');self.fill(s,e);self.assertEqual(self.fill(s,e)['side'],'Buy')
            now[0]=BASE+181;send('Short');self.assertTrue(list(e.orders.values())[-1]['reduceOnly']);self.assertFalse(s.state['reversals'])
            self.fill(s,e);self.assertFalse(s.state['owned'])
            now[0]=BASE+241;send('Long');self.assertEqual(len(e.orders),4)
    def test_new_closed_30m_permission_counts_open_position_as_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,send=self.fixture(tmp,now)
            now[0]=BASE+61;send('Long');self.fill(s,e)
            now[0]=BASE+121;send('Short');self.fill(s,e);self.fill(s,e)
            now[0]=BASE+1801;send(parent=[event(now[0])])
            gate=s.state['entry_gates']['BTCUSDT'];self.assertEqual(gate['primary_timeframe'],'30');self.assertEqual(len(gate['directions']),1)
            self.assertEqual(gate['directions'][0]['side'],'Short');self.assertEqual(len(e.orders),3)
            now[0]=BASE+1861;send('Long');self.fill(s,e);self.fill(s,e)
            now[0]=BASE+1921;send('Short');self.assertEqual(len(e.orders),6);self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
    def test_real_workers_wait_for_both_closes_and_enter_outside_old_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,send=self.fixture(tmp,now)
            s.state['entry_gates']={};s.state['cursors']={};s.save()
            now[0]=BASE+1799;feed=FeedAdapter(s);main=Runner(feed,fetch=feed.fetch);minute=Runner(feed,confirmation_only=True);calls=[]
            def fetch(symbol,interval,limit=1000,end=None):
                calls.append(interval);return rows(now[0],interval)[-limit:]
            event_types={BASE:'bullish_formed',BASE+2100:'bullish_formed',BASE+2160:'bearish_formed',BASE+2220:'bullish_formed'}
            def selected(result,settings,**kw):return real_select({**result,'events':[event_types[result['time']]] if result['time'] in event_types else []},settings,**kw)
            with patch.object(e,'candles',side_effect=fetch),patch('thoth.closed_two_direction.select',side_effect=selected),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                main.cycle(paced=False);self.assertFalse(s.state['entry_gates']['BTCUSDT']['phase']=='armed')
                now[0]=BASE+1801;main.cycle(paced=False)
                self.assertEqual(s.state['entry_gates']['BTCUSDT']['phase'],'armed');self.assertTrue(all(frame=='30' for frame in calls))
                now[0]=BASE+1802;minute.cycle();self.assertFalse(e.orders)
                now[0]=BASE+2099;minute.cycle();now[0]=BASE+2155;minute.cycle();self.assertFalse(e.orders)
                now[0]=BASE+2161;minute.cycle();self.assertEqual(len(e.orders),1,(minute.health,s.state['events'][-4:]));self.fill(s,e)
                self.assertIn('htf:30:ob:bullish_formed',s.state['owned']['BTCUSDT']['entry_sources'])
                now[0]=BASE+2215;minute.cycle();self.assertEqual(len(e.orders),1)
                now[0]=BASE+2221;minute.cycle();self.fill(s,e);self.fill(s,e);self.assertEqual(s.state['owned']['BTCUSDT']['side'],'Sell')
                now[0]=BASE+2281;minute.cycle();self.assertEqual(len(e.orders),4);self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
    def test_malformed_or_preclose_snapshot_is_not_a_confirmed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy=STRATEGY);r=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
            now=BASE+61;data=rows(now,'1')
            for bad in (data[:-1],data+[data[-1]],list(reversed(data))):
                with self.assertRaises(ValueError):observe(r,'BTCUSDT',cfg,bad,now,'1')
    def test_closed_minute_history_recovery_keeps_both_source_checkpoints_current(self):
        from thoth.database import connect
        with tempfile.TemporaryDirectory() as tmp:
            now=[BASE+5];s,e,cfg,send=self.fixture(tmp,now);settings=Settings(**cfg)
            feed=FeedAdapter(s);r=Runner(feed,confirmation_only=True)
            def row(i):return dict(time=BASE+i*60,open=100,high=101,low=99,close=100,volume=0)
            now[0]=BASE+199*60+5;r.confirmation_symbol_cycles['BTCUSDT']=1
            observe(r,'BTCUSDT',settings,[row(i) for i in range(200)],now[0],'1')
            now[0]=BASE+11000*60+5;calls=[];complete=False
            def fetch(symbol,interval,limit=1000,end=None):
                self.assertEqual(interval,'1');calls.append(end)
                last=(end//1000-BASE)//60 if end is not None else int((now[0]-BASE)//60)
                return [row(i) for i in range(last-limit+1,last+1)]
            with patch.object(e,'candles',side_effect=fetch):
                for cycle in range(2,9):
                    before=len(calls);data=feed.staged_minute_inputs('BTCUSDT');self.assertLessEqual(len(calls)-before,3)
                    r.confirmation_symbol_cycles['BTCUSDT']=cycle
                    try:
                        out=observe(r,'BTCUSDT',settings,data,now[0],'1');complete=True;break
                    except ValueError as exc:self.assertIn('gap',str(exc))
                self.assertTrue(complete);self.assertIsNone(out['signal']);self.assertFalse(e.orders)
                with connect(r.history_path) as db:
                    count=db.execute("SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT' AND interval='1'").fetchone()[0]
                self.assertEqual(count,5001)
                before=len(calls);now[0]+=5;feed.staged_minute_inputs('BTCUSDT');self.assertEqual(len(calls)-before,1)
    def test_major_sr_does_not_wait_for_order_block_atr_warmup(self):
        from thoth.indicator_stream import IndicatorState
        actual=IndicatorState.step
        def step(state,row):
            result=actual(state,row)
            return {**result,'signal':'Long','entry_sources':['sr:short_resistance_major'],'conflict':False} if row['time']==BASE else result
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy=STRATEGY);r=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
            with patch('thoth.closed_two_direction.IndicatorState.step',step):
                r.confirmation_symbol_cycles['BTCUSDT']=1
                self.assertIsNone(observe(r,'BTCUSDT',cfg,rows(BASE+55,'1',count=6),BASE+55,'1')['signal'])
                r.confirmation_symbol_cycles['BTCUSDT']=2
                result=observe(r,'BTCUSDT',cfg,rows(BASE+61,'1',count=6),BASE+61,'1')
            self.assertEqual(result['signal'],'Long');self.assertEqual(result['entry_sources'],['sr:short_resistance_major'])
    def test_closed_parent_permission_has_no_old_hourly_window_restriction(self):
        from thoth.closed_two_direction import arm
        from thoth.two_direction_gate import new_gate
        gate=new_gate();now=BASE+2041 # :34:01, outside both old entry windows.
        self.assertTrue(arm(gate,[event(now)],now,True,None))
        self.assertEqual(gate['phase'],'armed');self.assertEqual(gate['primary_timeframe'],'30')
