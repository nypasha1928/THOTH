import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from thoth.equity_trailing import window,advance,active_locks
from thoth.models import TestnetSettings
from thoth.testnet import TestnetSupervisor
import test_testnet as fixtures

def utc(text):return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()

class EquityPeriodMath(unittest.TestCase):
    def test_calendar_windows(self):
        now=utc('2026-10-28T12:00:00')
        self.assertEqual(window('weekly',now),(utc('2026-10-26T00:00:00'),utc('2026-11-02T00:00:00')))
        self.assertEqual(window('daily',now),(utc('2026-10-28T00:00:00'),utc('2026-10-29T00:00:00')))
        for period in ('daily','three_day','weekly'):
            start,end=window(period,now);self.assertLessEqual(start,now);self.assertGreater(end,now)
            self.assertEqual(window(period,end)[0],end)
        self.assertEqual(window('three_day',0),(0,259200))

    def test_peak_inclusive_drop_pause_survives_disable_and_expires(self):
        now=utc('2026-10-28T12:00:00')
        for period in ('daily','three_day','weekly'):
            state=advance({},100,0,True,5,period,now)
            state=advance(state,150,0,True,5,period,now+5)
            self.assertFalse(advance(state,145.01,0,True,5,period,now+10)['triggered'])
            state=advance(state,145,0,True,5,period,now+10)
            self.assertTrue(state['triggered']);self.assertEqual(state['remaining'],'0')
            self.assertEqual(advance(state,200,0,False,100,period,now+15),state)
            self.assertTrue(active_locks({period:state},now+20))
            self.assertFalse(active_locks({period:state},state['period_end']))
            new=advance(state,140,0,True,5,period,state['period_end'])
            self.assertFalse(new['triggered']);self.assertEqual(new['peak'],'140')

    def test_transfers_do_not_move_profit_peak_or_trigger_loss(self):
        now=utc('2026-10-28T12:00:00');s=advance({},100,0,True,5,'weekly',now)
        s=advance(s,150,50,True,5,'weekly',now+5)
        self.assertEqual(s['peak'],'100');self.assertFalse(s['triggered'])
        s=advance(s,120,20,True,5,'weekly',now+10)
        self.assertEqual(s['adjusted'],'100');self.assertFalse(s['triggered'])
        s=advance(s,115,20,True,5,'weekly',now+15);self.assertTrue(s['triggered'])
        self.assertEqual(s['stop_equity'],'115')
        with self.assertRaises(ValueError):advance({},'nan',0,True,5,'weekly',now)
        with self.assertRaises(ValueError):advance(s,120,20,True,5,'weekly',now-1)

class EquityPeriodIntegration(unittest.TestCase):
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[utc('2026-10-28T12:00:00')]
        self.exchange=fixtures.Exchange(lambda:self.now[0]);self.path=Path(self.temp.name)/'account.sqlite'
        self.service=self.create();self.cfg=TestnetSettings(dedicated_account=True,daily_loss_pct=90,
            weekly_equity_trailing_enabled=True,weekly_equity_trailing_drop_dollars=1)
        self.service.configure(self.cfg)

    def create(self):return TestnetSupervisor(self.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])
    def tearDown(self):self.temp.cleanup()

    def test_equity_profit_drop_closes_all_and_holds_through_restart_disable(self):
        self.enter();self.enter('ETHUSDT')
        self.exchange.prices.update(BTCUSDT=104,ETHUSDT=102);self.now[0]+=5;self.service.monitor()
        self.assertEqual(self.service.state['equity_trailing']['weekly']['peak'],'103.0')
        self.exchange.prices.update(BTCUSDT=102,ETHUSDT=102);self.now[0]+=5;self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'equity_trailing_stop')
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual({o['symbol'] for o in closes},{'BTCUSDT','ETHUSDT'})
        self.fill_closes();self.assertFalse(self.service.snapshot()['closing'])
        with self.assertRaisesRegex(ValueError,'period ends'):self.activate()
        self.service.pause();self.service.configure(self.cfg.model_copy(update={'weekly_equity_trailing_enabled':False}))
        self.service=self.create();self.service.activate('ENABLE TESTNET ORDERS',recovery=True)
        with self.assertRaisesRegex(ValueError,'period ends'):self.activate()
        self.assertFalse(self.service.state['entries'])
        end=self.service.state['equity_trailing']['weekly']['period_end'];self.now[0]=end+1
        self.service.monitor();self.assertFalse(self.service.state['entries'])
        self.activate();self.assertTrue(self.service.state['entries'])

    def test_deposit_withdrawal_duplicates_and_daily_rollover_preserve_weekly_peak(self):
        self.activate();initial=self.service.state['equity_trailing']['weekly']['peak']
        def flow(identity,change):
            self.now[0]+=5;self.exchange.balance+=change
            row=dict(id=identity,currency='USDT',type='TRANSFER_IN' if change>0 else 'TRANSFER_OUT',change=str(change),transactionTime=str(int(self.now[0]*1000)))
            self.exchange.logs.extend([row,row]);self.service.monitor()
        flow('deposit',50);flow('withdrawal',-30)
        state=self.service.state['equity_trailing']['weekly']
        self.assertEqual(state['peak'],initial);self.assertFalse(state['triggered'])
        self.assertEqual(len(self.service.state['equity_cashflows']),2)
        # Rollover a day while monitoring continuously; weekly transfer adjustment remains.
        self.now[0]=utc('2026-10-28T23:59:55');self.service.monitor()
        self.now[0]=utc('2026-10-29T00:00:02');self.service.monitor()
        self.assertEqual(self.service.state['equity_trailing']['weekly']['peak'],initial)
        self.assertFalse(self.service.state['equity_trailing']['weekly']['triggered'])

    def test_overlapping_locks_and_flat_realized_losses(self):
        self.cfg=self.cfg.model_copy(update=dict(daily_equity_trailing_enabled=True,daily_equity_trailing_drop_dollars=1,
            three_day_equity_trailing_enabled=True,three_day_equity_trailing_drop_dollars=2))
        self.service.configure(self.cfg);self.activate()
        self.exchange.balance=99;self.now[0]+=5;self.service.monitor()
        self.assertEqual(set(active_locks(self.service.state['equity_trailing'],self.now[0])),{'daily','weekly'})
        self.exchange.balance=98;self.now[0]+=5;self.service.monitor()
        self.assertIn('three_day',active_locks(self.service.state['equity_trailing'],self.now[0]))
        self.now[0]=utc('2026-10-29T00:00:01');self.service.monitor()
        self.assertIn('weekly',active_locks(self.service.state['equity_trailing'],self.now[0]))
        with self.assertRaisesRegex(ValueError,'period ends'):self.activate()
        self.assertFalse(self.service.state['entries'])

    def test_durable_trigger_before_close_failure_and_no_extra_transfer_queries(self):
        self.enter();self.exchange.prices['BTCUSDT']=104;self.now[0]+=5
        before=sum(path=='/v5/account/transaction-log' for method,path,params in self.exchange.calls)
        self.service.monitor()
        after=sum(path=='/v5/account/transaction-log' for method,path,params in self.exchange.calls)
        self.assertEqual(after-before,1)
        self.exchange.prices['BTCUSDT']=102;self.now[0]+=5
        with patch.object(self.service,'_cancel_entries',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):self.service.monitor()
        saved=self.service.load();self.assertTrue(saved['equity_trailing']['weekly']['triggered'])
        self.assertFalse(saved['entries']);self.assertEqual(saved['flatten_reason'],'Weekly equity trailing stop')
        self.service.monitor();self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_automatic_resume_only_after_period_end_and_not_after_operator_pause(self):
        from thoth.account_risk import start_daily_period
        for manual_pause in (False,True):
            with self.subTest(manual_pause=manual_pause):
                # Independent service for each operator-intent scenario.
                with tempfile.TemporaryDirectory() as directory:
                    self.path=Path(directory)/'resume.sqlite';self.service=self.create()
                    self.exchange.logs=[];self.exchange.balance=100
                    self.service.configure(self.cfg);self.activate()
                    self.exchange.balance=99;self.now[0]+=5;self.service.monitor()
                    self.assertFalse(self.service.state['entries'])
                    if manual_pause:self.service.pause()
                    end=self.service.state['equity_trailing']['weekly']['period_end']
                    self.now[0]=end-5
                    start_daily_period(self.service.state['risk'],99,self.cfg,self.now[0]);self.service.save()
                    self.service.monitor();self.assertFalse(self.service.state['entries'])
                    self.now[0]=end+2;self.service.monitor()
                    self.assertEqual(self.service.state['entries'],not manual_pause)
                    self.assertFalse(self.service.state['pending_signals'])
                    self.assertFalse(self.exchange.orders)

    def test_invalid_cashflow_fails_without_advancing_cursor(self):
        self.activate();previous=self.service.state.get('equity_cashflow_cursor_ms')
        self.now[0]+=5;self.exchange.logs=[dict(id='bad',currency='USDT',type='TRANSFER_IN',change='10')]
        with self.assertRaises(KeyError):self.service.monitor()
        self.assertEqual(self.service.state.get('equity_cashflow_cursor_ms'),previous)
        self.assertFalse(self.service.state['equity_trailing']['weekly']['triggered'])

    def test_expired_period_waits_for_settlement_before_automatic_resume(self):
        from thoth.account_risk import start_daily_period
        self.enter();self.exchange.prices['BTCUSDT']=104;self.now[0]+=5;self.service.monitor()
        self.exchange.prices['BTCUSDT']=102;self.now[0]+=5;self.service.monitor()
        end=self.service.state['equity_trailing']['weekly']['period_end']
        self.now[0]=end-5
        start_daily_period(self.service.state['risk'],101,self.cfg,self.now[0]);self.service.save();self.service.monitor()
        self.now[0]=end+2;self.service.monitor()
        self.assertFalse(self.service.state['entries']);self.assertTrue(self.service.state['flatten_reason'])
        self.assertTrue(self.service.state['equity_trailing_resume_intent'])
        self.fill_closes();self.assertTrue(self.service.state['entries'])
        self.assertFalse(self.service.snapshot()['closing'])

    def test_resume_setting_can_keep_entries_paused_after_period_end(self):
        from thoth.account_risk import start_daily_period
        self.cfg=self.cfg.model_copy(update={'equity_trailing_resume_automatic':False})
        self.service.configure(self.cfg);self.activate()
        self.exchange.balance=99;self.now[0]+=5;self.service.monitor()
        end=self.service.state['equity_trailing']['weekly']['period_end']
        self.now[0]=end-5;start_daily_period(self.service.state['risk'],99,self.cfg,self.now[0]);self.service.save();self.service.monitor()
        self.now[0]=end+2;self.service.monitor()
        self.assertFalse(self.service.state['entries']);self.assertEqual(self.service.state['status'],'paused')
