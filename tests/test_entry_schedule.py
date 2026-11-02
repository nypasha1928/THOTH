"""Entry-only schedule gates, real NY timezone conversion and synthetic exchange."""
import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from thoth.entry_schedule import reason,status
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from test_testnet import Exchange

NY=ZoneInfo('America/New_York')
def at(value,fold=0):return datetime.datetime.fromisoformat(value).replace(tzinfo=NY,fold=fold).timestamp()
def cfg(**kw):return TestnetSettings(**dict(entry_daily_enabled=True,entry_weekend_enabled=True,**kw))

class EntryScheduleTests(unittest.TestCase):
    def test_agreed_weekly_and_daily_boundaries(self):
        c=cfg()
        for value,blocked in [('2026-10-30T23:59:59',False),('2026-10-31T00:00:00',True),('2026-11-01T23:59:59',True),('2026-11-02T00:00:00',True),('2026-11-02T10:44:59',True),('2026-11-02T10:45:00',False),('2026-11-03T03:44:59',False),('2026-11-03T03:45:00',True),('2026-11-03T10:45:00',False)]:
            with self.subTest(value=value):self.assertEqual(bool(reason(c,at(value))),blocked)
        self.assertEqual(status(c,at('2026-10-31T12:00:00'))['next_open_at'],at('2026-11-02T10:45:00'))
        self.assertEqual(status(c,at('2026-11-02T11:00:00'))['next_block_at'],at('2026-11-03T03:45:00'))

    def test_scope_disabled_validation_and_overnight_daily_window(self):
        self.assertFalse(reason(Settings(),at('2026-11-01T12:00:00')))
        self.assertFalse(reason(cfg(active_symbol_list='tradfi'),at('2026-11-01T12:00:00')))
        self.assertTrue(reason(cfg(active_symbol_list='tradfi',entry_schedule_scope='all'),at('2026-11-01T12:00:00')))
        c=TestnetSettings(entry_daily_enabled=True,entry_daily_start='22:00',entry_daily_end='02:00')
        for time,blocked in [('2026-11-03T21:59:59',False),('2026-11-03T22:00:00',True),('2026-11-04T01:59:59',True),('2026-11-04T02:00:00',False)]:self.assertEqual(bool(reason(c,at(time))),blocked)
        for changes in [dict(entry_daily_start='25:00'),dict(entry_daily_enabled=True,entry_daily_end='03:45'),dict(entry_weekend_start_day=7),dict(entry_weekend_enabled=True,entry_weekend_start_day=0,entry_weekend_start='10:45')]:
            with self.assertRaises(ValueError):Settings(**changes)

    def test_dst_repeated_and_missing_hours(self):
        c=TestnetSettings(entry_daily_enabled=True,entry_daily_start='01:15',entry_daily_end='01:45')
        for fold in (0,1):
            self.assertTrue(reason(c,at('2026-11-01T01:30:00',fold)))
            self.assertEqual(status(c,at('2026-11-01T01:30:00',fold))['next_open_at'],at('2026-11-01T01:45:00',fold))
        c=TestnetSettings(entry_daily_enabled=True,entry_daily_start='01:30',entry_daily_end='02:30')
        self.assertEqual(status(c,at('2026-03-08T01:59:00'))['next_open_at'],at('2026-03-08T03:00:00'))

    def create(self,tmp,now,**kw):
        exchange=Exchange(lambda:now[0])
        service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        c=TestnetSettings(dedicated_account=True,daily_loss_pct=90,entry_daily_enabled=True,**kw)
        service.configure(c);service.activate(service.confirmation)
        return service,exchange,c

    def signal(self,service,c,stamp,signal):
        settings=Settings(**c.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
        service.apply_signals(settings,{'BTCUSDT':dict(time=stamp,mark=100,signal=signal)})

    def test_block_reopen_and_manual_pause_are_separate_from_arm_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T04:01:00')];s,e,c=self.create(tmp,now)
            self.signal(s,c,now[0]-1,None);self.signal(s,c,now[0],'Long')
            self.assertFalse(e.orders);self.assertTrue(s.state['entries']);self.assertTrue(s.snapshot()['entry_schedule']['blocked'])
            now[0]=at('2026-11-03T10:45:00');self.signal(s,c,now[0],None);self.assertFalse(e.orders)
            now[0]+=1;self.signal(s,c,now[0],'Long');self.assertEqual(len(e.orders),1)
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T04:01:00')];s,e,c=self.create(tmp,now);s.pause()
            now[0]=at('2026-11-03T10:45:00');s.monitor()
            self.assertFalse(s.state['entries']);self.assertFalse(e.orders)

    def test_opposite_signal_still_closes_during_block_but_never_reverses(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')];s,e,c=self.create(tmp,now)
            self.signal(s,c,now[0]-1,None);self.signal(s,c,now[0],'Long')
            first=next(iter(e.orders));e.fill(first);s.monitor()
            now[0]=at('2026-11-03T03:45:00');self.signal(s,c,now[0],'Short')
            closes=[o for o in e.orders.values() if o['reduceOnly']]
            self.assertEqual(len(closes),1);self.assertFalse(s.state['reversals'])
            e.fill(closes[0]['orderLinkId']);s.monitor();self.assertEqual(len(e.orders),2)
            self.assertTrue(s.state['entries']);self.assertFalse(s.state['owned'])

    def test_window_crossed_during_reconciliation_is_cancelled_before_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:59')];s,e,c=self.create(tmp,now)
            original=e.reconcile
            def delayed(symbol,link):
                result=original(symbol,link);now[0]=at('2026-11-03T03:45:00');return result
            with patch.object(e,'reconcile',side_effect=delayed):s._entry('BTCUSDT','Long',now[0],'fixture',c)
            self.assertFalse(e.orders)
            record=s.journal.records()[0];self.assertEqual(record['phase'],'cancelled');self.assertTrue(record['result']['not_submitted'])
            self.assertFalse(any(path=='/v5/order/create' for _,path,_ in e.calls))

    def test_restart_does_not_enable_entries_or_lose_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')];s,e,c=self.create(tmp,now)
            now[0]=at('2026-11-03T10:45:00')
            recovered=TestnetSupervisor(s.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            self.assertFalse(recovered.state['entries']);self.assertTrue(recovered.state['config']['entry_daily_enabled'])
            self.assertFalse(recovered.snapshot()['entry_schedule']['blocked'])

    def test_delayed_entry_permission_does_not_arm_during_schedule_block(self):
        from thoth.testnet_feed import FeedAdapter
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T04:01:00')];s,e,c=self.create(tmp,now)
            feed=FeedAdapter(s);self.assertFalse(feed.entry_delay_allowed('BTCUSDT'))
            now[0]=at('2026-11-03T10:45:00');self.assertTrue(feed.entry_delay_allowed('BTCUSDT'))

    def test_position_trailing_continues_during_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,coin_pnl_trailing_enabled=True,coin_pnl_trailing_activation_dollars=.1,coin_pnl_trailing_drop_dollars=.05,sma_exit_enabled=False)
            self.signal(s,c,now[0]-1,None);self.signal(s,c,now[0],'Long')
            first=next(iter(e.orders));e.fill(first);s.monitor()
            now[0]=at('2026-11-03T04:01:00');e.prices['BTCUSDT']=101;s.monitor()
            e.prices['BTCUSDT']=100.8;s.monitor()
            closes=[o for o in e.orders.values() if o['reduceOnly']]
            self.assertEqual(len(closes),1);self.assertTrue(s.state['owned']['BTCUSDT']['coin_pnl_trailing']['triggered'])

    def test_local_cancellation_does_not_consume_hourly_entry_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:00:59')]
            s,e,c=self.create(tmp,now,strategy='ob_sr_minute_hourly',entry_daily_start='03:01')
            original=e.reconcile
            def delayed(symbol,link):
                found=original(symbol,link);now[0]=at('2026-11-03T03:01:00');return found
            with patch.object(e,'reconcile',side_effect=delayed):s._entry('BTCUSDT','Long',now[0],'fixture',c)
            self.assertFalse(e.orders);self.assertEqual(s._minute_hour_symbols(),set())
            record=s.journal.records()[0];self.assertTrue(record['result']['not_submitted'])
            # Unknown submissions still reserve capacity; never infer no exposure.
            s.journal.update(record['link'],'unknown',{})
            self.assertEqual(s._minute_hour_symbols(),{'BTCUSDT'})

    def test_queued_reversal_settling_inside_window_is_not_replayed_at_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:43:00')];s,e,c=self.create(tmp,now)
            self.signal(s,c,now[0]-1,None);self.signal(s,c,now[0],'Long')
            first=next(iter(e.orders));e.fill(first);s.monitor()
            now[0]=at('2026-11-03T03:44:59');self.signal(s,c,now[0],'Short')
            self.assertTrue(s.state['reversals'])
            close=next(o for o in e.orders.values() if o['reduceOnly'])
            now[0]=at('2026-11-03T03:45:00');e.fill(close['orderLinkId']);s.monitor()
            self.assertFalse(s.state['reversals']);self.assertEqual(len(e.orders),2)
            now[0]=at('2026-11-03T10:45:00');self.signal(s,c,now[0],None)
            self.assertEqual(len(e.orders),2)

    def open_position(self,s,e,c,now):
        self.signal(s,c,now[0]-1,None);self.signal(s,c,now[0],'Long')
        link=next(iter(e.orders));e.fill(link);s.monitor();return link

    def test_cutoff_closes_paused_positions_and_blocks_until_settled(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.open_position(s,e,c,now);s.pause()
            now[0]=at('2026-11-03T03:45:00');s.monitor()
            closes=[o for o in e.orders.values() if o['reduceOnly']]
            self.assertEqual(len(closes),1)
            self.assertIn('03:45',s.state['owned']['BTCUSDT']['reason'])
            self.assertTrue(s.snapshot()['entry_schedule']['flatten_pending'])
            now[0]=at('2026-11-03T10:55:00');s.monitor()
            self.assertEqual(len(e.orders),2)  # Reconcile existing close, never duplicate.
            self.assertTrue(s.snapshot()['entry_schedule']['blocked'])
            e.fill(closes[0]['orderLinkId']);s.monitor();s.monitor()
            self.assertFalse(s.state['owned']);self.assertFalse(s.state['schedule_exit']['active'])
            self.assertFalse(s.state['entries'])  # Never undo the operator's manual pause.

    def test_cutoff_closes_all_owned_positions_before_account_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.open_position(s,e,c,now)
            s._entry('ETHUSDT','Long',now[0]+1,'fixture',c)
            eth=next(o for o in e.orders.values() if o['symbol']=='ETHUSDT');e.fill(eth['orderLinkId']);s.monitor()
            self.assertFalse(any(o['reduceOnly'] for o in e.orders.values()))
            original=e.request
            def unavailable(method,path,params,private=False):
                if path=='/v5/account/wallet-balance':raise ConnectionError('fixture wallet down')
                return original(method,path,params,private)
            now[0]=at('2026-11-03T03:45:00')
            with patch.object(e,'request',side_effect=unavailable):
                with self.assertRaises(ConnectionError):s.monitor()
            self.assertEqual({o['symbol'] for o in e.orders.values() if o['reduceOnly']},{'BTCUSDT','ETHUSDT'})
            self.assertTrue(s.state['schedule_exit']['active'])

    def test_restart_recovers_cutoff_close_without_duplicate_or_autoactivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.open_position(s,e,c,now)
            now[0]=at('2026-11-03T03:45:00');s.monitor()
            recovered=TestnetSupervisor(s.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
            recovered.monitor()
            self.assertTrue(recovered.state['schedule_exit']['active'])
            self.assertEqual(len(e.orders),2);self.assertFalse(recovered.state['entries'])

    def test_missed_cutoff_closes_carryover_but_not_new_positions_after_reopening(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.open_position(s,e,c,now)
            now[0]=at('2026-11-03T10:55:00');s.monitor()
            close=next(o for o in e.orders.values() if o['reduceOnly'])
            e.fill(close['orderLinkId']);s.monitor();s.monitor()
            now[0]+=1;s._entry('ETHUSDT','Long',now[0],'fixture',c)
            eth=next(o for o in e.orders.values() if o['symbol']=='ETHUSDT');e.fill(eth['orderLinkId']);s.monitor()
            self.assertEqual(len([o for o in e.orders.values() if o['reduceOnly']]),1)
            self.assertFalse(s.state['schedule_exit']['active'])

    def test_pending_entry_cancelled_at_cutoff_and_late_fill_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.signal(s,c,now[0]-1,None);self.signal(s,c,now[0],'Long')
            link=next(iter(e.orders))
            now[0]=at('2026-11-03T03:45:00');s.monitor()
            self.assertEqual(e.orders[link]['orderStatus'],'Cancelled')
            e.fill(link);s.monitor()  # Fill races with cancellation at the exchange.
            self.assertEqual(len([o for o in e.orders.values() if o['reduceOnly']]),1)
            self.assertTrue(s.state['schedule_exit']['active'])

    def test_cutoff_does_not_close_unowned_positions_or_allow_0355_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.open_position(s,e,c,now)
            e.active['ETHUSDT']=dict(size='1',side='Buy',positionIdx=0,avgPrice='100',stopLoss='99',liqPrice='0')
            now[0]=at('2026-11-03T03:45:00');s.monitor()
            self.assertEqual([o['symbol'] for o in e.orders.values() if o['reduceOnly']],['BTCUSDT'])
            now[0]=at('2026-11-03T03:55:00');s._entry('SOLUSDT','Long',now[0],'fixture',c)
            self.assertEqual(len(e.orders),2)
            self.assertTrue(s.snapshot()['entry_schedule']['blocked'])

    def test_failed_close_keeps_obligation_and_does_not_skip_other_coins(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[at('2026-11-03T03:44:00')]
            s,e,c=self.create(tmp,now,entry_daily_exit_enabled=True)
            self.open_position(s,e,c,now)
            s._entry('ETHUSDT','Long',now[0]+1,'fixture',c)
            eth=next(o for o in e.orders.values() if o['symbol']=='ETHUSDT');e.fill(eth['orderLinkId']);s.monitor()
            original=s._close
            def failed(symbol,position,reason):
                if symbol=='BTCUSDT':raise ConnectionError('fixture close unavailable')
                return original(symbol,position,reason)
            now[0]=at('2026-11-03T03:45:00')
            with patch.object(s,'_close',side_effect=failed):s.monitor()
            self.assertEqual([o['symbol'] for o in e.orders.values() if o['reduceOnly']],['ETHUSDT'])
            self.assertIn('BTCUSDT',s.state['schedule_exit']['errors'])
            self.assertTrue(s.state['schedule_exit']['active'])
            s.monitor()
            self.assertEqual({o['symbol'] for o in e.orders.values() if o['reduceOnly']},{'BTCUSDT','ETHUSDT'})
