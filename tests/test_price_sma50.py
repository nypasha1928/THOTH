import math,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from thoth.price_sma50 import PriceSMACross,STRATEGY
from thoth.models import Settings,TestnetSettings
from thoth.indicator import evaluate
from thoth.indicator_stream import IndicatorState
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.signal_alerts import read_alerts
from test_testnet import Exchange

D=1800;BASE=1790000000//D*D

def candles(values):return [dict(time=BASE+i*D,open=c,close=c,high=c+1,low=max(.00000001,c-1),volume=100) for i,c in enumerate(values)]

class PriceCrossTests(unittest.TestCase):
    def test_first_true_cross_once_no_trend_entries(self):
        state=PriceSMACross();rows=candles([100]*49+[99,98,101,102,103,97,96])
        results=[state.step(row,D) for row in rows]
        self.assertEqual([(i,r['signal']) for i,r in enumerate(results) if r['signal']],[(51,'Long'),(54,'Short')])
        self.assertAlmostEqual(results[51]['sma'],sum(r['close'] for r in rows[2:52])/50)
        self.assertEqual(results[51]['entry_sources'],['price_sma:50'])
    def test_first_departure_from_ready_equality_counts_once(self):
        state=PriceSMACross();rows=candles([100]*50+[101,102])
        result=[state.step(row,D) for row in rows]
        self.assertEqual([x['signal'] for x in result[-2:]],['Long',None])

    def test_touch_equality_does_not_rearm_same_direction(self):
        state=PriceSMACross(dict(closes=[100]*48+[99,101],side='Long',last_time=BASE,previous={'close':101,'sma50':100}))
        eq=state.step(dict(time=BASE+D,close=100),D);self.assertEqual(eq['sma'],100);self.assertIsNone(eq['signal'])
        self.assertIsNone(state.step(dict(time=BASE+2*D,close=101),D)['signal'])
        self.assertEqual(state.step(dict(time=BASE+3*D,close=99),D)['signal'],'Short')
    def test_warmup_gap_and_small_price_equality(self):
        state=PriceSMACross()
        for i in range(80):self.assertIsNone(state.step(dict(time=BASE+i*D,close=.1),D)['signal'])
        self.assertIsNone(state.side)
        state.step(dict(time=BASE+82*D,close=.2),D);self.assertIsNone(state.side);self.assertEqual(len(state.closes),1)
    def test_closed_only_batch_checkpoint_resume_and_bounded_state(self):
        cfg=Settings(strategy=STRATEGY);rows=candles([100+8*math.sin(i/5) for i in range(500)])
        expected=evaluate(rows,cfg,now=rows[-1]['time']+D)
        actual=[];state=IndicatorState(cfg)
        for i,row in enumerate(rows):
            if i%73==0:state=IndicatorState(cfg,state.checkpoint())
            actual.append(state.step(row))
        self.assertEqual(actual,expected);self.assertEqual(len(state.cross.closes),50)
        self.assertEqual(len(evaluate(rows,cfg,now=rows[-1]['time']+D-1)),len(rows)-1)
        self.assertFalse(any(k.startswith(('rsi:','sr:','ob:')) for r in actual for k in r['entry_sources']))

class ExecutionTests(unittest.TestCase):
    def test_closed_cross_enters_once_and_opposite_respects_selected_action(self):
        for action in ('reverse','close_only'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as tmp:
                rows=candles([100]*50+[99,101,102,103,98,97,96]);now=[rows[51]['time']+D-5]
                exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
                service.configure(TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT'],dedicated_account=True,opposite_action=action,daily_loss_pct=90,stop_dollars=1));service.activate(service.confirmation)
                feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
                def fetch(symbol,interval,limit=1000,end=None):
                    self.assertEqual(interval,'30');return [r for r in rows if r['time']<=int(now[0])//D*D][-limit:]
                def fill():
                    order=list(exchange.orders.values())[-1];exchange.fill(order['orderLinkId']);now[0]+=.1;service.monitor()
                with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
                    runner.cycle(paced=False);self.assertFalse(exchange.orders) # intrabar cross cannot enter
                    now[0]=rows[52]['time']+5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1,runner.health)
                    self.assertEqual(next(iter(exchange.orders.values()))['side'],'Buy');fill()
                    owner=service.state['owned']['BTCUSDT'];self.assertEqual(owner['price_sma_cross']['side'],'Long')
                    self.assertEqual(owner['entry_sources'],['price_sma:50'])
                    now[0]+=10;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1,service.state['events'][-8:])
                    now[0]=rows[54]['time']+5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1) # same-side closes
                    now[0]=rows[55]['time']+5;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),2)
                    self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly']);fill()
                    trade=service.history()['items'][0];self.assertEqual(trade['reason'],'Opposite CLOSED 30m price / SMA50 cross')
                    self.assertEqual(trade['price_sma_cross']['signal_bar'],rows[51]['time']);self.assertIn('SMA50',trade['entry_source_label'])
                    if action=='reverse':
                        self.assertEqual(len(exchange.orders),3);self.assertEqual(list(exchange.orders.values())[-1]['side'],'Sell');fill()
                    else:self.assertEqual(len(exchange.orders),2)
                    with patch.object(exchange,'candles',side_effect=AssertionError('No legacy SMA7/50 exit')):service.monitor()
                    self.assertTrue(all(r['entry_sources']==['price_sma:50'] for r in read_alerts(service.path)['items'] if r.get('signal') in ('Long','Short')))
