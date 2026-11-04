"""Read-only :25 comparison; synthetic candles only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.late_hour_comparison import ComparisonSettings
from thoth.volume_delta_comparison import VolumeDeltaComparison,compared,early_window
from thoth.volume_delta import signal
from thoth.volume_delta_midpoint import STRATEGY
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from test_volume_delta_midpoint import data,BAR
from test_testnet import Exchange

class DeltaTimingTests(unittest.TestCase):
    def cfg(self,**kw):return Settings(strategy=STRATEGY,symbols=['BTCUSDT'],**kw)
    def observer(self,tmp,clock):return VolumeDeltaComparison(Path(tmp)/'comparison.sqlite',clock=clock)
    def send(self,o,phase,cfg,at,side='Long',price=None):
        rows,lower=data(side)
        if price is not None:rows[-1]['close']=price
        if phase=='at25':lower=lower[:26]
        v=cfg.volume_delta
        decision=signal(rows,lower,'60',at,sma_filter=v.sma_filter,bull_type=v.bull_type,bear_type=v.bear_type,bullish_enabled=v.bullish,bearish_enabled=v.bearish) if phase=='at30' else None
        o.submit(phase,'BTCUSDT',cfg,rows,lower,at,decision=decision)
        o.process(o.inputs.get_nowait())
    def test_first_snapshot_no_signal_is_retained_and_same_settings_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);cfg=self.cfg()
            self.send(o,'at25',cfg,now[0],side='None');now[0]+=1;self.send(o,'at25',cfg,now[0])
            now[0]=BAR+1801;self.send(o,'at30',cfg,now[0])
            row=o.report()['items'][0];self.assertIsNone(row['at25']['signal']);self.assertEqual(row['at30']['signal'],'Long')
            self.assertEqual(row['comparison']['status'],':30 only; no signal at :25')
            recovered=self.observer(tmp,lambda:now[0]);self.assertEqual(recovered.report()['total'],1)
    def test_price_advantage_and_common_close_for_both_directions(self):
        for side,p25,p30,close in [('Long',101,102,103),('Short',99,98,97)]:
            with self.subTest(side=side),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);cfg=self.cfg()
                self.send(o,'at25',cfg,now[0],side,price=p25)
                now[0]=BAR+1801;self.send(o,'at30',cfg,now[0],side,price=p30)
                row=o.report()['items'][0];self.assertEqual(row['comparison']['status'],'Same direction');self.assertGreater(row['comparison']['early_price_advantage_pct'],0)
                now[0]=BAR+3601;closed=[dict(time=BAR,close=close)]
                o.submit('close','BTCUSDT',cfg,closed,[],now[0]);o.process(o.inputs.get_nowait())
                moves=o.report()['items'][0]['comparison']['move_to_hour_close_pct'];self.assertGreater(moves['at25'],moves['at30']);self.assertGreater(moves['at30'],0)
    def test_changed_direction_and_disappearing_signal_are_not_mislabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);cfg=self.cfg();self.send(o,'at25',cfg,now[0])
            now[0]=BAR+1801;self.send(o,'at30',cfg,now[0],'Short')
            c=o.report()['items'][0]['comparison'];self.assertEqual(c['status'],'Direction changed');self.assertIsNone(c['early_price_advantage_pct'])
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);cfg=self.cfg();self.send(o,'at25',cfg,now[0])
            now[0]=BAR+1801;self.send(o,'at30',cfg,now[0],'None');self.assertIn('signal absent',o.report()['items'][0]['comparison']['status'])
    def test_unavailable_and_stale_data_are_unverified_not_no_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);cfg=self.cfg();rows,lower=data()
            o.submit('at25','BTCUSDT',cfg,rows,lower[1:26],now[0]);o.process(o.inputs.get_nowait())
            now[0]=BAR+1801;self.send(o,'at30',cfg,now[0]);row=o.report()['items'][0];self.assertFalse(row['at25']['valid']);self.assertIn('Unverified',row['comparison']['status'])
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1540];o=self.observer(tmp,lambda:now[0]);rows,lower=data();cfg=self.cfg()
            o.submit('at25','BTCUSDT',cfg,rows,lower[:26],BAR+1501);o.process(o.inputs.get_nowait())
            self.assertIn('too late',o.report()['items'][0]['at25']['reason'])
    def test_filters_are_not_cross_matched_and_missing_windows_remain_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);self.send(o,'at25',self.cfg(),now[0])
            now[0]=BAR+1801;self.send(o,'at30',self.cfg(volume_delta={'sma_filter':False}),now[0]);now[0]=BAR+1900
            rows=o.report()['items'];self.assertEqual(len(rows),2);self.assertEqual({r['comparison']['status'] for r in rows},{'Missing :25 observation','Missing :30 observation'})
    def test_enabled_default_persistence_and_queue_overflow_do_not_touch_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];o=self.observer(tmp,lambda:now[0]);self.assertTrue(o.enabled)
            o.configure(ComparisonSettings(enabled=False));self.assertFalse(self.observer(tmp,lambda:now[0]).enabled)
            rows,lower=data();o.submit('at25','BTCUSDT',self.cfg(),rows,lower[:26],now[0]);self.assertTrue(o.inputs.empty())
            o.configure(ComparisonSettings(enabled=True))
            for _ in range(257):o.submit('at25','BTCUSDT',self.cfg(),rows,lower[:26],now[0])
            self.assertEqual(o.inputs.qsize(),256);self.assertEqual(o.dropped,1)
            self.assertEqual(list(Path(tmp).glob('*.execution.sqlite')),[])

    def test_adapter_25_download_and_observer_failure_never_trigger_or_block_live_30(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1501];e=Exchange(lambda:now[0]);s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            s.configure(cfg);s.activate(s.confirmation);feed=FeedAdapter(s);runner=Runner(feed,fetch=feed.fetch)
            o=self.observer(tmp,lambda:now[0]);feed.volume_delta_comparison=o;runner.volume_delta_comparison=o
            rows,lower=data();e.prices['BTCUSDT']=101;calls=[]
            def fetch(symbol,interval,limit=1000,**kw):calls.append((interval,limit));return rows[-limit:] if interval=='60' else lower[:26] if early_window(now[0]) else lower
            with patch.object(e,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False);runner.cycle(paced=False)
                self.assertFalse(e.orders);self.assertEqual(calls.count(('1',32)),1)
                now[0]=BAR+1801
                with patch.object(o,'submit',side_effect=RuntimeError('fixture observation failure')):runner.cycle(paced=False)
                self.assertTrue(e.orders,(runner.health,s.state['events'][-5:]))
            self.assertTrue(all(not row['reduceOnly'] for row in e.orders.values()))
