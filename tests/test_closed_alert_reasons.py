import tempfile,unittest
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.signal_alerts import read_alerts,read_scan_checks
import test_delayed_30m as fixtures

class ClosedAlertReasons(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=[fixtures.BASE+5]
        self.s,self.exchange,_,_=fixtures.DelayedExecutionTests().fixture(self.tmp.name,self.now)
        self.s.pause();self.s.configure(TestnetSettings(**{**self.s.state['config'],'strategy':'sr_rsi_30m_delayed'}));self.s.activate(self.s.confirmation)
        self.cfg=Settings(**{k:v for k,v in self.s.state['config'].items() if k in Settings.model_fields}).model_dump()
    def tearDown(self):self.tmp.cleanup()
    def observation(self,signal='Long'):
        d=dict(reason='closed 30m source',signal=signal,candle={'time':fixtures.BASE-1800},rsi_events=['ml_long_up'] if signal else [],sr_sources=[],bounce_sources=[],conflict=False)
        raw=dict(signal=signal,closed_bar=fixtures.BASE-1800,entry_sources=['rsi:ml_long_up'],signal_diagnostic=d) if signal else None
        return dict(time=self.now[0],signal=None,mark=100,signal_diagnostic=d,observation_channel='delayed_capture',delayed_capture=raw)
    def test_saved_capture_is_not_overwritten_by_generic_skip(self):
        self.s.apply_signals(self.cfg,{'BTCUSDT':self.observation()})
        rows=read_alerts(self.s.path)['items'];self.assertEqual(len(rows),1)
        self.assertIn('saved; scheduled entry',rows[0]['reason']);self.assertIn('htf:30:rsi:ml_long_up',rows[0]['entry_sources'])
        self.assertFalse(self.exchange.orders)
    def test_blocked_capture_keeps_weekend_reason_and_diagnostics(self):
        with patch.object(self.s,'_reversal_block_reason',return_value='Scheduled entry pause (New York): weekend window'):
            self.s.apply_signals(self.cfg,{'BTCUSDT':self.observation()})
        rows=read_alerts(self.s.path)['items'];self.assertEqual(len(rows),1)
        self.assertIn('weekend window',rows[0]['reason']);self.assertEqual(rows[0]['diagnostic']['rsi_events'],['ml_long_up'])
        self.assertFalse(self.s.state['delayed_30m']);self.assertFalse(self.exchange.orders)
    def test_legacy_generic_skip_projection_preserves_specific_reason(self):
        obs={**self.observation(),'entry_sources':['htf:30:rsi:ml_long_up']}
        self.s._signal_decision('BTCUSDT','skipped: weekend window',obs)
        self.s._signal_decision('BTCUSDT','skipped: closed 30m source',obs)
        rows=read_alerts(self.s.path)['items'];self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['reason'],'skipped: weekend window')
        self.assertEqual(rows[0]['original_reason'],'skipped: weekend window')
    def test_legacy_no_signal_is_explicit_but_unknown_source_is_not_invented(self):
        self.s._signal_decision('BTCUSDT','skipped: closed 30m source',self.observation(None))
        self.now[0]+=1;self.s._signal_decision('ETHUSDT','skipped: old status',dict(time=self.now[0],signal=None))
        rows=read_alerts(self.s.path)['items'];self.assertEqual(len(rows),1);unknown=rows[0]
        empty=read_scan_checks(self.s.path)['items'][0]
        self.assertNotIn('source_status',unknown)
        self.assertEqual(empty['source_status'],'no_qualifying_signal');self.assertIn('no qualifying',empty['reason'])
    def test_future_expired_window_retains_the_captured_rsi_source(self):
        self.s.apply_signals(self.cfg,{'BTCUSDT':self.observation()})
        self.now[0]=fixtures.BASE+481;self.s.monitor()
        row=read_alerts(self.s.path)['items'][0]
        self.assertIn('window expired',row['reason']);self.assertEqual(row['entry_sources'],['htf:30:rsi:ml_long_up'])
        self.assertEqual(row['diagnostic']['rsi_events'],['ml_long_up']);self.assertFalse(self.exchange.orders)

    def test_no_signal_checks_do_not_create_events_or_reduce_alert_retention(self):
        before=len(self.s.state['events'])
        self.s._signal_decision('BTCUSDT','skipped: no qualifying CLOSED 30m S/R or RSI signal',self.observation(None))
        self.assertEqual(len(self.s.state['events']),before)
        self.assertEqual(read_alerts(self.s.path)['total'],0)
        self.assertEqual(read_scan_checks(self.s.path)['total'],1)
        with self.s.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM signal_decisions').fetchone()[0],0)

    def test_legacy_empty_rows_are_filtered_before_pagination_and_export(self):
        import json
        old={**self.observation(None),'alert_key':'legacy-empty','strategy':'sr_rsi_30m_delayed','symbol':'BTCUSDT','reason':'skipped: closed 30m source','diagnostic':self.observation(None)['signal_diagnostic']}
        with self.s.db() as db:db.execute('INSERT INTO signal_decisions(time,symbol,payload) VALUES(?,?,?)',(self.now[0],'BTCUSDT',json.dumps(old)))
        for coin in ('BTCUSDT','ETHUSDT'):
            self.now[0]+=1;self.s._signal_decision(coin,'skipped: maximum positions reached',dict(time=self.now[0],signal='Long',entry_sources=['htf:30:rsi:ml_long_up']))
        first=read_alerts(self.s.path,limit=1);second=read_alerts(self.s.path,limit=1,offset=1)
        self.assertEqual(first['total'],2);self.assertEqual(first['items'][0]['symbol'],'ETHUSDT');self.assertEqual(second['items'][0]['symbol'],'BTCUSDT')
        self.assertEqual(len(read_alerts(self.s.path,limit=None)['items']),2);self.assertEqual(read_scan_checks(self.s.path)['total'],1)
