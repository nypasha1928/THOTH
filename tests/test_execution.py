import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from thoth.bybit_testnet import AmbiguousOrder, TestnetClient, BybitError
from thoth.execution import ExecutionJournal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=Mock(BASE=TestnetClient.BASE,armed=True)
        self.journal=ExecutionJournal(Path(self.tmp.name)/'execution.sqlite',self.client)
        self.payload=dict(symbol='BTCUSDT',side='Buy',orderLinkId='fixture-link',category='linear',reduceOnly=False)
        self.instrument={'priceFilter':{'tickSize':'.1'}}

    def tearDown(self):self.tmp.cleanup()

    def submit(self):return self.journal.submit(self.payload,1,self.instrument)

    def test_timeout_restart_does_not_resubmit(self):
        self.client.submit.side_effect=AmbiguousOrder()
        self.assertEqual(self.submit()['phase'],'unknown')
        self.client.reconcile.return_value=None
        restored=ExecutionJournal(self.journal.path,self.client)
        self.assertEqual(restored.submit(self.payload,1,self.instrument)['phase'],'unknown')
        self.assertEqual(self.client.submit.call_count,1)
        with self.assertRaises(ValueError):
            self.journal.submit({**self.payload,'orderLinkId':'another'},1,self.instrument)

    def test_fill_and_protective_stop_are_verified_separately(self):
        self.client.submit.return_value={'state':'accepted_not_filled'}
        self.assertEqual(self.submit()['phase'],'submitted')
        self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
        position={'size':'.5','positionIdx':0,'side':'Buy','avgPrice':'100','stopLoss':'98'}
        self.client.request.return_value={'list':[position]}
        result=self.journal.poll('fixture-link')
        self.assertEqual(result['phase'],'protected')
        self.client.attach_stop.assert_not_called()

    def test_partial_fill_requests_cancel_before_protecting(self):
        self.client.submit.return_value={'state':'accepted_not_filled'};self.submit()
        self.client.reconcile.return_value={'orderStatus':'PartiallyFilled','cumExecQty':'.1'}
        self.assertEqual(self.journal.poll('fixture-link')['phase'],'partial')
        self.assertEqual(self.client.request.call_args.args[1],'/v5/order/cancel')
        self.client.attach_stop.assert_not_called()

    def test_stop_failure_blocks_new_submissions(self):
        self.client.submit.return_value={'state':'accepted_not_filled'};self.submit()
        self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
        self.client.request.return_value={'list':[{'size':'.5','positionIdx':0,'side':'Buy','avgPrice':'100','stopLoss':'0'}]}
        self.client.attach_stop.side_effect=RuntimeError('sensitive upstream body')
        result=self.journal.poll('fixture-link')
        self.assertEqual(result['phase'],'stop_error')
        self.assertNotIn('sensitive',str(result))
        with self.assertRaises(ValueError):self.journal.submit({**self.payload,'orderLinkId':'another'},1,self.instrument)

    def test_wrong_stop_readback_does_not_mark_protected(self):
        self.client.submit.return_value={'state':'accepted_not_filled'};self.submit()
        self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
        self.client.request.return_value={'list':[{'size':'.5','positionIdx':0,'side':'Buy','avgPrice':'100','stopLoss':'90'}]}
        self.assertEqual(self.journal.poll('fixture-link')['phase'],'stop_error')

    def test_changed_position_is_not_claimed_as_owned(self):
        self.client.submit.return_value={'state':'accepted_not_filled'};self.submit()
        self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
        self.client.request.return_value={'list':[{'size':'1','positionIdx':0,'side':'Buy','avgPrice':'100'}]}
        self.assertEqual(self.journal.poll('fixture-link')['phase'],'mismatch')
        self.client.attach_stop.assert_not_called()

    def test_not_modified_requires_correct_readback(self):
        for stop,phase in [('98','protected'),('90','stop_error')]:
            with self.subTest(stop=stop):
                self.client.submit.return_value={'state':'accepted_not_filled'}
                payload={**self.payload,'orderLinkId':'readback-'+stop}
                self.journal.submit(payload,1,self.instrument)
                self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
                position=dict(size='.5',positionIdx=0,side='Buy',avgPrice='100',stopLoss='0')
                self.client.request.side_effect=[{'list':[position]},{'list':[{**position,'stopLoss':stop}]}]
                self.client.attach_stop.side_effect=BybitError('private body',34040)
                self.assertEqual(self.journal.poll(payload['orderLinkId'])['phase'],phase)

    def test_protection_diagnostics_survive_settlement(self):
        self.client.submit.return_value={'state':'accepted_not_filled'};self.submit()
        self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
        self.client.request.return_value={'list':[dict(size='.5',positionIdx=0,side='Buy',avgPrice='100',stopLoss='0')]}
        self.client.attach_stop.side_effect=BybitError('secret response',10001)
        result=self.journal.poll('fixture-link')
        self.assertEqual(result['result']['protection_error']['exchange_code'],10001)
        result=self.journal.update('fixture-link','closed')
        self.assertEqual(result['result']['protection_error']['exchange_code'],10001)
        self.assertNotIn('secret response',str(result))

    def test_2_dollar_stop_can_be_before_or_beyond_liquidation_on_50_dollar_20x_position(self):
        from thoth.diagnostics import failure_summary
        for side,liquidation,stop,phase in [('Buy','96.5','96','stop_error'),('Buy','95','96','protected'),('Sell','103.5','104','stop_error'),('Sell','105','104','protected')]:
            with self.subTest(side=side,liquidation=liquidation):
                link='liquidation-'+side+'-'+liquidation
                self.client.submit.return_value={'state':'accepted_not_filled'}
                self.journal.submit({**self.payload,'side':side,'orderLinkId':link},2,self.instrument)
                self.client.reconcile.return_value={'orderStatus':'Filled','cumExecQty':'.5'}
                self.client.request.return_value={'list':[dict(size='.5',positionIdx=0,side=side,avgPrice='100',leverage='20',liqPrice=liquidation,stopLoss=stop)]}
                result=self.journal.poll(link);self.assertEqual(result['phase'],phase)
                if phase=='stop_error':
                    details=result['result']['protection_error']
                    self.assertEqual(details['protection_reason'],'stop_at_or_beyond_liquidation')
                    self.assertEqual(details['protection_values']['filled_margin'],'2.5')
                    self.assertEqual(details['protection_values']['expected_stop'],stop)
                    self.assertIn('at or beyond liquidation',failure_summary(details))
                    self.journal.update(link,'closed')
                    self.assertEqual(self.journal.get(link)['result']['protection_error'],details)
                else:self.journal.update(link,'closed')
        self.client.attach_stop.assert_not_called()

    def test_partial_margin_and_tick_validation_have_specific_safe_reasons(self):
        from thoth.diagnostics import protection_details
        for message,reason in [('Partial fill margin is too small for requested dollar stop','stop_exceeds_filled_margin'),('Dollar stop is smaller than one price tick','stop_smaller_than_tick')]:
            details=protection_details(ValueError(message),'validate_protection','stop_price',{'entry':'100','secret':'never show me'})
            self.assertEqual(details['protection_reason'],reason)
            self.assertEqual(details['protection_values'],{'entry':'100'})
        details=protection_details(ValueError('private response key=SECRET'),'validate_protection','private check SECRET',{'entry':'SECRET','filled_qty':'NaN','leverage':'Infinity','secret':'SECRET'})
        self.assertNotIn('SECRET',str(details))
        self.assertNotIn('protection_values',details)
        self.assertNotIn('protection_check',details)
