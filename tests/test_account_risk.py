import unittest
from datetime import datetime,timezone
from thoth.account_risk import new_risk,sample,closure
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
