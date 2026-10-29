"""One submitted entry per closed-hour permission; confirmed minute exits only."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.staged_entry import HOUR_GATE
from thoth.bybit_testnet import BybitError,AmbiguousOrder
from test_testnet import Exchange
from test_order_block_intrabar import rows as source_rows

NOW=1793275200.  # Fixed test hour; no exchange/network.

class SinglePermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=[NOW+45];self.path=Path(self.tmp.name)/'live.sqlite'
        self.exchange=Exchange(lambda:self.now[0])
        self.service=self.make_service()
        self.cfg=TestnetSettings(strategy=HOUR_GATE,symbols=['PAXGUSDT'],dedicated_account=True,daily_loss_pct=90,consecutive_losses=2)
        self.exchange.prices['PAXGUSDT']=100
        self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.feed_cfg=Settings(**{k:v for k,v in self.cfg.model_dump().items() if k in Settings.model_fields}).model_dump()
        self.bar=NOW-3600
        self.sample(primary=True)

    def tearDown(self):self.tmp.cleanup()

    def make_service(self):
        return TestnetSupervisor(self.path,enabled=True,password_configured=True,
            client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])

    def sample(self,primary=False,side=None,exit_side=None,exit_bar=None,source_bar=None):
        self.now[0]+=5
        bar=self.bar if source_bar is None else source_bar
        primary_events=[dict(id='closed-hour:'+str(bar),signal='Long',time=self.now[0],timeframe='60',
            closed_bar=bar,window_start=bar+3600,sources=['htf:60:ob:bullish_formed'])] if primary else []
        self.service.apply_signals(self.feed_cfg,{'PAXGUSDT':dict(time=self.now[0],mark=100,primary_events=primary_events,
            volume_signal=side,volume_sources=['htf:1:sr:short_support_major'],exit_signal=exit_side,
            exit_sources=['htf:1:ob:bullish_formed'],closed_exit_bar=exit_bar)})

    def entries(self):return [o for o in self.exchange.orders.values() if not o['reduceOnly']]

    def open_short(self):
        self.sample(side='Short');order=self.entries()[-1]
        self.exchange.fill(order['orderLinkId']);self.service.monitor()
        return order

    def close_trailing(self,pnl=-.1):
        self.exchange.prices['PAXGUSDT']=float(self.service.state['owned']['PAXGUSDT']['entry'])-pnl/float(self.service.state['owned']['PAXGUSDT']['qty'])
        self.service._close('PAXGUSDT',self.exchange.active['PAXGUSDT'],'Fixture trailing')
        close=next(o for o in self.exchange.orders.values() if o['reduceOnly'] and o['orderStatus']=='New')
        self.exchange.fill(close['orderLinkId']);self.service.monitor()

    def test_paxg_cannot_reenter_after_close_until_later_closed_hour_signal(self):
        self.assertFalse(self.entries());self.open_short()
        self.assertEqual(self.service.state['entry_gates']['PAXGUSDT']['phase'],'consumed')
        self.close_trailing()
        self.sample(side='Short');self.sample(side='Long');self.sample(primary=True,side='Short')
        self.assertEqual(len(self.entries()),1)
        # Even a differently keyed duplicate source on the same closed hour cannot rearm.
        gate=self.service.state['entry_gates']['PAXGUSDT']
        gate['seen_primary']=[];self.service.save();self.sample(primary=True)
        self.assertEqual(gate['phase'],'consumed')
        self.now[0]=NOW+3601;self.sample(side='Short')
        self.assertEqual(len(self.entries()),1) # clock advancing alone supplies no permission
        self.sample(primary=True,source_bar=NOW)
        self.assertEqual(gate['phase'],'armed');self.assertEqual(gate['losses'],1)
        self.open_short();self.assertEqual(len(self.entries()),2)

    def test_winning_trailing_close_also_requires_new_hour_permission(self):
        self.open_short();self.close_trailing(pnl=.1)
        gate=self.service.state['entry_gates']['PAXGUSDT']
        self.assertEqual((gate['phase'],gate['losses']),('consumed',0))
        self.sample(side='Short');self.assertEqual(len(self.entries()),1)

    def test_old_installed_armed_permission_is_consumed_from_legacy_journal(self):
        self.open_short();self.close_trailing()
        record=next(r for r in self.service.journal.records() if not r['payload']['reduceOnly'])
        instrument=record['instrument']
        for key in ('thoth_permission_armed_at','thoth_permission_arm_id','thoth_permission_closed_bar'):
            instrument.pop(key,None)
        import json
        with self.service.journal.db() as db:
            db.execute('UPDATE execution SET instrument=? WHERE link=?',(json.dumps(instrument),record['link']))
        # Simulate the installed version which retained "armed" after a loss.
        gate=self.service.state['entry_gates']['PAXGUSDT'];gate['phase']='armed'
        self.service.save();self.service=self.make_service();self.service.activate(self.service.confirmation)
        self.sample(side='Short')
        self.assertEqual(len(self.entries()),1)
        self.assertEqual(self.service.state['entry_gates']['PAXGUSDT']['phase'],'consumed')

    def test_live_minute_opposite_cannot_exit_closed_minute_can_exit_without_reversal(self):
        self.open_short();self.sample(side='Long')
        self.assertFalse([o for o in self.exchange.orders.values() if o['reduceOnly']])
        self.service.pause()
        self.now[0]=NOW+121
        self.sample(side='Long',exit_side='Long',exit_bar=NOW+60)
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual(len(closes),1);self.assertFalse(self.service.state['reversals'])
        self.exchange.fill(closes[0]['orderLinkId']);self.service.monitor()
        self.assertEqual(self.service.history()['items'][0]['reason'],'Opposite CLOSED 1m Order Block / S/R signal')
        self.assertEqual(len(self.entries()),1)

    def test_closed_candle_before_entry_or_stale_candle_cannot_exit(self):
        self.open_short();self.sample(exit_side='Long',exit_bar=NOW-60)
        self.now[0]=NOW+121;self.sample(exit_side='Long',exit_bar=NOW)
        self.assertFalse([o for o in self.exchange.orders.values() if o['reduceOnly']])

    def test_rejection_can_retry_same_permission_uncertain_submission_cannot(self):
        with patch.object(self.exchange,'submit',side_effect=BybitError('fixture rejection',code=110125)):
            self.sample(side='Short')
        self.assertEqual(self.service.state['entry_gates']['PAXGUSDT']['phase'],'armed')
        with patch.object(self.exchange,'submit',side_effect=AmbiguousOrder('fixture uncertain')):
            self.sample(side='Short')
        self.assertEqual(self.service.state['entry_gates']['PAXGUSDT']['phase'],'consumed')
        self.sample(side='Long');self.assertFalse(self.service.state['pending_signals'])

    def test_two_losses_across_two_distinct_permissions_keep_cooldown(self):
        self.open_short();self.close_trailing()
        self.now[0]=NOW+3601;self.sample(primary=True,source_bar=NOW)
        self.open_short();self.close_trailing()
        gate=self.service.state['entry_gates']['PAXGUSDT']
        self.assertEqual((gate['losses'],gate['phase']),(2,'cooldown'))


class ClosedMinuteDetectorTests(unittest.TestCase):
    def test_all_four_ob_events_wait_for_close_then_emit_once_and_not_on_restart(self):
        for kind,side in [('bullish_formed','Long'),('bearish_formed','Short'),('supply_breakout','Long'),('demand_breakdown','Short')]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'live.sqlite';cfg=Settings(strategy=HOUR_GATE)
                runner=Runner(SimpleNamespace(path=str(path)),confirmation_only=True)
                start=int(NOW)//60*60
                data=source_rows(kind=kind,trigger=True,passing=False)
                rows=[{**row,'time':start-(len(data)-i-1)*60,'volume':0} for i,row in enumerate(data)]
                def analysis(symbol,cfg,rows,now,**kwargs):
                    return [dict(time=int(now)//60*60-60,signal=None,entry_sources=[])],200
                runner.analysis=analysis;runner.confirmation_symbol_cycles['PAXGUSDT']=1
                self.assertIsNone(runner.closed_minute_exit_observation('PAXGUSDT',cfg,rows,start+55)['exit_signal'])
                # A wildly different forming candle must not determine the closed exit.
                next_rows=rows+[dict(time=start+60,open=100,close=10000,high=10001,low=1,volume=0)]
                runner.confirmation_symbol_cycles['PAXGUSDT']=2
                report=runner.closed_minute_exit_observation('PAXGUSDT',cfg,next_rows,start+61)
                self.assertEqual(report['exit_signal'],side,report)
                self.assertEqual(report['closed_exit_bar'],start)
                self.assertIn('htf:1:ob:'+kind,report['exit_sources'])
                runner.confirmation_symbol_cycles['PAXGUSDT']=3
                self.assertIsNone(runner.closed_minute_exit_observation('PAXGUSDT',cfg,next_rows,start+66)['exit_signal'])
                restarted=Runner(SimpleNamespace(path=str(path)),confirmation_only=True);restarted.analysis=analysis
                restarted.confirmation_symbol_cycles['PAXGUSDT']=1
                self.assertIsNone(restarted.closed_minute_exit_observation('PAXGUSDT',cfg,next_rows,start+71)['exit_signal'])

    def test_closed_sr_uses_completed_bar_both_sides_and_skips_missed_minute(self):
        for side in ('Long','Short'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(strategy=HOUR_GATE);runner=Runner(SimpleNamespace(path=str(Path(tmp)/'live.sqlite')),confirmation_only=True)
                start=NOW;rows=[dict(time=start-(59-i)*60,open=100,close=100,high=101,low=99,volume=0) for i in range(60)]
                levels={('long_support_major' if side=='Long' else 'short_resistance_minor'):dict(value=99 if side=='Long' else 101,pivot_time=0)}
                rows[-1].update(close=101 if side=='Long' else 99,high=102,low=98)
                def analysis(symbol,cfg,rows,now,**kwargs):
                    return [dict(time=int(now)//60*60-60,levels=levels,signal=None,entry_sources=[])],200
                runner.analysis=analysis;runner.confirmation_symbol_cycles['PAXGUSDT']=1
                runner.closed_minute_exit_observation('PAXGUSDT',cfg,rows,start+55)
                rows.append({**rows[-1],'time':start+60})
                runner.confirmation_symbol_cycles['PAXGUSDT']=2
                self.assertEqual(runner.closed_minute_exit_observation('PAXGUSDT',cfg,rows,start+61)['exit_signal'],side)
                rows.extend([{**rows[-1],'time':start+120},{**rows[-1],'time':start+180}])
                runner.confirmation_symbol_cycles['PAXGUSDT']=3
                self.assertIsNone(runner.closed_minute_exit_observation('PAXGUSDT',cfg,rows,start+181)['exit_signal'])
