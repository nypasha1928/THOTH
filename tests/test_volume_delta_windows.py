import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.volume_delta_windows import STRATEGY,window_start,context,fresh,evaluate
from thoth.closed_sma7_14_exit import observe
from thoth.intrabar_exit import ExitDataError
from thoth.runner import Runner
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from test_testnet import Exchange
from test_volume_delta_midpoint import BAR,data


def minutes(now,price=99):
    latest=int(now)//60*60-60
    return [dict(time=latest-(30-i)*60,close=100 if i<30 else price) for i in range(31)]+[dict(time=latest+60,close=1000)]

class WindowTests(unittest.TestCase):
    def test_boundaries_and_source_hour_expiration(self):
        self.assertEqual(window_start(BAR+3480),BAR+3480)
        self.assertEqual(window_start(BAR+3600),BAR+3480)
        self.assertEqual(window_start(BAR+3779),BAR+3480)
        self.assertIsNone(window_start(BAR+3780))
        self.assertEqual(window_start(BAR+1680),BAR+1680)
        self.assertIsNone(window_start(BAR+1980))
        cfg=Settings(strategy=STRATEGY,timeframe='D',sma_exit_enabled=True)
        self.assertEqual(cfg.timeframe,'60');self.assertFalse(cfg.sma_exit_enabled)
        rows,_=data();ctx=context(rows,cfg,BAR+3599)
        self.assertTrue(fresh(ctx,cfg,BAR+3599));self.assertFalse(fresh(ctx,cfg,BAR+3600))
    def test_cross_is_closed_only_fresh_and_post_entry(self):
        now=BAR+1861
        for side,price in [('Buy',99),('Sell',101)]:
            tracker={};rows=minutes(now,price)
            self.assertIn('crossed',observe(tracker,rows,side,now,BAR+1801,sampled_at=now))
            self.assertIsNone(observe(tracker,rows,side,now,BAR+1801,sampled_at=now))
            self.assertIsNone(observe({},rows,side,now,BAR+1860,sampled_at=now))
        self.assertIsNone(observe({},minutes(now,100),'Buy',now,BAR,sampled_at=now))
        rows=minutes(now);rows[0]['close']=200
        self.assertIsNone(observe({},rows,'Buy',now,BAR,sampled_at=now)) # already below, no new cross
        with self.assertRaises(ExitDataError):observe({},minutes(now)[1:],'Buy',now,BAR,sampled_at=now)
        with self.assertRaises(ExitDataError):observe({},minutes(now),'Buy',now+60,BAR,sampled_at=now)
    def test_crossing_sma14_alone_does_not_exit(self):
        now=BAR+1861
        for side,older,last in [('Buy',80,99),('Sell',120,101)]:
            rows=minutes(now,last)
            for row in rows[:17]:row['close']=older
            tracker={}
            self.assertIsNone(observe(tracker,rows,side,now,BAR,sampled_at=now))
            self.assertIn('sma30',tracker)

    def fixture(self,tmp,now):
        e=Exchange(lambda:now[0]);s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        s.configure(TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,daily_loss_pct=90,max_positions=1))
        s.activate(s.confirmation);cfg=TestnetSettings(**s.state['config']);feed=FeedAdapter(s);runner=Runner(feed,fetch=feed.fetch)
        return s,e,cfg,feed,runner
    def test_waits_for_qualifying_then_persists_window_decision_and_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1681];s,e,cfg,feed,runner=self.fixture(tmp,now);rows,lower=data();lower=lower[:29]
            feed.volume_delta_data['BTCUSDT']=dict(interval='60',bar=BAR,rows=lower)
            quiet,_=data('None')
            self.assertIsNone(runner.volume_delta_windows_observation('BTCUSDT',cfg,quiet,now[0])['signal'])
            now[0]+=1;ob=runner.volume_delta_windows_observation('BTCUSDT',cfg,rows,now[0]);self.assertEqual(ob['signal'],'Long')
            self.assertIsNone(Runner(feed,fetch=feed.fetch).volume_delta_windows_observation('BTCUSDT',cfg,rows,now[0]+1)['signal'])
            def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
            with patch.object(e,'candles',side_effect=fetch):s._entry('BTCUSDT','Long',now[0],'test',cfg,ob['entry_context'])
            self.assertEqual(len(e.orders),1,s.state['events'][-3:]);self.assertTrue(s._volume_window_used('BTCUSDT',BAR+1680))
            e.fill(next(iter(e.orders)))
            with patch.object(e,'candles',return_value=minutes(now[0],100)):s.monitor()
            self.assertEqual(s.state['owned']['BTCUSDT']['exit_timeframe'],'1')
            # Full means an opposite signal does not close this existing position.
            conf=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
            s.apply_signals(conf,{'BTCUSDT':dict(time=now[0],signal=None,mark=101)})
            now[0]+=1
            s.apply_signals(conf,{'BTCUSDT':dict(time=now[0],signal='Short',mark=101,entry_context=context(rows,cfg,now[0]))})
            self.assertFalse(s.state['reversals']);self.assertNotIn('signal_exit',s.state['owned']['BTCUSDT']);self.assertEqual(len(e.orders),1)
            s.pause();now[0]=BAR+1741
            with patch.object(e,'candles',return_value=minutes(now[0])):s.monitor()
            self.assertEqual(len([o for o in e.orders.values() if o['reduceOnly']]),1)
    def test_available_capacity_closes_reconciles_then_reverses(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+1681];s,e,cfg,feed,runner=self.fixture(tmp,now)
            cfg=cfg.model_copy(update={'max_positions':2});s.pause();s.configure(cfg);s.activate(s.confirmation);cfg=TestnetSettings(**s.state['config'])
            rows,lower=data();lower=lower[:29]
            def fetch(symbol,interval,limit=1000,**kw):return rows[-limit:] if interval=='60' else lower[-limit:]
            with patch.object(e,'candles',side_effect=fetch):s._entry('BTCUSDT','Long',now[0],'first',cfg,context(rows,cfg,now[0]))
            e.fill(next(iter(e.orders)))
            with patch.object(e,'candles',return_value=minutes(now[0],100)):s.monitor()
            conf=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
            s.apply_signals(conf,{'BTCUSDT':dict(time=now[0],signal=None,mark=101)})
            now[0]=BAR+3481;rows,lower=data('Short');lower=[{**lower[0],'time':BAR+i*60} for i in range(59)]
            with patch.object(e,'candles',side_effect=fetch):
                s.apply_signals(conf,{'BTCUSDT':dict(time=now[0],signal='Short',mark=99,entry_context=context(rows,cfg,now[0]))})
                self.assertEqual(len(e.orders),2);self.assertTrue(s.state['reversals'])
                closing=[o for o in e.orders.values() if o['reduceOnly']][0]
                e.fill(closing['orderLinkId']);s.monitor()
            self.assertEqual(len(e.orders),3,s.state['events'][-5:])
            self.assertEqual(list(e.orders.values())[-1]['side'],'Sell')
    def test_cross_hour_window_does_not_reset_consumed_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+3481];s,e,cfg,feed,runner=self.fixture(tmp,now);rows,lower=data()
            lower=[{**lower[0],'time':BAR+i*60} for i in range(59)]
            feed.volume_delta_data['BTCUSDT']=dict(interval='60',bar=BAR,rows=lower)
            self.assertEqual(runner.volume_delta_windows_observation('BTCUSDT',cfg,rows,now[0])['signal'],'Long')
            now[0]=BAR+3601;rows=[{**r,'time':r['time']+3600} for r in rows]
            feed.volume_delta_data['BTCUSDT']=dict(interval='60',bar=BAR+3600,rows=[{**lower[0],'time':BAR+3600}])
            self.assertIsNone(Runner(feed,fetch=feed.fetch).volume_delta_windows_observation('BTCUSDT',cfg,rows,now[0])['signal'])
