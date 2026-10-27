"""Simulated account interruption tests; no real account or API calls."""
import unittest
import test_testnet as fixtures
from thoth.bybit_testnet import BybitError


class RateRecoveryTests(unittest.TestCase):
    setUp=fixtures.TestnetIntegration.setUp
    tearDown=fixtures.TestnetIntegration.tearDown
    create=fixtures.TestnetIntegration.create
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def limited(self):
        original=self.exchange.request
        def request(method,path,params,private=False):
            if path=='/v5/position/list':
                error=BybitError('fixture rate limit',10006);error.thoth_endpoint=path;error.thoth_method=method;raise error
            return original(method,path,params,private)
        self.exchange.request=request
        try:self.service.monitor()
        except Exception as error:self.service._monitor_failure(error)
        return original

    def test_read_interruption_waits_then_resumes_only_after_full_success(self):
        self.enter();original=self.limited()
        self.assertEqual(self.service.state['status'],'rate_limited')
        self.assertFalse(self.service.state['entries'])
        self.assertTrue(self.service.state['rate_limit_resume'])
        self.assertEqual(float(self.exchange.active['BTCUSDT']['stopLoss']),98)
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.exchange.request=original;self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'running')
        self.assertFalse(self.service.state['rate_limit_resume'])
        self.assertIsNone(self.service.state['monitor_error'])

    def test_manual_pause_during_rate_wait_prevents_automatic_reactivation(self):
        self.enter();original=self.limited();self.service.pause()
        self.exchange.request=original;self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'paused')

    def test_restart_never_restores_automatic_resume_intent(self):
        self.enter();original=self.limited()
        recovered=self.create()
        self.assertFalse(recovered.state['entries'])
        self.assertFalse(recovered.state['rate_limit_resume'])
        self.exchange.request=original

    def test_loss_limit_during_interruption_wins_over_automatic_resume(self):
        self.enter();original=self.limited()
        self.exchange.request=original;self.exchange.balance=96
        self.service.monitor()
        self.assertEqual(self.service.state['status'],'daily_stop')
        self.assertFalse(self.service.state['entries'])
        self.assertFalse(self.service.state['rate_limit_resume'])
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_account_ownership_mismatch_prevents_reactivation(self):
        self.enter();original=self.limited();self.exchange.request=original
        self.exchange.active['BTCUSDT']['side']='Sell'
        self.service.monitor()
        self.assertEqual(self.service.state['status'],'safety_lock')
        self.assertFalse(self.service.state['entries'])
        self.assertFalse(self.service.state['rate_limit_resume'])

    def test_settings_change_cancels_auto_resume(self):
        self.enter();original=self.limited()
        self.service.configure(self.cfg)
        self.exchange.request=original;self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'paused')

    def test_non_rate_monitor_fault_is_still_manual_safety_lock(self):
        self.enter()
        self.service._monitor_failure(TimeoutError('fixture'))
        self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'safety_lock')

    def test_monitor_failure_archive_survives_activity_churn_and_restart(self):
        self.enter()
        self.service._monitor_failure(KeyError('SECRET_RESPONSE'))
        for i in range(110):self.service.event('ordinary signal '+str(i))
        self.service.save()
        recovered=self.create()
        archive=recovered.snapshot()['monitor_failures']
        self.assertEqual(len(archive),1)
        self.assertEqual(archive[0]['details']['exception_class'],'KeyError')
        self.assertNotIn('SECRET_RESPONSE',str(archive))
        self.assertFalse(recovered.state['entries'])

    def test_repeated_rate_rejections_preserve_intent_but_another_fault_cancels_it(self):
        self.enter();original=self.limited()
        self.limited()
        self.assertTrue(self.service.state['rate_limit_resume'])
        self.service._monitor_failure(TimeoutError('fixture'))
        self.assertFalse(self.service.state['rate_limit_resume'])
        self.exchange.request=original;self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'safety_lock')

    def test_manual_close_all_cancels_auto_resume(self):
        self.enter();original=self.limited();self.exchange.request=original
        self.service.close_all();self.fill_closes();self.service.monitor()
        self.assertFalse(self.service.state['entries'])
        self.assertFalse(self.service.state['rate_limit_resume'])
        self.assertEqual(self.service.state['status'],'paused')

    def test_write_rate_rejection_does_not_enable_automatic_resume(self):
        self.enter()
        error=BybitError('fixture',10006);error.thoth_method='POST'
        self.service._monitor_failure(error)
        self.assertEqual(self.service.state['status'],'safety_lock')
        self.assertFalse(self.service.state['rate_limit_resume'])

    def test_wallet_failure_after_positions_recover_prevents_resume(self):
        self.enter();original=self.limited()
        def request(method,path,params,private=False):
            if path=='/v5/account/wallet-balance':raise TimeoutError('fixture')
            return original(method,path,params,private)
        self.exchange.request=request
        try:self.service.monitor()
        except Exception as error:self.service._monitor_failure(error)
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'safety_lock')
