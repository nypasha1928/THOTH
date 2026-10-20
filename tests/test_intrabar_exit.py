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
            with self.assertRaises(ValueError):observe_exit({},data,'1','Buy',6010)


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
        self.assertFalse(self.service.state['entries'])
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

    def test_failed_reads_back_off_and_recovery_stays_paused(self):
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
        self.assertFalse(self.service.state['entries'])
        self.assertTrue(any('observations recovered' in e['message'] for e in self.service.state['events']))
