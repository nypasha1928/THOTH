"""Timing diagnostics use simulated exchange data; never real orders."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from thoth.entry_diagnostics import measure,read
from thoth.execution import ExecutionJournal
from thoth.bybit_testnet import TestnetClient
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange
from test_order_block_intrabar import rows,NOW


class EntryDiagnosticTests(unittest.TestCase):
    def test_measured_stages_price_move_and_unknown_vs_zero(self):
        details=dict(qualified_at=100,qualified_price=100,previous_observed_at=90,sources={'ob:bullish_formed':dict(first_seen_at=70)},
            sample_requested_at=95,sample_received_at=97,processed_at=100,queued_at=101,checks_started_at=103,checks_completed_at=106)
        timing=dict(submit_started_at=107,acknowledged_at=109,exchange_created_at=108,fill_observed_at=112,fill_price=101,filled_qty=.5)
        result=measure(details,timing,'Buy',1)
        self.assertEqual(result['volume_wait_seconds'],30);self.assertEqual(result['observation_gap_seconds'],10)
        self.assertEqual(result['queue_seconds'],2);self.assertEqual(result['entry_checks_seconds'],3)
        self.assertEqual(result['qualified_to_submit_seconds'],7);self.assertEqual(result['submit_to_fill_observed_seconds'],5)
        self.assertEqual(result['entry_move_pct'],1);self.assertEqual(result['entry_move_dollars'],.5);self.assertEqual(result['entry_move_stop_pct'],50)
        self.assertEqual(measure(details,timing,'Sell',1)['entry_move_pct'],-1)
        unknown=measure({}, {},'Buy',1);self.assertNotIn('volume_wait_seconds',unknown)
        self.assertNotIn('submit_to_fill_observed_seconds',unknown)
        self.assertEqual(measure(dict(qualified_at=100),dict(submit_started_at=100),'Buy',1)['qualified_to_submit_seconds'],0)
        skew=measure(details,dict(submit_started_at=99),'Buy',1)
        self.assertTrue(skew['clock_order_warning']);self.assertNotIn('qualified_to_submit_seconds',skew)

    def test_order_timing_survives_settlement_without_retrying_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[1000.];client=Mock(BASE=TestnetClient.BASE,armed=True)
            def submit(payload):now[0]+=2;return dict(state='accepted_not_filled')
            client.submit.side_effect=submit
            journal=ExecutionJournal(Path(tmp)/'execution.sqlite',client,clock=lambda:now[0])
            payload=dict(symbol='BTCUSDT',side='Buy',orderLinkId='fixture',reduceOnly=False)
            journal.submit(payload,1,dict(priceFilter=dict(tickSize='.1')))
            journal.record_timing('fixture',fill_observed_at=1005,fill_price=101,filled_qty=.5)
            journal.update('fixture','closed',dict(reason='Position flat'))
            result=journal.get('fixture')['result']['entry_timing']
            self.assertEqual(result['submit_started_at'],1000);self.assertEqual(result['acknowledged_at'],1002)
            self.assertEqual(result['fill_observed_at'],1005);self.assertEqual(result['fill_price'],101)
            self.assertEqual(client.submit.call_count,1)
            with patch.object(journal,'db',side_effect=sqlite3.OperationalError('fixture unavailable')):
                journal.record_timing('fixture',fill_price=999) # No trading exception from optional diagnostics.

    def test_real_worker_records_sampled_raw_volume_wait_and_fill_without_extra_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[NOW];stage=[0];exchange=Exchange(lambda:now[0]);calls=[]
            service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy='order_block_intrabar',symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            service.configure(cfg);service.activate(service.confirmation)
            def fetch(symbol,interval,limit=1000):
                calls.append((interval,limit));return rows(now[0],trigger=stage[0]>0,passing=stage[0]>1)[-limit:]
            runner=Runner(FeedAdapter(service),fetch=FeedAdapter(service).fetch)
            with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False)
                now[0]+=5;stage[0]=1;runner.cycle(paced=False);self.assertFalse(exchange.orders)
                now[0]+=5;stage[0]=2;runner.cycle(paced=False)
                order=next(iter(exchange.orders.values()));now[0]+=3;exchange.fill(order['orderLinkId']);service.monitor()
            self.assertEqual(calls,[('240',1000),('240',60),('240',60),('240',20)])
            report=read(service.path);self.assertEqual(report['total'],1);item=report['items'][0]
            self.assertEqual(item['measurements']['volume_wait_seconds'],5)
            self.assertEqual(item['measurements']['observation_gap_seconds'],5)
            self.assertEqual(item['timing']['fill_price'],100);self.assertEqual(item['measurements']['submit_to_fill_observed_seconds'],3)
            self.assertEqual(item['details']['sources']['ob:bullish_formed']['first_seen_at'],NOW+5)
            self.assertEqual(item['details']['qualified_at'],NOW+10)
            service._close('BTCUSDT',exchange.active['BTCUSDT'],'Fixture close')
            close=next(value for value in exchange.orders.values() if value['reduceOnly']);exchange.fill(close['orderLinkId']);service.monitor()
            closed=read(service.path,symbol='BTCUSDT')['items'][0]
            self.assertEqual(closed['phase'],'closed');self.assertEqual(closed['measurements']['volume_wait_seconds'],5)
            self.assertEqual(read(service.path,symbol='ETHUSDT')['total'],0)
            self.assertEqual(read(service.path,offset=1)['items'],[])
            self.assertEqual(read(service.path)['total'],1) # Reading does not mutate diagnostics.

    def test_sr_volume_wait_and_repaint_reset_are_observations_not_market_event_time(self):
        from thoth.sr_volume import observe
        cfg=Settings(strategy='sr_volume_intrabar',timeframe='240');tracker={}
        level={'long_resistance_major':dict(value=101,pivot_time=0)}
        start=int(NOW)//14400*14400
        def sample(price,volume,t):
            data=[dict(time=start-(19-i)*14400,close=price,volume=volume if i==19 else 10) for i in range(20)]
            return observe(tracker,data,level,cfg,t,30)
        sample(100,10,NOW);self.assertIsNone(sample(102,10,NOW+5)['signal'])
        sample(100,10,NOW+10) # Pullback invalidates the pending raw crossing.
        sample(102,10,NOW+15)
        result=sample(102,40,NOW+20)
        self.assertEqual(result['signal'],'Long')
        self.assertEqual(result['entry_diagnostics']['sources']['sr:long_resistance_major']['first_seen_at'],NOW+15)
