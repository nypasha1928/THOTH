"""User-authorized one-time filter opt-out preserves later deliberate choices."""
import tempfile,unittest
from pathlib import Path
from thoth.models import TestnetSettings,RSISettings
from thoth.testnet import TestnetSupervisor

KEYS=('volume_filter','volume_for_static','vwap_filter','sma_filter','histogram_filter')

class RSIFiltersOffTests(unittest.TestCase):
    def test_new_defaults_off_without_changing_smoothing_or_entry_families(self):
        cfg=RSISettings()
        self.assertTrue(all(getattr(cfg,key) is False for key in KEYS))
        self.assertTrue(cfg.smooth and cfg.static_signals and cfg.ml_signals)
        self.assertEqual((cfg.rsi_length,cfg.smooth_period,cfg.signal_rules),(14,4,'original_six'))

    def test_existing_saved_filters_disabled_once_and_later_opt_in_survives(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'filter.sqlite'
            create=lambda:TestnetSupervisor(path,clock=lambda:1792152000.)
            service=create()
            enabled={key:True for key in KEYS}
            cfg=TestnetSettings(sma_enabled=True,sma_7_50_filter_enabled=True,
                rsi={**enabled,'signal_rules':'eight_crossings','volume_period':40})
            service.configure(cfg)
            old=service.load();old.pop('rsi_filters_off_v1');service.save(old)
            service=create();state=service.load()
            self.assertTrue(state['rsi_filters_off_v1'])
            self.assertTrue(all(state['config']['rsi'][key] is False for key in KEYS))
            self.assertEqual(state['risk'],old['risk'])
            self.assertEqual(state['config']['rsi']['signal_rules'],'eight_crossings')
            self.assertEqual(state['config']['rsi']['volume_period'],40)
            self.assertTrue(state['config']['rsi']['smooth'])
            self.assertTrue(state['config']['sma_enabled'] and state['config']['sma_7_50_filter_enabled'])
            self.assertFalse(state['entries'])
            service.configure(cfg)
            restarted=create().load()
            self.assertTrue(all(restarted['config']['rsi'][key] for key in KEYS))
            self.assertEqual(len([e for e in restarted['events'] if e['message'].startswith('RSI filters switched off')]),1)
