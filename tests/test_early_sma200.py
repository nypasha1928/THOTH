"""Strict Early entry direction; synthetic data/exchange, no real orders."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from thoth.early_hour import sma200_baseline,direction_check,context,STRATEGY
from thoth.late_hour_comparison import SourceState,decision,LateHourComparison
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from test_early_hour import history
from test_late_hour_comparison import BAR,raw
from test_testnet import Exchange

class SMA200Tests(unittest.TestCase):
    def test_strict_direction_equality_and_decimal_precision(self):
        baseline=sma200_baseline(history(),BAR+3301)
        self.assertEqual(baseline['value'],'100');self.assertEqual(baseline['count'],200)
        for price,side,allowed in [('100.0000000001','Long',True),('99.9999999999','Short',True),('100.1','Short',False),('99.9','Long',False),('100','Long',False),('100.0','Short',False),('NaN','Long',False),(None,'Long',False)]:
            with self.subTest(price=price,side=side):self.assertEqual(direction_check(price,side,baseline,BAR+3301)['passed'],allowed)

    def test_uses_exactly_200_completed_hours_and_blocks_missing_gapped_duplicate_invalid_stale(self):
        rows=history();rows[-1].update(close=99999,high=100000)
        baseline=sma200_baseline(rows,BAR+3301);self.assertEqual(baseline['value'],'100')
        older={**rows[0],'time':rows[0]['time']-3600,'close':9999}
        self.assertEqual(sma200_baseline([older]+rows,BAR+3301)['value'],'100')
        gap=copy.deepcopy(rows);gap.pop(30)
        duplicate=copy.deepcopy(rows);duplicate[30]['time']=duplicate[29]['time']
        bad=copy.deepcopy(rows);bad[30]['close']=float('nan')
        for data in (rows[1:],gap,duplicate,bad):
            check=direction_check(101,'Long',sma200_baseline(data,BAR+3301),BAR+3301)
            self.assertFalse(check['passed'])
        self.assertFalse(direction_check(101,'Long',baseline,BAR+6901)['passed'])
        self.assertFalse(direction_check(101,'Long',None,BAR+3301)['passed'])

    def test_comparison_blocks_against_direction_and_does_not_filter_normal_bar_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=lambda:BAR+3301
            for strategy,expected in [(STRATEGY,None),('order_block_sr_1h_close','Long')]:
                cfg=Settings(strategy=strategy,symbols=['BTCUSDT'],pivot_long=1,pivot_short=1)
                rows=history();rows[:-1]=[{**r,'close':101,'high':102} for r in rows[:-1]]
                observer=LateHourComparison(Path(tmp)/(strategy+'.sqlite'),Path(tmp)/'live.sqlite',clock=clock)
                with patch('thoth.late_hour_comparison.select',return_value=raw()):observer.process(('BTCUSDT',cfg,rows,clock()))
                sample=observer.report()['items'][0]['latest']
                self.assertEqual(sample['signal'],expected)
                if strategy==STRATEGY:self.assertIn('Short entries only',sample['reason'])

    def setup_trade(self,tmp,now):
        e=Exchange(lambda:now[0]);e.prices['BTCUSDT']=100.5
        s=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:e,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
        cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],pivot_long=1,pivot_short=1,dedicated_account=True,daily_loss_pct=90)
        s.configure(cfg);s.activate(s.confirmation)
        self.assertTrue(s.snapshot()['entry_filters']['sma200']['enabled'])
        rows=history();state=SourceState(cfg);state.advance(rows,now[0])
        return s,e,cfg,rows,context(state,rows,cfg,now[0])

    def test_submission_reconciliation_crosses_sma_or_missing_quote_blocks_post_and_releases_slot(self):
        for price in (100,99.5,float('nan')):
            with self.subTest(price=price),tempfile.TemporaryDirectory() as tmp:
                now=[BAR+3301];s,e,cfg,rows,ctx=self.setup_trade(tmp,now)
                original=e.reconcile
                def reconcile(symbol,link):
                    result=original(symbol,link);e.prices['BTCUSDT']=price;return result
                with patch.object(e,'candles',return_value=rows[-2:]),patch.object(e,'reconcile',side_effect=reconcile),patch('thoth.late_hour_comparison.select',return_value=raw()):
                    s._entry('BTCUSDT','Long',now[0],'fixture',cfg,ctx)
                self.assertFalse(e.orders)
                record=s.journal.records()[0];self.assertEqual(record['phase'],'cancelled');self.assertTrue(record['result']['not_submitted'])
                self.assertFalse(s._early_hour_used('BTCUSDT',cfg))
                self.assertFalse(any(path=='/v5/order/create' for _,path,_ in e.calls))

    def test_missing_history_blocks_without_any_order_or_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+3301];s,e,cfg,rows,ctx=self.setup_trade(tmp,now)
            ctx['sma200_baseline']=sma200_baseline(rows[1:],now[0])
            s._entry('BTCUSDT','Long',now[0],'fixture',cfg,ctx)
            self.assertFalse(e.orders);self.assertFalse(s.journal.records())
            self.assertIn('200 completed hourly candles',s.state['events'][-1]['message'])

    def test_opposite_signal_closes_but_disallowed_replacement_never_opens(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[BAR+3301];s,e,cfg,rows,ctx=self.setup_trade(tmp,now)
            s.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=now[0]-1,signal=None,mark=100.5)})
            with patch.object(e,'candles',return_value=rows[-2:]),patch('thoth.late_hour_comparison.select',return_value=raw()):s._entry('BTCUSDT','Long',now[0],'fixture',cfg,ctx)
            first=next(iter(e.orders));e.fill(first);s.monitor()
            now[0]+=3600;rows=[{**r,'time':r['time']+3600} for r in rows]
            state=SourceState(cfg);state.advance(rows,now[0]);ctx=context(state,rows,cfg,now[0])
            # Short signal above the SMA still exits the held Long, but cannot reverse.
            s.apply_signals(Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump(),{'BTCUSDT':dict(time=now[0],signal='Short',mark=100.5,entry_context=ctx)})
            closes=[o for o in e.orders.values() if o['reduceOnly']];self.assertEqual(len(closes),1)
            e.fill(closes[0]['orderLinkId']);s.monitor()
            self.assertEqual(len(e.orders),2);self.assertFalse(s.state['owned'])
