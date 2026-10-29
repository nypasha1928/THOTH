"""Major-only execution policy, including saved settings and bypassed model copies."""
import unittest
from thoth.models import Settings
from thoth.intrabar_entry import observe_breakout
from thoth.sr_bounce import evaluate as bounce
from thoth.sr_volume import observe as volume_observe

class MajorOnlyTests(unittest.TestCase):
    def test_every_profile_normalizes_legacy_minor_selection(self):
        pattern=Settings.model_fields['strategy'].metadata[0].pattern
        for strategy in pattern.removeprefix('^(').removesuffix(')$').split('|'):
            with self.subTest(strategy=strategy):
                cfg=Settings(strategy=strategy,minor=True,inverse_direction_confirmed=True)
                self.assertFalse(cfg.minor)
                saved=Settings(**cfg.model_dump())
                self.assertFalse(saved.minor)
                self.assertEqual(saved.stop_dollars,cfg.stop_dollars)
                self.assertEqual(saved.opposite_action,cfg.opposite_action)
        self.assertTrue(Settings(strategy='sr_breakout',major=False,minor=True).major)

    def test_all_minor_breakouts_bounces_and_persisted_volume_setups_are_ignored(self):
        # Even model_copy or an old pending volume setup cannot re-enable minors.
        cfg=Settings(timeframe='1').model_copy(update={'minor':True})
        start=360000
        for term in ('long','short'):
            for kind,side,value,price in [('resistance','Long',101,102),('support','Short',99,98)]:
                for family in ('minor','major'):
                    key=f'{term}_{kind}_{family}'
                    levels={key:dict(value=value,pivot_time=0)}
                    rows=[dict(time=start-(19-i)*60,open=100,high=103,low=97,close=100,volume=10) for i in range(20)]
                    tracker={}
                    observe_breakout(tracker,rows,levels,cfg,start+1)
                    rows[-1]['close']=price
                    result=observe_breakout(tracker,rows,levels,cfg,start+2)
                    self.assertEqual(result['signal'],side if family=='major' else None)
                    bounced=bounce(dict(close=100,high=102,low=98),99 if kind=='support' else 101,levels,cfg)
                    self.assertEqual(bounced['signal'],('Long' if kind=='support' else 'Short') if family=='major' else None)
                    tracker=dict(bar=start,observed_at=start+1,price=100,levels=levels,volume_setups=levels)
                    rows[-1]['volume']=100
                    result=volume_observe(tracker,rows,levels,cfg,start+2,30)
                    self.assertEqual(result['signal'],side if family=='major' else None)

    def test_standalone_closed_hour_minor_does_not_open_or_conflict_with_order_block(self):
        from unittest.mock import patch
        from thoth.closed_order_block_sr import evaluate
        from test_closed_inverted_ob_sr import history,NOW
        cfg=Settings(strategy='order_block_sr_1h_close',minor=True)
        start=int(NOW)//3600*3600
        rows=[dict(time=start,close=100)]
        levels={'short_support_minor':dict(value=99,pivot_time=0)}
        tracker={}
        observe_breakout(tracker,rows,levels,cfg,NOW)
        rows[-1]['close']=98
        sr=observe_breakout(tracker,rows,levels,cfg,NOW+1)
        self.assertIsNone(sr['signal'])
        for signal,sources in [(None,[]),('Long',['ob:bullish_formed'])]:
            with patch('thoth.order_block.reference',return_value=[dict(ready=True,signal=signal,entry_sources=sources,conflict=False)]):
                result=evaluate(history(),cfg,NOW,sr)
                self.assertEqual(result['signal'],signal)
                self.assertEqual(result['entry_sources'],sources)
