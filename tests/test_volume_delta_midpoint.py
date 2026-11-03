"""Hourly :30 sampled decisions, synthetic exchange only."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.volume_delta_midpoint import STRATEGY,window,evaluate,context,recheck
from test_testnet import Exchange

BAR=216000

def data(side='Long'):
    price=101 if side=='Long' else 99 if side=='Short' else 100
    rows=[dict(time=BAR-(59-i)*3600,open=100,high=104,low=96,close=100,volume=31) for i in range(60)]
    rows[-1]['close']=price
    lower=[dict(time=BAR+i*60,open=100,close=price,high=102,low=98,volume=1) for i in range(31)]
    return rows,lower

class MidpointTests(unittest.TestCase):
    def cfg(self,**kw):return Settings(strategy=STRATEGY,symbols=['BTCUSDT'],**kw)
    def test_profile_and_window_both_directions_and_existing_filters(self):
        cfg=self.cfg(timeframe='D',sma_enabled=True,sma_exit_enabled=True)
        self.assertEqual(cfg.timeframe,'60');self.assertFalse(cfg.sma_exit_enabled or cfg.sma_enabled)
        for seconds,expected in [(1799,False),(1800,True),(1859.99,True),(1860,False),(3599,False)]:self.assertEqual(window(BAR+seconds),expected)
        for side in ('Long','Short'):
            rows,lower=data(side);self.assertEqual(evaluate(rows,lower,cfg,BAR+1801)['signal'],side)
            self.assertEqual(recheck(rows[-2:],lower,cfg,side,context(rows,cfg,BAR+1801),BAR+1802)['signal'],side)
        rows,lower=data();rows[-7:-1]=[{**r,'close':90} for r in rows[-7:-1]]
        self.assertIsNone(evaluate(rows,lower,cfg,BAR+1801)['signal'])
        cfg=self.cfg(volume_delta={'sma_filter':False});self.assertEqual(evaluate(rows,lower,cfg,BAR+1801)['signal'],'Long')

    def fixture(self,tmp,now,side='Long'):
        e=Exchange(lambda:now[0]);s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90)
        s.configure(cfg);s.activate(s.confirmation);cfg=TestnetSettings(**s.state['config']);feed=FeedAdapter(s);runner=Runner(feed,fetch=feed.fetch)
        rows,lower=data(side);e.prices['BTCUSDT']=rows[-1]['close']
        feed.volume_delta_data['BTCUSDT']=dict(interval='60',bar=BAR,rows=lower)
        return s,e,cfg,feed,runner,rows,lower

    def test_one_snapshot_even_without_signal_persists_after_restart_and_next_hour_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1799];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now)
            self.assertIsNone(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])['signal'])
            now[0]=BAR+1800;quiet,_=data('None')
            first=runner.volume_delta_midpoint_observation('BTCUSDT',cfg,quiet,now[0]);self.assertIsNone(first['signal'])
            now[0]+=1;self.assertIsNone(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])['signal'])
            recovered=Runner(feed);self.assertIsNone(recovered.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])['signal'])
            now[0]+=3600;rows=[{**r,'time':r['time']+3600} for r in rows];feed.volume_delta_data['BTCUSDT']=dict(interval='60',bar=BAR+3600,rows=[{**r,'time':r['time']+3600} for r in lower])
            self.assertEqual(recovered.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])['signal'],'Long')

    def test_missing_lower_data_consumes_decision_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1801];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now)
            feed.volume_delta_data['BTCUSDT']['rows']=lower[1:]
            observation=runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0]);self.assertIsNone(observation['signal']);self.assertIn('Incomplete',observation['volume_delta_status'])
            feed.volume_delta_data['BTCUSDT']['rows']=lower;now[0]+=1
            self.assertIsNone(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])['signal'])

    def test_current_signal_enters_without_new_crossover_records_wick_only_and_cannot_reenter(self):
        for side in ('Long','Short'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+1801];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now,side)
                s.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=now[0]-1,signal=None,mark=100)})
                def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
                observation=runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])
                self.assertEqual(observation['signal'],side);self.assertGreater(observation['entry_context']['signal_stretch']['opposing_wick_pct'],30)
                with patch.object(e,'candles',side_effect=fetch):s.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':observation})
                self.assertEqual(len(e.orders),1,s.state['events'][-4:]);order=next(iter(e.orders.values()));self.assertEqual(order['side'],'Buy' if side=='Long' else 'Sell')
                e.fill(order['orderLinkId']);s.monitor();self.assertTrue(s.state['owned']['BTCUSDT']['protection_confirmed'])
                self.assertTrue(s._midpoint_hour_used('BTCUSDT'))
                self.assertIsNone(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0]+1)['signal'])

    def test_faded_or_expired_signal_never_posts_and_guard_catches_boundary_crossing(self):
        for mode in ('faded','expired','reconcile'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+1859 if mode=='reconcile' else BAR+1801];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now)
                ctx=context(rows,cfg,now[0]);stamp=now[0]
                fresh=data('Short')[0] if mode=='faded' else rows
                def fetch(symbol,interval,limit=1000,**kw):return fresh[-limit:] if interval=='60' else lower[-limit:]
                if mode=='expired':now[0]=BAR+1860
                original=e.reconcile
                def reconcile(symbol,link):
                    result=original(symbol,link);now[0]=BAR+1860;return result
                with patch.object(e,'candles',side_effect=fetch):
                    if mode=='reconcile':
                        with patch.object(e,'reconcile',side_effect=reconcile):s._entry('BTCUSDT','Long',stamp,'fixture',cfg,ctx)
                    else:s._entry('BTCUSDT','Long',stamp,'fixture',cfg,ctx)
                self.assertFalse(e.orders)
                self.assertFalse(any(path=='/v5/order/create' for _,path,_ in e.calls))
                if mode=='reconcile':self.assertTrue(s.journal.records()[0]['result']['not_submitted'])

    def test_old_download_is_not_a_30_snapshot_and_cannot_trigger_late(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1805];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now)
            self.assertIsNone(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0],BAR+1799)['signal'])
            self.assertEqual(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0],BAR+1804)['signal'],'Long')
            now[0]=BAR+1860
            self.assertIsNone(runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])['signal'])

    def test_reversal_closes_then_fresh_replacement_only_inside_decision_minute(self):
        for late in (False,True):
            with self.subTest(late=late),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+1801];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now)
                settings=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
                s.apply_signals(settings,{'BTCUSDT':dict(time=now[0]-1,signal=None,mark=100)})
                def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
                with patch.object(e,'candles',side_effect=fetch):s.apply_signals(settings,{'BTCUSDT':runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])})
                first=next(iter(e.orders));e.fill(first);s.monitor()
                now[0]+=3600;rows,lower=data('Short');rows=[{**r,'time':r['time']+3600} for r in rows];lower=[{**r,'time':r['time']+3600} for r in lower];e.prices['BTCUSDT']=99
                feed.volume_delta_data['BTCUSDT']=dict(interval='60',bar=BAR+3600,rows=lower)
                observation=runner.volume_delta_midpoint_observation('BTCUSDT',cfg,rows,now[0])
                s.apply_signals(settings,{'BTCUSDT':observation})
                close=next(o for o in e.orders.values() if o['reduceOnly'])
                if late:now[0]=BAR+3600+1860
                e.fill(close['orderLinkId'])
                with patch.object(e,'candles',side_effect=fetch):s.monitor()
                self.assertEqual(len([o for o in e.orders.values() if not o['reduceOnly']]),1 if late else 2,s.state['events'][-7:])

    def test_full_market_worker_dispatch_uses_half_hour_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1801];s,e,cfg,feed,runner,rows,lower=self.fixture(tmp,now)
            def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
            # First account source sample establishes its execution cursor; a later
            # hour's current signal is actionable without a new intrabar crossing.
            with patch.object(e,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]):
                runner.cycle(paced=False)
                now[0]+=3600;rows=[{**r,'time':r['time']+3600} for r in rows];lower=[{**r,'time':r['time']+3600} for r in lower]
                runner.cycle(paced=False)
            self.assertTrue(e.orders,(runner.health,s.state['events'][-5:]))
            self.assertIn(':30',runner.health['note'])
