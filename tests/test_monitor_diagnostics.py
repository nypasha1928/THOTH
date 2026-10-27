import json
from pathlib import Path
import unittest
from thoth.diagnostics import failure_details, failure_summary


class MonitorDiagnosticTests(unittest.TestCase):
    def test_code_location_survives_without_exception_text_or_locals(self):
        path=Path(__file__).resolve().parents[1]/'thoth'/'testnet.py'
        namespace={}
        exec(compile('def monitor():\n secret="PRIVATE_FIXTURE"\n raise KeyError(secret)\n',str(path),'exec'),namespace)
        try:namespace['monitor']()
        except KeyError as exc:details=failure_details(exc,'account_monitor')
        self.assertEqual(details['exception_class'],'KeyError')
        self.assertEqual(details['code_locations'],[dict(file='testnet.py',function='monitor',line=3)])
        self.assertIn('testnet.py:3 monitor',failure_summary(details))
        self.assertNotIn('PRIVATE_FIXTURE',json.dumps(details))
        self.assertNotIn(str(path.parent),json.dumps(details))

    def test_unknown_exception_names_and_external_frames_are_not_copied(self):
        custom=type('PRIVATE_FIXTURE',(Exception,),{})
        try:raise custom('SECRET_RESPONSE')
        except Exception as exc:details=failure_details(exc,'account_monitor')
        self.assertEqual(details,dict(stage='account_monitor',error_type='UnexpectedError'))

    def test_other_diagnostic_stages_are_unchanged(self):
        details=failure_details(KeyError('SECRET_RESPONSE'),'sma_exit')
        self.assertEqual(details,dict(stage='sma_exit',error_type='UnexpectedError'))
