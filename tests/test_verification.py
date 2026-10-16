import json
import tempfile
import unittest
from pathlib import Path
from thoth.models import Settings
from thoth.verification import source_fingerprint,strategy_fingerprint,reference_status


class ReferenceReceiptTests(unittest.TestCase):
    def test_receipt_requires_current_source_settings_and_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=Settings()
            report=dict(format=1,source_fingerprint=source_fingerprint(),strategy_fingerprint=strategy_fingerprint(cfg),mismatch_count=0,bars=600,
                coverage={'long_signals':2,'short_signals':2,'level_types':[family+'_'+direction+'_'+kind for family in ('long','short') for direction in ('support','resistance') for kind in ('major','minor')]})
            path=Path(d)/'reference.json';path.write_text(json.dumps(report))
            self.assertTrue(reference_status(cfg,d)['verified'])
            self.assertFalse(reference_status(Settings(timeframe='D'),d)['verified'])
            report['coverage']['short_signals']=0;path.write_text(json.dumps(report))
            self.assertFalse(reference_status(cfg,d)['verified'])
            report['coverage']['short_signals']=2;report['source_fingerprint']='changed';path.write_text(json.dumps(report))
            self.assertFalse(reference_status(cfg,d)['verified'])
