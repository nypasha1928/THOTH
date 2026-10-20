import unittest
from datetime import datetime,timezone
from thoth.account_risk import new_risk,sample,closure,symbol_paused
from thoth.models import TestnetSettings


class AccountRiskTests(unittest.TestCase):
    def setUp(self):
        self.cfg=TestnetSettings()
        self.t=datetime(2026,10,16,12,tzinfo=timezone.utc).timestamp()

    def test_transfers_are_neutral_and_daily_stop_latches(self):
        risk=new_risk();sample(risk,100,0,self.cfg,self.t)
        self.assertEqual(sample(risk,107,10,self.cfg,self.t+1),'daily_stop')
        self.assertEqual(risk['loss_pct'],3)
        self.assertEqual(sample(risk,110,10,self.cfg,self.t+2),'daily_stop')

    def test_consecutive_losses_pause_once_and_reset_after_timeout(self):
        self.cfg=self.cfg.model_copy(update={'loss_streak_scope':'account'})
        risk=new_risk();sample(risk,100,0,self.cfg,self.t)
        for i in range(3):closure(risk,str(i),-1,self.cfg,self.t+i)
        until=risk['pause_until']
        self.assertFalse(closure(risk,'2',-1,self.cfg,self.t+5))
        self.assertEqual(sample(risk,100,0,self.cfg,self.t+5),'cooldown')
        self.assertEqual(risk['pause_until'],until)
        self.assertEqual(sample(risk,100,0,self.cfg,until+1),'ok')
        self.assertEqual(risk['streak'],0)

    def test_missed_midnight_blocks_until_flat_baseline_review(self):
        risk=new_risk();sample(risk,100,0,self.cfg,self.t)
        self.assertEqual(sample(risk,100,0,self.cfg,self.t+86400),'baseline_required')
        self.assertEqual(sample(risk,100,0,self.cfg,self.t+86401,initial=True),'ok')
        self.assertFalse(risk['baseline_required'])

    def test_continuous_midnight_sampling_rolls_baseline(self):
        now=datetime(2026,10,16,23,59,59,tzinfo=timezone.utc).timestamp()
        risk=new_risk();sample(risk,100,0,self.cfg,now)
        risk['daily_stop']=True
        self.assertEqual(sample(risk,97,0,self.cfg,now+2),'ok')
        self.assertEqual(risk['baseline'],97)

    def test_peak_150_uses_fixed_five_dollars_from_100_start(self):
        cfg=self.cfg.model_copy(update={'daily_loss_pct':5})
        risk=new_risk();sample(risk,100,0,cfg,self.t)
        self.assertEqual(risk['stop_equity'],95)
        self.assertEqual(sample(risk,150,0,cfg,self.t+1),'ok')
        self.assertEqual(risk['loss_allowance'],5)
        self.assertEqual(risk['stop_equity'],145)
        sample(risk,147,0,cfg,self.t+2)
        self.assertEqual(risk['peak'],150)
        self.assertEqual(risk['remaining'],2)
        self.assertEqual(risk['remaining_pct'],2)
        self.assertEqual(sample(risk,145,0,cfg,self.t+3),'daily_stop')
        self.assertEqual(risk['remaining'],0)
        self.assertEqual(sample(risk,150,0,cfg,self.t+4),'daily_stop')

    def test_deposits_and_withdrawals_do_not_move_adjusted_peak_or_budget(self):
        cfg=self.cfg.model_copy(update={'daily_loss_pct':5})
        risk=new_risk();sample(risk,100,0,cfg,self.t)
        sample(risk,150,0,cfg,self.t+1)
        sample(risk,250,100,cfg,self.t+2)
        self.assertEqual(risk['peak'],150)
        self.assertEqual(risk['loss_allowance'],5)
        self.assertEqual(risk['stop_equity'],245)
        self.assertEqual(sample(risk,245,100,cfg,self.t+3),'daily_stop')
        risk=new_risk();sample(risk,100,0,cfg,self.t)
        sample(risk,150,0,cfg,self.t+1);sample(risk,100,-50,cfg,self.t+2)
        self.assertEqual(risk['remaining'],5)
        self.assertEqual(risk['stop_equity'],95)

    def test_per_coin_losses_do_not_pause_account_or_other_coin(self):
        risk=new_risk();sample(risk,100,0,self.cfg,self.t)
        for i,symbol in enumerate(('ETHUSDT','HBARUSDT','XVGUSDT')):
            closure(risk,str(i),-1,self.cfg,self.t+i,symbol)
        self.assertEqual(sample(risk,100,0,self.cfg,self.t+5),'ok')
        self.assertFalse(symbol_paused(risk,'ETHUSDT',self.cfg,self.t+5))
        closure(risk,'4',-1,self.cfg,self.t+6,'ETHUSDT')
        closure(risk,'5',-1,self.cfg,self.t+7,'ETHUSDT')
        self.assertTrue(symbol_paused(risk,'ETHUSDT',self.cfg,self.t+8))
        self.assertFalse(symbol_paused(risk,'HBARUSDT',self.cfg,self.t+8))
        self.assertEqual(risk['symbols']['HBARUSDT']['streak'],1)
        closure(risk,'6',1,self.cfg,self.t+9,'HBARUSDT')
        self.assertEqual(risk['symbols']['HBARUSDT']['streak'],0)
        self.assertTrue(symbol_paused(risk,'ETHUSDT',self.cfg,self.t+10))
        until=risk['symbols']['ETHUSDT']['pause_until']
        self.assertFalse(symbol_paused(risk,'ETHUSDT',self.cfg,until+1))
        self.assertEqual(risk['symbols']['ETHUSDT']['streak'],0)

    def test_legacy_risk_preserves_latch_and_starts_from_verified_peak(self):
        risk=new_risk();sample(risk,100,0,self.cfg,self.t)
        for key in ('peak','loss_allowance','loss_used','remaining','remaining_pct','stop_equity','symbols'):risk.pop(key)
        risk['daily_stop']=True
        sample(risk,110,0,self.cfg,self.t+1)
        self.assertEqual(risk['peak'],110)
        self.assertEqual(risk['loss_allowance'],3)
        self.assertTrue(risk['daily_stop'])

    def test_midnight_discards_yesterdays_peak_loss_and_latch(self):
        cfg=self.cfg.model_copy(update={'daily_loss_pct':15})
        before=datetime(2026,10,19,23,59,58,tzinfo=timezone.utc).timestamp()
        risk=new_risk();sample(risk,61.52,0,cfg,before-5)
        sample(risk,70,0,cfg,before-4)
        sample(risk,54.71,0,cfg,before)
        self.assertTrue(risk['daily_stop'])
        self.assertGreater(risk['loss_used'],0)
        for i in range(3):closure(risk,'old-'+str(i),-1,cfg,before,'ETHUSDT')
        until=risk['symbols']['ETHUSDT']['pause_until']
        self.assertEqual(sample(risk,54,0,cfg,before+4),'ok')
        self.assertEqual(risk['day'],'2026-10-20')
        self.assertEqual(risk['baseline'],54)
        self.assertEqual(risk['peak'],54)
        self.assertEqual(risk['loss_used'],0)
        self.assertAlmostEqual(risk['remaining'],8.10)
        self.assertEqual(risk['remaining_pct'],15)
        self.assertAlmostEqual(risk['stop_equity'],45.90)
        self.assertEqual(risk['peak_ms'],risk['baseline_ms'])
        self.assertEqual(risk['symbols']['ETHUSDT']['pause_until'],until)
        # A loss realized today that was already marked at midnight is not charged twice.
        closure(risk,'overnight-position',-5,cfg,before+6,'BTCUSDT')
        sample(risk,54,0,cfg,before+6)
        self.assertEqual(risk['loss_used'],0)
        sample(risk,53,0,cfg,before+8)
        self.assertEqual(risk['loss_used'],1)
        self.assertAlmostEqual(risk['remaining'],7.10)

    def test_peak_time_tracks_new_high_not_recovery_below_peak(self):
        risk=new_risk();sample(risk,100,0,self.cfg,self.t)
        sample(risk,110,0,self.cfg,self.t+1)
        self.assertEqual(risk['peak_ms'],int((self.t+1)*1000))
        sample(risk,109,0,self.cfg,self.t+2)
        self.assertEqual(risk['peak_ms'],int((self.t+1)*1000))

    def test_midnight_reset_is_persistable_and_not_repeated_on_same_day(self):
        import json
        before=datetime(2026,10,19,23,59,59,tzinfo=timezone.utc).timestamp()
        risk=new_risk();sample(risk,100,0,self.cfg,before)
        sample(risk,98,0,self.cfg,before+2)
        baseline_ms=risk['baseline_ms']
        risk=json.loads(json.dumps(risk))
        sample(risk,97,0,self.cfg,before+10,initial=True)
        self.assertEqual(risk['baseline'],98)
        self.assertEqual(risk['baseline_ms'],baseline_ms)
        self.assertEqual(risk['loss_used'],1)
