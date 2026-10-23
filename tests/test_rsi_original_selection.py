"""Original-mode selection preserves the corrected alternative and user's inputs."""
import copy,hashlib,tempfile,unittest
from pathlib import Path
from thoth.models import RSISettings,TestnetSettings
from thoth.testnet import TestnetSupervisor

class OriginalSelectionTests(unittest.TestCase):
    def test_uploaded_default_and_source_are_preserved(self):
        self.assertEqual(RSISettings().calculation_mode,'fixed')
        self.assertEqual(hashlib.sha256(Path('reference/ml_rsi_900_uploaded.pine').read_bytes()).hexdigest(),
            'cf415bc7edbd732e04bf2c11148a2a04d06c4ff5dc418625134fd3a43e3078df')

    def test_once_only_switch_backs_up_mode_and_keeps_all_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'original.sqlite'
            create=lambda:TestnetSupervisor(path,clock=lambda:1792152000.)
            service=create()
            cfg=TestnetSettings(strategy='rsi_hourly_intrabar',timeframe='5',
                rsi={'calculation_mode':'corrected','signal_rules':'eight_crossings','smooth':True,'smooth_period':4})
            service.configure(cfg)
            old=service.load();old.pop('rsi_original_mode_v1');old['events']=[];service.save(old)
            before=copy.deepcopy(old)
            service=create();state=service.load()
            self.assertEqual(state['config']['rsi']['calculation_mode'],'uploaded_pine')
            for key,value in before['config'].items():
                if key!='rsi':self.assertEqual(state['config'][key],value)
            for key,value in before['config']['rsi'].items():
                if key!='calculation_mode':self.assertEqual(state['config']['rsi'][key],value)
            self.assertEqual(state['rsi_previous_calculation']['settings'],before['config']['rsi'])
            self.assertEqual(state['risk'],before['risk'])
            self.assertFalse(state['entries'])
            self.assertEqual(state['owned'],before['owned'])
            # Corrected remains selectable, and a later deliberate choice persists.
            service.configure(cfg)
            later=create().load()
            self.assertEqual(later['config']['rsi']['calculation_mode'],'corrected')
            self.assertEqual(later['rsi_previous_calculation'],state['rsi_previous_calculation'])
            self.assertEqual(len([e for e in later['events'] if e['message'].startswith('RSI switched to uploaded-original')]),1)
