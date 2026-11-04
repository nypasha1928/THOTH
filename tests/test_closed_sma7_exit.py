"""New-profile-only completed-minute SMA7 exits; synthetic exchange only."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.closed_sma7_exit import observe as check_exit
from thoth.intrabar_exit import ExitDataError
from thoth.volume_delta_midpoint import STRATEGY,EXIT_STRATEGY,context,evaluate
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from test_volume_delta_midpoint import data,BAR
from test_testnet import Exchange

def observe(tracker,rows,side,now,entered_at,*,sampled_at=None):
    return check_exit(tracker,rows,side,now,entered_at,sampled_at=now if sampled_at is None else sampled_at)


def minute_rows(now,side='Buy',relation='adverse'):
    latest=int(now)//60*60-60
    last=99 if side=='Buy' else 101
    if relation=='equal':last=100
    if relation=='favorable':last=101 if side=='Buy' else 99
    rows=[dict(time=latest-(6-i)*60,open=100,high=102,low=98,close=100,volume=1) for i in range(7)]
    rows[-1]['close']=last
    rows.append(dict(time=latest+60,open=100,high=200,low=1,close=1 if side=='Buy' else 200,volume=1))
    return rows

class ClosedSMA7Tests(unittest.TestCase):
    def test_closed_only_levels_equality_and_no_required_cross(self):
        now=BAR+1861
        for side in ('Buy','Sell'):
            for relation in ('adverse','equal','favorable'):
                tracker={};rows=minute_rows(now,side,relation)
                reason=observe(tracker,rows,side,now,BAR+1801)
                self.assertEqual(bool(reason),relation=='adverse')
                self.assertEqual(tracker['last_bar'],BAR+1800)
                self.assertIsNone(observe(tracker,rows,side,now+1,BAR+1801))
    def test_only_after_entry_and_missing_stale_duplicate_invalid_prices_fail(self):
        now=BAR+1861;rows=minute_rows(now)
        tracker={};self.assertIsNone(observe(tracker,rows,'Buy',now,BAR+1860))
        self.assertTrue(tracker['waiting_for_post_entry_close'])
        gap=copy.deepcopy(rows);gap.pop(2)
        duplicate=copy.deepcopy(rows);duplicate[2]['time']=duplicate[1]['time']
        invalid=copy.deepcopy(rows);invalid[2]['close']=float('nan')
        for bad in (rows[1:],gap,duplicate,invalid):
            with self.assertRaises(ExitDataError):observe({},bad,'Buy',now,BAR+1801)
        with self.assertRaises(ExitDataError):observe({},rows,'Buy',now+60,BAR+1801,sampled_at=now)
        with self.assertRaises(ExitDataError):observe({},rows[:-1],'Buy',now+75,BAR+1801)
        future=copy.deepcopy(rows);future[-1]['time']+=60
        with self.assertRaises(ExitDataError):observe({},future,'Buy',now,BAR+1801)
        with self.assertRaises(ExitDataError):check_exit({},rows,'Buy',now,BAR+1801)
        tracker={};self.assertIsNone(observe(tracker,rows[:-2],'Buy',now,BAR+1801));self.assertIn('waiting_for_candle',tracker)
    def test_new_profile_uses_identical_entry_calculation_and_original_has_no_minute_exit(self):
        old=Settings(strategy=STRATEGY);new=Settings(strategy=EXIT_STRATEGY,timeframe='D',sma_exit_enabled=True)
        self.assertEqual(new.timeframe,'60');self.assertFalse(new.sma_exit_enabled);self.assertEqual(old.volume_delta,new.volume_delta)
        rows,lower=data()
        with self.assertRaisesRegex(ValueError,'1h strategy timeframe'):evaluate(rows,lower,new.model_copy(update={'timeframe':'D'}),BAR+1801)
        for side in ('Long','Short'):
            rows,lower=data(side);self.assertEqual(evaluate(rows,lower,old,BAR+1801),evaluate(rows,lower,new,BAR+1801))

    def fixture(self,tmp,now,side='Long',strategy=EXIT_STRATEGY):
        e=Exchange(lambda:now[0]);s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        cfg=TestnetSettings(strategy=strategy,symbols=['BTCUSDT','ETHUSDT'],dedicated_account=True,daily_loss_pct=90)
        s.configure(cfg);s.activate(s.confirmation);cfg=TestnetSettings(**s.state['config']);rows,lower=data(side);e.prices['BTCUSDT']=rows[-1]['close'];e.prices['ETHUSDT']=rows[-1]['close']
        def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
        with patch.object(e,'candles',side_effect=fetch):s._entry('BTCUSDT',side,now[0],'fixture',cfg,context(rows,cfg,now[0]))
        first=next(iter(e.orders));e.fill(first)
        with patch.object(e,'candles',return_value=minute_rows(now[0],e.orders[first]['side'],'favorable')):s.monitor()
        return s,e,cfg,rows,lower

    def test_new_profile_exits_after_close_while_paused_close_only_no_reentry(self):
        for side in ('Long','Short'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+1801];s,e,cfg,rows,lower=self.fixture(tmp,now,side)
                self.assertEqual(s.state['owned']['BTCUSDT']['entry_timeframe'],'60');self.assertEqual(s.state['owned']['BTCUSDT']['exit_timeframe'],'1')
                s.pause();now[0]=BAR+1861
                with patch.object(e,'candles',return_value=minute_rows(now[0],'Buy' if side=='Long' else 'Sell')):s.monitor();now[0]+=5;s.monitor()
                closes=[o for o in e.orders.values() if o['reduceOnly']];self.assertEqual(len(closes),1)
                self.assertEqual(len(e.orders),2);self.assertFalse(s.state['reversals']);self.assertFalse(s.state['entries'])
                self.assertIn('Closed 1m price',s.state['owned']['BTCUSDT']['reason'])
                e.fill(closes[0]['orderLinkId']);s.monitor();self.assertFalse(s.state['owned'])
                self.assertEqual(len(e.orders),2)
    def test_original_profile_never_runs_new_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1801];s,e,cfg,rows,lower=self.fixture(tmp,now,strategy=STRATEGY)
            now[0]=BAR+1861
            with patch.object(e,'candles',side_effect=AssertionError('Original profile must not fetch minute exit data')):s.monitor()
            self.assertEqual(len(e.orders),1);self.assertFalse(s.state['sma_exit_blocks'])
            self.assertEqual(s.state['owned']['BTCUSDT']['exit_timeframe'],'none')
    def test_exit_requested_before_wallet_failure_and_survives_restart_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1801];s,e,cfg,rows,lower=self.fixture(tmp,now);now[0]=BAR+1861
            original=e.request
            def request(method,path,params,private=False):
                if path=='/v5/account/wallet-balance':raise ConnectionError('fixture wallet down')
                return original(method,path,params,private)
            with patch.object(e,'candles',return_value=minute_rows(now[0])),patch.object(e,'request',side_effect=request):
                with self.assertRaises(ConnectionError):s.monitor()
            self.assertEqual(len(e.orders),2)
            recovered=TestnetSupervisor(s.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            recovered.activate(recovered.confirmation,recovery=True);recovered.monitor()
            self.assertFalse(recovered.state['entries']);self.assertEqual(len(e.orders),2)
            self.assertIn('Closed 1m price',recovered.state['owned']['BTCUSDT']['signal_exit'])
    def test_minute_data_failure_is_local_and_does_not_disable_other_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1801];s,e,cfg,rows,lower=self.fixture(tmp,now)
            def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
            with patch.object(e,'candles',side_effect=fetch):s._entry('ETHUSDT','Long',now[0],'fixture',cfg,context(rows,cfg,now[0]))
            eth=next(o for o in e.orders.values() if o['symbol']=='ETHUSDT');e.fill(eth['orderLinkId'])
            # Observe ETH's fill while minute :30 is still forming.
            with patch.object(e,'candles',return_value=minute_rows(now[0],relation='favorable')):s.monitor()
            now[0]=BAR+1861
            def partial(symbol,interval,limit=1000,**kw):
                if symbol=='BTCUSDT':raise ConnectionError('fixture candle gap')
                return minute_rows(now[0])
            with patch.object(e,'candles',side_effect=partial):s.monitor()
            self.assertIn('BTCUSDT',s.state['sma_exit_blocks']);self.assertEqual(s.state['status'],'running');self.assertTrue(s.state['entries'])
            self.assertEqual([o['symbol'] for o in e.orders.values() if o['reduceOnly']],['ETHUSDT'])

    def test_new_profile_full_runner_entry_pipeline_and_closed_minute_exit(self):
        from thoth.runner import Runner
        from thoth.testnet_feed import FeedAdapter
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1799];e=Exchange(lambda:now[0]);e.prices['BTCUSDT']=101
            s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            cfg=TestnetSettings(strategy=EXIT_STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
            s.configure(cfg);s.activate(s.confirmation);feed=FeedAdapter(s);runner=Runner(feed,fetch=feed.fetch);rows,lower=data()
            def fetch(symbol,interval,limit=1000,**kw):
                if interval=='60':return rows[-limit:]
                if now[0]>=BAR+1860:return minute_rows(now[0])[-limit:]
                return lower[-limit:]
            with patch.object(e,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False);self.assertFalse(e.orders)
                now[0]=BAR+1801;runner.cycle(paced=False);self.assertEqual(len(e.orders),1,(runner.health,s.state['events'][-5:]))
                e.fill(next(iter(e.orders)));s.monitor()
                now[0]=BAR+1861;s.monitor()
            self.assertEqual(len([o for o in e.orders.values() if o['reduceOnly']]),1)
            self.assertEqual(len(e.orders),2)

    def test_missing_fill_observation_never_falls_back_to_order_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1801];s,e,cfg,rows,lower=self.fixture(tmp,now)
            s.state['owned']['BTCUSDT']['sma_exit']={}
            original=s.journal.get
            def without_fill_time(link):
                record=copy.deepcopy(original(link));record.get('result',{}).get('entry_timing',{}).pop('fill_observed_at',None);return record
            now[0]=BAR+1861
            with patch.object(s.journal,'get',side_effect=without_fill_time),patch.object(e,'candles',side_effect=lambda *a,**k:minute_rows(now[0])):
                s.monitor();self.assertEqual(len(e.orders),1)
                self.assertEqual(s.state['owned']['BTCUSDT']['sma_exit']['entry_confirmed_at'],now[0])
                now[0]+=60;s.monitor()
                self.assertEqual(len(e.orders),2)
