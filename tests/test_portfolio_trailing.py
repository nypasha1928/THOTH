import unittest
from thoth.portfolio_trailing import new_state,advance


class PortfolioTrailingMath(unittest.TestCase):
    def step(self,state,pnl,**kw):
        return advance(state,pnl,kw.get('exposure',True),kw.get('enabled',True),1,.1,123)

    def test_both_requested_examples_and_inclusive_boundaries(self):
        for peak,exit_at in [('1.00','.90'),('1.50','1.40')]:
            s=self.step(new_state(),'.99');self.assertFalse(s['active'])
            s=self.step(s,'1');self.assertTrue(s['active'])
            s=self.step(s,peak);self.assertFalse(s['triggered'])
            s=self.step(s,str(float(exit_at)+.001));self.assertFalse(s['triggered'])
            s=self.step(s,exit_at);self.assertTrue(s['triggered'])
            self.assertEqual(s['triggered_at'],123)

    def test_net_profit_losses_peak_and_flat_cycle(self):
        s=self.step(new_state(),'1.5');s=self.step(s,'1.45')
        self.assertEqual(s['peak'],'1.5')
        self.assertFalse(s['triggered'])
        self.assertEqual(self.step(s,0,exposure=False),new_state())
        self.assertEqual(self.step(s,0,enabled=False),new_state())
        s=self.step(s,'1.4')
        self.assertEqual(self.step(s,0,exposure=False),s)
        self.assertEqual(self.step(s,3,enabled=False),s)

    def test_invalid_samples_do_not_mutate_last_good_peak(self):
        s=self.step(new_state(),1.5)
        for bad in (None,'nan','inf','not a price'):
            with self.assertRaises(ValueError):self.step(s,bad)
        self.assertEqual(s['peak'],'1.5')


import tempfile
from pathlib import Path
from unittest.mock import patch
from thoth.models import TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.bybit_testnet import dec
import test_testnet as fixtures


class PortfolioTrailingIntegration(unittest.TestCase):
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1792152000.]
        self.exchange=fixtures.Exchange(lambda:self.now[0]);self.path=Path(self.temp.name)/'pnl.sqlite'
        self.service=self.create()
        self.cfg=TestnetSettings(dedicated_account=True,pnl_trailing_enabled=True,
            pnl_trailing_activation_dollars=1,pnl_trailing_drop_dollars=.1,daily_loss_pct=50)
        self.service.configure(self.cfg)

    def create(self):
        return TestnetSupervisor(self.path,enabled=True,password_configured=True,
            client_factory=lambda *a,**kw:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])

    def tearDown(self):self.temp.cleanup()

    def test_combined_winners_and_losers_close_all_then_next_signal_can_enter(self):
        self.enter();self.enter('ETHUSDT')
        self.signal(self.now[0]-60,None,'SOLUSDT')
        self.exchange.prices.update(BTCUSDT=104,ETHUSDT=99)
        self.service.monitor()
        self.assertEqual(dec(self.service.snapshot()['pnl_trailing']['peak']),dec('1.5'))
        self.exchange.prices['BTCUSDT']=103.8;self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual({o['symbol'] for o in closes},{'BTCUSDT','ETHUSDT'})
        self.assertTrue(self.service.state['entries'])
        self.assertTrue(self.service.snapshot()['closing'])
        self.assertTrue(all(o['reduceOnly'] for o in closes))
        self.now[0]+=60;self.signal(self.now[0]-60,'Long','SOLUSDT')
        self.assertFalse(any(o['symbol']=='SOLUSDT' for o in self.exchange.orders.values()))
        self.fill_closes()
        self.assertFalse(self.service.snapshot()['closing'])
        self.assertFalse(self.service.snapshot()['pnl_trailing']['active'])
        self.assertTrue(self.service.state['entries'])
        self.now[0]+=60;self.signal(self.now[0]-60,'Long','SOLUSDT')
        self.assertTrue(any(o['symbol']=='SOLUSDT' and not o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_peak_survives_restart_and_paused_entries_are_not_enabled(self):
        self.enter();self.exchange.prices['BTCUSDT']=103;self.service.monitor()
        self.service=self.create();self.activate_recovery()
        self.assertEqual(dec(self.service.state['pnl_trailing']['peak']),dec('1.5'))
        self.exchange.prices['BTCUSDT']=102.8;self.service.monitor();self.fill_closes()
        self.assertFalse(self.service.state['entries'])
        self.assertFalse(self.service.snapshot()['closing'])

    def activate_recovery(self):self.service.activate('ENABLE TESTNET ORDERS',recovery=True)

    def test_durable_flatten_intent_survives_close_failure_and_disable(self):
        self.enter();self.exchange.prices['BTCUSDT']=102;self.service.monitor()
        self.exchange.prices['BTCUSDT']=101.8
        with patch.object(self.service,'_cancel_entries',side_effect=RuntimeError('network unavailable')):
            with self.assertRaises(RuntimeError):self.service.monitor()
        saved=self.service.load()
        self.assertEqual(saved['flatten_reason'],'Open P&L trailing stop')
        self.assertTrue(saved['pnl_trailing']['triggered'])
        self.service.pause()
        self.service.configure(self.cfg.model_copy(update={'pnl_trailing_enabled':False}))
        self.service.monitor()
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.fill_closes();self.assertFalse(self.service.state['entries'])

    def test_unrelated_settings_preserve_peak_and_daily_stop_keeps_priority(self):
        self.enter();self.exchange.prices['BTCUSDT']=103;self.service.monitor();self.service.pause()
        self.service.configure(self.cfg.model_copy(update={'max_positions':4}))
        self.assertEqual(dec(self.service.state['pnl_trailing']['peak']),dec('1.5'))
        self.service.state['risk']['daily_stop']=True;self.service.save()
        self.exchange.prices['BTCUSDT']=102.8;self.service.monitor();self.fill_closes()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'daily_stop')

    def test_late_entry_fill_is_also_closed_before_new_cycle(self):
        self.enter();self.signal(self.now[0]-60,None,'ETHUSDT')
        self.now[0]+=60;self.signal(self.now[0]-60,'Long','ETHUSDT')
        pending=next(k for k,o in self.exchange.orders.items() if o['symbol']=='ETHUSDT' and not o['reduceOnly'])
        self.exchange.prices['BTCUSDT']=103;self.service.monitor()
        # Exchange accepts an entry fill despite an in-flight cancellation.
        with patch.object(self.service,'_cancel_entries',return_value=None):
            self.exchange.prices['BTCUSDT']=102.8;self.service.monitor()
            self.fill_closes()
            self.assertTrue(self.service.snapshot()['closing'])
            self.exchange.fill(pending);self.service.monitor()
            self.assertTrue(any(o['symbol']=='ETHUSDT' and o['reduceOnly'] for o in self.exchange.orders.values()))
            self.fill_closes()
        self.assertFalse(self.service.snapshot()['closing'])
        self.assertTrue(self.service.state['entries'])
        self.assertFalse(self.exchange.active)
