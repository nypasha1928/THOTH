import unittest
from thoth.intrabar_exit import observe_exit
import test_testnet as fixtures


def rows(now,price=100,duration=60):
    start=int(now)//duration*duration
    return [dict(time=start-(50-i)*duration,close=100 if i<50 else price) for i in range(51)]


class IntrabarMath(unittest.TestCase):
    def test_both_directions_before_close(self):
        for side,price,word in [('Buy',99,'below'),('Sell',101,'above')]:
            tracker={};self.assertIn(word,observe_exit(tracker,rows(6010,price),'1',side,6010))
            self.assertIsNone(observe_exit(tracker,rows(6011,price),'1',side,6011))

    def test_second_cross_within_same_candle_and_daily(self):
        tracker={}
        self.assertIsNone(observe_exit(tracker,rows(6010,101),'1','Buy',6010))
        self.assertIn('below',observe_exit(tracker,rows(6015,99),'1','Buy',6015))
        self.assertIn('above',observe_exit({},rows(86410,101,86400),'D','Sell',86410))

    def test_stale_gap_and_invalid_price_rejected(self):
        for data in [rows(5950),rows(6010)[:-1],rows(6010,float('nan'))]:
            with self.assertRaises(ValueError):observe_exit({},data,'1','Buy',6011)


class IntrabarIntegration(unittest.TestCase):
    setUp=fixtures.TestnetIntegration.setUp
    tearDown=fixtures.TestnetIntegration.tearDown
    create=fixtures.TestnetIntegration.create
    activate=fixtures.TestnetIntegration.activate
    signal=fixtures.TestnetIntegration.signal
    enter=fixtures.TestnetIntegration.enter
    fill_closes=fixtures.TestnetIntegration.fill_closes

    def test_paused_exit_close_only_both_strategies(self):
        for strategy in ['sr_breakout','sma_cross']:
            self.cfg.strategy=strategy
            self.service.configure(self.cfg)
            self.enter();self.service.pause();self.now[0]+=6
            original=self.exchange.request
            def request(method,path,params,private=False):
                if path=='/v5/market/kline':
                    data=rows(self.now[0],99)
                    return {'list':[[str(r['time']*1000),'100','100','99',str(r['close']),'1'] for r in data]}
                return original(method,path,params,private)
            self.exchange.request=request
            self.service.monitor()
            owner=self.service.state['owned']['BTCUSDT']
            self.assertIn('intrabar exit',owner['reason'])
            self.assertFalse(self.service.state['reversals'])
            closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
            self.assertEqual(len(closes),1)
            self.service.monitor();self.assertEqual(len([o for o in self.exchange.orders.values() if o['reduceOnly']]),1)
            self.fill_closes();self.service.monitor()
            self.exchange.request=original
            self.exchange.orders.clear()

    def test_disabled_no_intrabar_exit(self):
        self.cfg.sma_exit_enabled=False;self.service.configure(self.cfg)
        self.enter();self.now[0]+=6;self.service.monitor()
        self.assertFalse(any(p=='/v5/market/kline' for _,p,_ in self.exchange.calls))

    def test_candle_failure_keeps_account_loss_exit_managed(self):
        self.enter();self.now[0]+=6
        original=self.exchange.request
        def request(method,path,params,private=False):
            if path=='/v5/market/kline':raise TimeoutError('fixture timeout')
            return original(method,path,params,private)
        self.exchange.request=request
        self.exchange.prices['BTCUSDT']=97
        self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.assertIn('BTCUSDT',self.service.state['sma_exit_blocks'])
        self.assertIsNotNone(self.service.state['last_monitor'])
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_short_exit_and_persistent_observation(self):
        self.enter(side='Short');self.service.pause();self.now[0]+=6
        original=self.exchange.request
        def request(method,path,params,private=False):
            if path=='/v5/market/kline':
                self.assertEqual(params['limit'],51)
                return {'list':[[str(r['time']*1000),'100','101','100',str(r['close']),'1'] for r in rows(self.now[0],101)]}
            return original(method,path,params,private)
        self.exchange.request=request
        self.service.monitor()
        owner=self.service.load()['owned']['BTCUSDT']
        self.assertIn('above',owner['reason'])
        self.assertEqual(owner['sma_exit']['timeframe'],'1')
        self.assertTrue(self.exchange.orders[owner['close_link']]['reduceOnly'])
        self.assertFalse(self.service.state['reversals'])

    def test_failed_reads_back_off_and_recovery_preserves_running_state(self):
        from thoth.intrabar_exit import ExitDataError
        self.enter();self.now[0]+=6
        original=self.exchange.candles;attempts=[]
        def unavailable(*args,**kwargs):
            attempts.append(self.now[0]);raise ExitDataError('candle_gap')
        self.exchange.candles=unavailable
        self.service.monitor()
        owner=self.service.state['owned']['BTCUSDT']
        self.assertEqual(owner['sma_exit_error']['data_reason'],'candle_gap')
        self.now[0]+=6;self.service.monitor()
        self.assertEqual(len(attempts),1)
        self.exchange.candles=original
        self.now[0]+=25;self.service.monitor()
        self.assertNotIn('sma_exit_error',owner)
        self.assertTrue(self.service.state['entries'])
        self.assertNotIn('BTCUSDT',self.service.state['sma_exit_blocks'])
        self.assertTrue(any('observations recovered' in e['message'] for e in self.service.state['events']))

    def test_overnight_open_loss_rolls_at_midnight_without_double_charge(self):
        from datetime import datetime,timezone
        self.now[0]=datetime(2026,10,19,23,58,tzinfo=timezone.utc).timestamp()
        self.cfg.daily_loss_pct=15;self.cfg.stop_dollars=2.4
        self.service.configure(self.cfg)
        self.enter()
        self.exchange.prices['BTCUSDT']=104;self.service.monitor()
        before=datetime(2026,10,19,23,59,59,tzinfo=timezone.utc).timestamp()
        self.now[0]=before;self.exchange.prices['BTCUSDT']=97;self.service.monitor()
        old=self.service.state['risk']['loss_used']
        self.assertGreater(old,0)
        self.now[0]=before+3;self.service.monitor()
        risk=self.service.state['risk']
        self.assertEqual(risk['day'],'2026-10-20')
        self.assertEqual(risk['baseline'],98.5)
        self.assertEqual(risk['peak'],98.5)
        self.assertEqual(risk['loss_used'],0)
        self.assertAlmostEqual(risk['remaining'],98.5*.15)
        self.assertEqual(sum('New UTC daily period 2026-10-20' in e['message'] for e in self.service.state['events']),1)
        self.service._close('BTCUSDT',self.service.positions()['BTCUSDT'],'fixture overnight close')
        self.fill_closes();self.service.monitor()
        self.assertEqual(risk['loss_used'],0)
        saved=self.service.snapshot()['risk']
        self.assertEqual(saved['baseline_ms'],int((before+3)*1000))
        self.service=self.create()
        self.assertEqual(self.service.state['risk']['peak_ms'],saved['peak_ms'])
        self.assertEqual(self.service.state['risk']['loss_used'],0)

    def test_publication_delay_does_not_pause_or_skip_a_boundary_cross(self):
        self.now[0]=int(self.now[0])//60*60+50
        self.enter();self.service.pause()
        # Model an already enabled, protected position observed just before a boundary.
        self.service.state.update(entries=True,status='running',reason='')
        owner=self.service.state['owned']['BTCUSDT']
        start=int(self.now[0])//60*60+60
        owner['sma_exit']=dict(time=start-60,timeframe='1',difference=.1,checked_at=start-1)
        self.service.save();original=self.exchange.request
        publishing=[True]
        def request(method,path,params,private=False):
            if path=='/v5/market/kline':
                data=rows(start-1 if publishing[0] else self.now[0],99)
                return {'list':[[str(r['time']*1000),'100','100','99',str(r['close']),'1'] for r in data]}
            return original(method,path,params,private)
        self.exchange.request=request
        self.now[0]=start+5;self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'running')
        self.assertEqual(owner['sma_exit']['waiting_for_candle'],start)
        self.assertNotIn('sma_exit_error',owner)
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        publishing[0]=False;self.now[0]=start+11;self.service.monitor()
        self.assertIn('intrabar exit',owner['reason'])
        self.assertNotIn('waiting_for_candle',owner['sma_exit'])
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.assertTrue(self.service.state['entries'])

    def test_prolonged_publication_failure_still_blocks_entries_and_manages_stop(self):
        self.enter();original=self.exchange.candles
        self.now[0]=int(self.now[0])//60*60+65
        stale=rows(self.now[0]-60)
        self.exchange.candles=lambda *args,**kwargs:stale
        self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.now[0]+=7;self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'running')
        self.assertIn('BTCUSDT',self.service.state['sma_exit_blocks'])
        self.assertEqual(self.service.state['owned']['BTCUSDT']['sma_exit_error']['data_reason'],'forming_candle_or_history_unavailable')
        self.exchange.candles=original

    def test_one_coin_data_failure_does_not_block_other_coin_entries_or_reversals(self):
        from thoth.intrabar_exit import ExitDataError
        self.enter();original=self.exchange.candles
        def candles(symbol,*args,**kwargs):
            if symbol=='BTCUSDT':raise ExitDataError('candle_gap')
            return original(symbol,*args,**kwargs)
        self.exchange.candles=candles;self.now[0]+=6;self.service.monitor()
        self.assertIn('BTCUSDT',self.service.snapshot()['sma_exit_blocks'])
        self.assertEqual(float(self.exchange.active['BTCUSDT']['stopLoss']),98)
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.signal(self.now[0]-60,None,'ETHUSDT')
        self.now[0]+=60;self.signal(self.now[0]-60,'Short','ETHUSDT')
        eth=next(link for link,o in self.exchange.orders.items() if o['symbol']=='ETHUSDT' and not o['reduceOnly'])
        self.exchange.fill(eth);self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.now[0]+=60;self.signal(self.now[0]-60,'Long','ETHUSDT')
        self.fill_closes();self.service.monitor()
        replacements=[o for o in self.exchange.orders.values() if o['symbol']=='ETHUSDT' and not o['reduceOnly'] and o['side']=='Buy']
        self.assertEqual(len(replacements),1)
        self.assertEqual(self.service.state['status'],'running')

    def test_data_recovery_never_resumes_a_manual_pause(self):
        from thoth.intrabar_exit import ExitDataError
        self.enter();self.service.pause();original=self.exchange.candles
        def fail(*args,**kwargs):raise ExitDataError('candle_gap')
        self.exchange.candles=fail;self.now[0]+=6;self.service.monitor()
        self.assertIn('BTCUSDT',self.service.state['sma_exit_blocks'])
        self.exchange.candles=original;self.now[0]+=31;self.service.monitor()
        self.assertNotIn('BTCUSDT',self.service.state['sma_exit_blocks'])
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(self.service.state['status'],'paused')

    def test_flat_affected_coin_requires_verified_data_before_reentry(self):
        from thoth.intrabar_exit import ExitDataError
        self.enter();original=self.exchange.candles
        def fail(*args,**kwargs):raise ExitDataError('candle_gap')
        self.exchange.candles=fail;self.now[0]+=6;self.service.monitor()
        self.service._close('BTCUSDT',self.service.positions()['BTCUSDT'],'fixture close')
        self.fill_closes();self.service.monitor()
        self.assertNotIn('BTCUSDT',self.service.state['owned'])
        self.now[0]+=31
        before=len(self.exchange.orders)
        self.signal(self.now[0]-60,'Short','BTCUSDT')
        self.assertEqual(len(self.exchange.orders),before)
        self.assertIn('BTCUSDT',self.service.state['sma_exit_blocks'])
        self.assertTrue(self.service.state['entries'])
        self.exchange.candles=original;self.now[0]+=60
        self.signal(self.now[0]-60,'Short','BTCUSDT')
        self.assertEqual(len(self.exchange.orders),before+1)
        self.assertNotIn('BTCUSDT',self.service.state['sma_exit_blocks'])
