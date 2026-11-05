"""Synthetic sequence and source tests; no exchange network requests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from thoth.models import Settings,TestnetSettings
from thoth.two_direction_gate import STRATEGY,new_gate,arm,decision,reserve,finish,hourly_signal
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from test_testnet import Exchange

BAR=216000

def event(now,side='Long'):
    from thoth.volume_delta_windows import window_start
    start=window_start(now)
    return dict(id='window:'+str(start),time=now,window_start=start,signal=side,timeframe='60',sources=['htf:60:ob:bullish_formed'])

class TwoDirectionTests(unittest.TestCase):
    def test_settings_and_direction_state_no_extra_entry(self):
        cfg=Settings(strategy=STRATEGY,timeframe='D',sma_exit_enabled=True,major=False,opposite_action='close_only')
        self.assertEqual(cfg.timeframe,'1');self.assertEqual(cfg.opposite_action,'reverse');self.assertTrue(cfg.major)
        self.assertFalse(cfg.sma_enabled or cfg.sma_exit_enabled or cfg.minor or cfg.order_block.filter_breaks)
        gate=new_gate();now=BAR+1681;arm(gate,[event(now)],now,True,None)
        self.assertIsNone(decision(gate,'Short',now,now))
        self.assertEqual(decision(gate,'Short',now+1,now+1),'entry')
        reserve(gate,'Short','first',gate['arm_id'])
        self.assertIsNone(decision(gate,'Short',now+2,now+2))
        self.assertEqual(decision(gate,'Long',now+3,now+3),'reverse')
        reserve(gate,'Long','second',gate['arm_id'])
        self.assertEqual(decision(gate,'Short',now+4,now+4),'exit')
        with self.assertRaises(ValueError):reserve(gate,'Short','third',gate['arm_id'])
        finish(gate);self.assertIsNone(decision(gate,'Short',now+5,now+5))
        self.assertFalse(arm(gate,[event(now+6)],now+6,True,None))
    def test_replacement_keeps_open_position_as_first_direction(self):
        gate=new_gate();first=BAR+1681;arm(gate,[event(first)],first,True,None)
        reserve(gate,'Long','first',gate['arm_id']);reserve(gate,'Short','second',gate['arm_id'])
        later=BAR+3481;owner=dict(side='Sell',link='second')
        self.assertTrue(arm(gate,[event(later)],later,True,owner))
        self.assertEqual(gate['arm_id'],2);self.assertEqual(gate['directions'],[dict(side='Short',link='second')])
        self.assertEqual(decision(gate,'Long',later+1,later+1),'reverse')
        self.assertFalse(arm(gate,[event(later+5)],later+5,True,owner))
    def fixture(self,tmp,now,max_positions=1):
        e=Exchange(lambda:now[0]);e.fixture_now=now;s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        s.configure(TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT','ETHUSDT'],dedicated_account=True,daily_loss_pct=90,max_positions=max_positions))
        s.activate(s.confirmation);cfg=Settings(**{k:v for k,v in s.state['config'].items() if k in Settings.model_fields}).model_dump()
        def send(side=None,primary=None,symbol='BTCUSDT'):
            s.apply_signals(cfg,{symbol:dict(time=now[0],signal=None,mark=100,primary_events=primary or [],volume_signal=side,volume_sources=['htf:1:sr:long_resistance_major'] if side else [])})
        send();now[0]=BAR+1681;send(primary=[event(now[0])])
        return s,e,cfg,send
    def fill_latest(self,s,e):
        order=list(e.orders.values())[-1];e.fill(order['orderLinkId']);e.fixture_now[0]+=.1;s.monitor();return order
    def test_outside_windows_first_reverse_then_third_close_only_and_no_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]=BAR+2001;send('Short');self.assertEqual(len(e.orders),1)
            self.assertEqual(self.fill_latest(s,e)['side'],'Sell')
            self.assertEqual(s.state['owned']['BTCUSDT']['entry_timeframe'],'1')
            now[0]+=5;send('Short');self.assertEqual(len(e.orders),1)
            now[0]+=5;send('Long');self.assertEqual(len(e.orders),2);self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
            self.fill_latest(s,e);self.assertEqual(len(e.orders),3)
            self.assertEqual(self.fill_latest(s,e)['side'],'Buy')
            now[0]+=5;send('Short');self.assertEqual(len(e.orders),4)
            self.assertTrue(list(e.orders.values())[-1]['reduceOnly']);self.assertFalse(s.state['reversals'])
            self.fill_latest(s,e);self.assertFalse(s.state['owned']);self.assertEqual(s.state['entry_gates']['BTCUSDT']['phase'],'waiting')
            for side in ('Long','Short','Long'):
                now[0]+=5;send(side)
            self.assertEqual(len(e.orders),4)
            self.assertEqual(len([o for o in e.orders.values() if not o['reduceOnly']]),2)
    def test_new_hour_restarts_active_sequence_without_closing_or_adding(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Long');self.fill_latest(s,e)
            now[0]=BAR+3481;send(primary=[event(now[0])])
            gate=s.state['entry_gates']['BTCUSDT'];self.assertEqual(gate['arm_id'],2);self.assertEqual(len(gate['directions']),1)
            self.assertEqual(len(e.orders),1)
            now[0]+=5;send('Short');self.fill_latest(s,e);self.fill_latest(s,e)
            now[0]+=5;send('Long');self.assertEqual(len(e.orders),4);self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
            self.assertFalse(s.state['reversals'])
    def test_restart_does_not_reset_direction_cap_and_paused_third_exit_still_managed(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Long');self.fill_latest(s,e);now[0]+=5;send('Short');self.fill_latest(s,e);self.fill_latest(s,e)
            recovered=TestnetSupervisor(s.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            recovered.activate(recovered.confirmation,recovery=True)
            self.assertFalse(recovered.state['entries']);self.assertEqual(len(recovered.state['entry_gates']['BTCUSDT']['directions']),2)
            now[0]+=5
            recovered.apply_signals(cfg,{'BTCUSDT':dict(time=now[0],signal=None,mark=100,primary_events=[],volume_signal='Long',volume_sources=['htf:1:ob:bullish_formed'])})
            self.assertEqual(len(e.orders),4);self.assertTrue(list(e.orders.values())[-1]['reduceOnly']);self.assertFalse(recovered.state['reversals'])
    def test_hourly_sr_breakout_bounce_rejection_and_major_only(self):
        cfg=Settings(strategy=STRATEGY)
        for key,previous,close,low,high,side,prefix in [
            ('long_resistance_major',100,102,100,102,'Long','sr:'),
            ('long_support_major',100,100.5,98,101,'Long','sr_bounce:'),
            ('long_resistance_major',102,100,99,102,'Short','sr_rejection:'),
            ('long_support_major',100,98,98,100,'Short','sr:')]:
            value=99 if 'support' in key else 101
            levels={key:dict(value=value,pivot_time=0),'short_support_minor':dict(value=1000,pivot_time=0)}
            state=SimpleNamespace(advance=lambda *a:None,ob=MagicMock(s={'last_time':BAR-3600}),sr_report={'levels':levels})
            rows=[dict(time=BAR-3600,close=previous),dict(time=BAR,close=close,low=low,high=high)]
            with patch('thoth.order_block.OrderBlockState'),patch('thoth.order_block.select',return_value=dict(ready=True,events=[],conflict=False)):
                out=hourly_signal(state,rows,cfg,BAR+1681)
            self.assertEqual(out,dict(signal=side,entry_sources=[prefix+key]))
    def test_minute_sr_accepts_breakout_and_bounce_independently(self):
        for kind in ('breakout','bounce'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=STRATEGY);now=BAR+2001;start=int(now)//60*60
                r=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                key='long_resistance_major' if kind=='breakout' else 'long_support_major'
                level=101 if kind=='breakout' else 99
                r.analysis=lambda *a,**k:([dict(time=start-60,levels={key:dict(value=level,pivot_time=0)})],200)
                rows=[dict(time=start-(199-i)*60,open=100,high=100,low=100,close=100,volume=0) for i in range(200)]
                r.confirmation_symbol_cycles['BTCUSDT']=1
                self.assertIsNone(r.order_block_sr_branch('BTCUSDT',cfg,rows,now)['signal'])
                rows[-1].update(close=102,high=102,low=100 if kind=='breakout' else 98)
                r.confirmation_symbol_cycles['BTCUSDT']=2
                out=r.order_block_sr_branch('BTCUSDT',cfg,rows,now+5)
                self.assertEqual(out['signal'],'Long');self.assertEqual(out['entry_sources'],[('sr:' if kind=='breakout' else 'sr_bounce:')+key])
    def test_real_hour_and_minute_workers_intrabar_breakout_rejection_bounce(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now);s.state['entry_gates']={};s.state['cursors']={};s.save()
            now[0]=BAR+1679;price=[100.];bars={};calls=[]
            feed=FeedAdapter(s);main=Runner(feed,fetch=feed.fetch);minute=Runner(feed,confirmation_only=True)
            def fetch(symbol,interval,limit=1000,**kw):
                calls.append(interval);duration=3600 if interval=='60' else 60;start=int(now[0])//duration*duration
                if interval=='60':return [dict(time=start-(59-i)*3600,open=100,close=100,low=99,high=101,volume=0) for i in range(60)][-limit:]
                current=bars.setdefault(start,dict(time=start,open=100,close=100,high=100,low=100,volume=0))
                current.update(close=price[0],low=min(current['low'],price[0]),high=max(current['high'],price[0]))
                return [dict(bars.get(start-(199-i)*60,dict(time=start-(199-i)*60,open=100,close=100,low=100,high=100,volume=0))) for i in range(200)][-limit:]
            levels={'long_resistance_major':dict(value=101,pivot_time=0),'short_support_major':dict(value=97,pivot_time=0)}
            minute.analysis=lambda symbol,source,rows,stamp,**kw:([dict(time=int(stamp)//60*60-60,levels=levels)],200)
            with patch.object(e,'candles',side_effect=fetch),patch('thoth.two_direction_gate.hourly_signal',return_value=dict(signal='Long',entry_sources=['ob:bullish_formed'])),patch.object(minute,'order_block_observation',return_value=dict(signal=None,entry_sources=[],order_block_conflict=False)),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                main.cycle(paced=False);now[0]=BAR+1681;main.cycle(paced=False)
                self.assertIn('BTCUSDT',s.state['entry_gates'],(main.health,s.state['events'][-5:]));self.assertEqual(s.state['entry_gates']['BTCUSDT']['phase'],'armed');self.assertFalse(e.orders)
                self.assertTrue(all(interval=='60' for interval in calls));calls.clear()
                # Both workers remain independent: the main worker still scans hours while armed.
                now[0]+=1;main.cycle(paced=False);self.assertTrue(all(interval=='60' for interval in calls))
                now[0]=BAR+2001;minute.cycle() # fresh minute baseline, outside entry window
                now[0]+=5;price[0]=102;minute.cycle();self.assertEqual(len(e.orders),1,s.state['events'][-5:]);self.fill_latest(s,e)
                self.assertIn('htf:1:sr:long_resistance_major',s.state['owned']['BTCUSDT']['entry_sources'])
                self.assertEqual(minute.health['symbols']['BTCUSDT']['last_detected_signal']['direction'],'Long')
                now[0]+=5;price[0]=99;minute.cycle();self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
                self.fill_latest(s,e);self.fill_latest(s,e)
                self.assertEqual(s.state['owned']['BTCUSDT']['side'],'Sell')
                self.assertIn('htf:1:sr_rejection:long_resistance_major',s.state['owned']['BTCUSDT']['entry_sources'])
                now[0]+=5;price[0]=96;minute.cycle();self.assertEqual(len(e.orders),3)
                now[0]+=5;price[0]=101;minute.cycle();self.assertEqual(len(e.orders),4,s.state['events'][-5:])
                self.assertFalse(s.state['reversals']);self.fill_latest(s,e)
                self.assertFalse(s.state['owned']);self.assertEqual(s.state['entry_gates']['BTCUSDT']['phase'],'waiting')
    def test_third_signal_during_unfilled_second_entry_cannot_consume_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Long');self.fill_latest(s,e)
            now[0]+=5;send('Short');self.fill_latest(s,e) # second entry submitted, not filled yet
            self.assertEqual(len(e.orders),3)
            now[0]+=5;send('Long')
            self.assertEqual(s.state['entry_gates']['BTCUSDT']['phase'],'armed');self.assertEqual(len(e.orders),3)
            self.fill_latest(s,e);now[0]+=5;send('Long')
            self.assertEqual(len(e.orders),4);self.assertTrue(list(e.orders.values())[-1]['reduceOnly'])
    def test_definitive_rejection_releases_slot_and_capacity_blocks_do_not_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            original=e.request
            from thoth.bybit_testnet import BybitError
            def request(method,path,params,private=False):
                if path=='/v5/order/create':raise BybitError('fixture rejected',code=110007)
                return original(method,path,params,private)
            now[0]+=2
            with patch.object(e,'request',side_effect=request):send('Long')
            self.assertEqual(s.state['entry_gates']['BTCUSDT']['directions'],[])
            self.assertFalse(e.orders)
            now[0]+=5;send('Long');self.fill_latest(s,e)
            self.assertEqual(len(s.state['entry_gates']['BTCUSDT']['directions']),1)
            # A distinct coin cannot exceed Max Positions=1, and takes no direction slot.
            now[0]+=5;send(primary=[event(now[0])],symbol='ETHUSDT')
            now[0]+=5;send('Short',symbol='ETHUSDT')
            self.assertFalse(s.state['entry_gates']['ETHUSDT']['directions']);self.assertEqual(len(e.orders),1)
    def test_legacy_gate_schema_can_arm_new_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            from thoth.staged_entry import new_gate as legacy_gate
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            s.state['entry_gates']['BTCUSDT']=legacy_gate();s.save()
            now[0]+=1;send(primary=[{**event(now[0]),'id':'fresh'}])
            self.assertEqual(s.state['entry_gates']['BTCUSDT']['phase'],'armed')
            self.assertEqual(s.state['entry_gates']['BTCUSDT']['directions'],[])
    def test_window_rollover_one_permission_and_stale_hour_sample_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(strategy=STRATEGY,symbols=['BTCUSDT']);r=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')))
            def packet(now,sampled=None):
                start=int(now)//3600*3600
                rows=[dict(time=start-(59-i)*3600,open=100,close=100,low=99,high=101,volume=0) for i in range(60)]
                return dict(primary={'60':rows},sample_received_at=now if sampled is None else sampled)
            raw=dict(signal='Long',entry_sources=['ob:bullish_formed'])
            with patch('thoth.two_direction_gate.hourly_signal',return_value=raw):
                r.scan_cycle=1;self.assertFalse(r.two_direction_observation('BTCUSDT',cfg,packet(BAR+3479),BAR+3479)['primary_events'])
                r.scan_cycle=2;self.assertTrue(r.two_direction_observation('BTCUSDT',cfg,packet(BAR+3481),BAR+3481)['primary_events'])
                r.scan_cycle=3;self.assertFalse(r.two_direction_observation('BTCUSDT',cfg,packet(BAR+3601),BAR+3601)['primary_events'])
                r.scan_cycle=4;self.assertFalse(r.two_direction_observation('BTCUSDT',cfg,packet(BAR+5279),BAR+5279)['primary_events'])
                r.scan_cycle=5;self.assertFalse(r.two_direction_observation('BTCUSDT',cfg,packet(BAR+5281,BAR+5240),BAR+5281)['primary_events'])
                r.scan_cycle=6;self.assertTrue(r.two_direction_observation('BTCUSDT',cfg,packet(BAR+5282),BAR+5282)['primary_events'])
    def test_all_four_minute_order_block_types_are_unfiltered(self):
        from test_order_block_intrabar import rows as ob_rows
        for kind,side in [('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=STRATEGY);now=BAR+2001;start=int(now)//60*60
                r=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                def rows(trigger):
                    source=ob_rows(kind=kind,trigger=trigger,passing=False)
                    return [{**row,'time':start-(len(source)-i-1)*60,'volume':0} for i,row in enumerate(source)]
                r.confirmation_symbol_cycles['BTCUSDT']=1
                with patch('thoth.order_block.entry_volume',side_effect=AssertionError('No volume filter')):
                    self.assertIsNone(r.order_block_observation('BTCUSDT',cfg,rows(False),now)['signal'])
                    r.confirmation_symbol_cycles['BTCUSDT']=2
                    out=r.order_block_observation('BTCUSDT',cfg,rows(True),now+5)
                self.assertEqual(out['signal'],side);self.assertIn('ob:'+kind,out['entry_sources']);self.assertEqual(out['volume_checks'],{})
    def test_hour_update_cannot_discard_fresh_minute_result_finished_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]=BAR+2001;send() # Minute baseline.
            now[0]=BAR+2007
            s.apply_signals(cfg,{'BTCUSDT':dict(time=now[0],signal=None,mark=100,primary_events=[],volume_signal=None,volume_sources=[],observation_channel='hour_permission')})
            now[0]=BAR+2008
            s.apply_signals(cfg,{'BTCUSDT':dict(time=BAR+2006,signal=None,mark=100,primary_events=[],volume_signal='Long',volume_sources=['htf:1:ob:bullish_formed'],observation_channel='minute_signal')})
            self.assertEqual(len(e.orders),1,s.state['events'][-5:])
            self.assertEqual(list(e.orders.values())[-1]['side'],'Buy')
    def test_separate_cursors_still_reject_minute_signal_before_replacement_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]=BAR+3481
            s.apply_signals(cfg,{'BTCUSDT':dict(time=now[0],signal=None,mark=100,primary_events=[event(now[0])],volume_signal=None,volume_sources=[],observation_channel='hour_permission')})
            now[0]+=1
            s.apply_signals(cfg,{'BTCUSDT':dict(time=BAR+3480,signal=None,mark=100,primary_events=[],volume_signal='Long',volume_sources=['htf:1:ob:bullish_formed'],observation_channel='minute_signal')})
            self.assertFalse(e.orders)
            now[0]+=1;send('Long');self.assertEqual(len(e.orders),1)
    def test_minute_backfill_preserves_bridge_until_sources_advance_then_prunes(self):
        import json
        from thoth.database import connect
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            feed=FeedAdapter(s);r=Runner(feed,confirmation_only=True);settings=Settings(**cfg)
            base=1790000000//60*60
            def row(i):return dict(time=base+i*60,open=100,high=101,low=99,close=100,volume=1)
            now[0]=base+199*60+5;r.confirmation_symbol_cycles['BTCUSDT']=1
            r.combined_order_block_sr_observation('BTCUSDT',settings,[row(i) for i in range(200)],now[0])
            now[0]=base+11000*60+5;calls=[];repaired=False
            def fetch(symbol,interval,limit=1000,end=None):
                self.assertEqual(interval,'1');calls.append((limit,end))
                last=int((now[0]-base)//60) if end is None else (end//1000-base)//60
                return [row(i) for i in range(last-limit+1,last+1)]
            with patch.object(e,'candles',side_effect=fetch):
                for _ in range(7):
                    before=len(calls);rows=feed.staged_minute_inputs('BTCUSDT')
                    self.assertLessEqual(len(calls)-before,3) # normal read + at most two backfill pages
                    r.confirmation_symbol_cycles['BTCUSDT']+=1
                    try:
                        out=r.combined_order_block_sr_observation('BTCUSDT',settings,rows,now[0]);repaired=True;break
                    except ValueError as exc:
                        self.assertIn('gap',str(exc))
                        with connect(r.history_path) as db:
                            first=db.execute("SELECT MIN(time) FROM candles WHERE symbol='BTCUSDT' AND interval='1'").fetchone()[0]
                        self.assertEqual(first,base) # Do not prune away the recovery bridge.
                        # A chart refresh uses history() without defer_prune; it must also preserve the bridge.
                        with self.assertRaisesRegex(ValueError,'gap'):r.history('BTCUSDT','1',[row(i) for i in range(10001,11001)])
                        with connect(r.history_path) as db:
                            self.assertEqual(db.execute("SELECT MIN(time) FROM candles WHERE symbol='BTCUSDT' AND interval='1'").fetchone()[0],base)
                self.assertTrue(repaired);self.assertIsNone(out['signal']);self.assertFalse(e.orders)
                self.assertFalse(feed.staged_history_recovery_status('BTCUSDT')['active'])
                with connect(r.history_path) as db:
                    count,first=db.execute("SELECT COUNT(*),MIN(time) FROM candles WHERE symbol='BTCUSDT' AND interval='1'").fetchone()
                    stamps=[t for t, in db.execute("SELECT stamp FROM checkpoints WHERE symbol='BTCUSDT' AND interval='1'")]
                    ob_stamp=db.execute("SELECT stamp FROM order_block_checkpoints WHERE symbol='BTCUSDT'").fetchone()[0]
                self.assertEqual(count,5001);self.assertEqual(first,base+6000*60)
                self.assertTrue(stamps and min(stamps)==base+10999*60);self.assertEqual(ob_stamp,base+10999*60)
                before=len(calls);now[0]+=5
                rows=feed.staged_minute_inputs('BTCUSDT');r.confirmation_symbol_cycles['BTCUSDT']+=1
                r.combined_order_block_sr_observation('BTCUSDT',settings,rows,now[0]);self.assertEqual(len(calls)-before,1)
    def test_unavailable_minute_backfill_waits_and_never_invents_or_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now);feed=FeedAdapter(s);r=Runner(feed,confirmation_only=True)
            base=1790000000//60*60
            def row(i):return dict(time=base+i*60,open=100,high=101,low=99,close=100,volume=1)
            r.history('BTCUSDT','1',[row(i) for i in range(200)])
            now[0]=base+3000*60+5;calls=[]
            def fetch(symbol,interval,limit=1000,end=None):
                calls.append(end)
                return [] if end is not None else [row(i) for i in range(2001,3001)]
            with patch.object(e,'candles',side_effect=fetch):
                rows=feed.staged_minute_inputs('BTCUSDT')
                with self.assertRaisesRegex(ValueError,'gap'):r.combined_order_block_sr_observation('BTCUSDT',Settings(**cfg),rows,now[0])
                self.assertIn('Backfill unavailable',feed.staged_history_recovery_status('BTCUSDT')['reason'])
                self.assertFalse(e.orders);now[0]+=5;before=len(calls)
                feed.staged_minute_inputs('BTCUSDT');self.assertEqual(len(calls)-before,1)
    def test_queued_reverse_survives_minute_rollover_while_still_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Long');self.fill_latest(s,e)
            now[0]=BAR+1915;send('Short') # :31:55
            self.assertEqual(len(e.orders),2)
            now[0]=BAR+1924;self.fill_latest(s,e) # :32:04, after confirmed close/reconciliation
            self.assertEqual(len(e.orders),3,s.state['events'][-6:])
            self.assertEqual(list(e.orders.values())[-1]['side'],'Sell')
    def test_rollover_allowance_does_not_extend_reversal_ttl_or_apply_to_first_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]=BAR+1915
            obs=s._two_direction_observation('BTCUSDT',dict(time=now[0],volume_signal='Long',volume_sources=[],primary_events=[]),TestnetSettings(**s.state['config']))
            now[0]=BAR+1924
            s._entry('BTCUSDT','Long',BAR+1915,'first-late',TestnetSettings(**s.state['config']),obs['entry_context'])
            self.assertFalse(e.orders) # First entry still needs the current minute.
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Long');self.fill_latest(s,e)
            now[0]=BAR+1915;send('Short');now[0]=BAR+1946;self.fill_latest(s,e)
            self.assertEqual(len(e.orders),2);self.assertFalse(s.state['reversals'])
            self.assertTrue(any('reverse signal expired' in v['message'] for v in s.state['events']))
    def test_rollover_reversal_still_respects_loss_cooldown_after_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1679];s,e,cfg,send=self.fixture(tmp,now)
            now[0]+=2;send('Long');self.fill_latest(s,e)
            s.state['risk']['symbols']['BTCUSDT']=dict(streak=s.state['config']['consecutive_losses']-1,pause_until=None);s.save()
            e.prices['BTCUSDT']=99
            now[0]=BAR+1915;send('Short');now[0]=BAR+1924;self.fill_latest(s,e)
            self.assertEqual(len(e.orders),2);self.assertFalse(s.state['reversals'])
            self.assertGreater(s.state['risk']['symbols']['BTCUSDT']['pause_until'],now[0])
            self.assertTrue(any('replacement entry blocked' in v['message'] and 'cooldown' in v['message'] for v in s.state['events']))
