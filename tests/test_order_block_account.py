import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from thoth.models import Settings,TestnetSettings
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from thoth.verification import strategy_fingerprint
from test_testnet import Exchange
from test_order_block import formation_rows

STRATEGY='sr_order_block_gate'

class OrderBlockAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=[1792152020.];self.exchange=Exchange(lambda:self.now[0])
        self.path=Path(self.tmp.name)/'account.sqlite'
        self.service=TestnetSupervisor(self.path,enabled=True,password_configured=True,client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture','fixture'),clock=lambda:self.now[0])
        self.cfg=TestnetSettings(strategy=STRATEGY,symbols=['BTCUSDT','ETHUSDT'],dedicated_account=True,daily_loss_pct=90)
        self.service.configure(self.cfg);self.service.activate(self.service.confirmation)
        self.feed=FeedAdapter(self.service);self.feed_cfg=Settings(**{k:v for k,v in self.cfg.model_dump().items() if k in Settings.model_fields}).model_dump()

    def tearDown(self):self.tmp.cleanup()

    def observation(self,side=None,primary=None,symbol='BTCUSDT'):
        self.now[0]+=5
        self.service.apply_signals(self.feed_cfg,{symbol:dict(time=self.now[0],signal=None,mark=100,
            volume_signal=side,volume_sources=['ob:bullish_formed'] if side else [],primary_events=[primary] if primary else [])})

    def arm(self,symbol='BTCUSDT'):
        self.observation(primary=dict(id='fresh-'+symbol,time=self.now[0]+5,signal='Short',timeframe='240',sources=['htf:240:sr:short_support_major'],window_start=self.now[0]),symbol=symbol)

    def rows(self,signal):
        rows=formation_rows();start=int(self.now[0])//300*300;offset=start-rows[-1]['time']
        rows=[{**row,'time':row['time']+offset} for row in rows]
        if not signal:rows[-1].update(close=99,high=100)
        return rows

    def test_5m_intrabar_formation_after_sr_permission_places_protected_long(self):
        self.arm();self.assertFalse(self.exchange.orders)
        worker=Runner(self.feed,confirmation_only=True,interval=0);calls=[];trigger=[False]
        def fetch(symbol,interval,**kw):calls.append((interval,kw['limit']));return self.rows(trigger[0])
        with patch.object(self.exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:self.now[0]),patch('thoth.testnet.observe_exit',side_effect=AssertionError('Order Block must not use SMA exit')):
            worker.cycle();self.assertFalse(self.exchange.orders)
            self.now[0]+=5;trigger[0]=True;worker.cycle()
            order=next(iter(self.exchange.orders.values()));self.assertEqual(order['side'],'Buy')
            self.assertEqual(calls,[('5',1000),('5',60)])
            self.exchange.fill(order['orderLinkId']);self.service.monitor()
            owner=self.service.state['owned']['BTCUSDT'];self.assertTrue(owner['protection_confirmed'])
            self.assertEqual(owner['exit_timeframe'],'none')
            self.assertIn('ob:bullish_formed',owner['entry_sources'])
            self.assertEqual(self.service.state['entry_gates']['BTCUSDT']['direction'],'Any')
            self.now[0]+=5;worker.cycle();self.assertEqual(len(self.exchange.orders),1)
        self.assertEqual(worker.health['timeframe'],'5')
        gate_events=[e['message'] for e in self.service.state['events'] if 'armed by' in e['message']]
        self.assertIn('5m Order Block',gate_events[0]);self.assertNotIn('RSI',gate_events[0])
        self.service._close('BTCUSDT',self.exchange.active['BTCUSDT'],'Fixture close')
        for o in list(self.exchange.orders.values()):
            if o['reduceOnly'] and o['orderStatus']=='New':self.exchange.fill(o['orderLinkId'])
        self.service.monitor();trade=self.service.history()['items'][0]
        self.assertEqual(trade['strategy'],STRATEGY);self.assertIn('Bullish Order Block formed',trade['entry_source_label'])
        self.assertIn('4-hour S/R',trade['entry_source_label'])

    def test_sr_only_permission_never_calls_rsi_and_detects_both_higher_frames(self):
        runner=Runner(self.feed);price=[100]
        def history(symbol,tf,rows):return rows
        def analysis(symbol,cfg,rows,now,**kw):
            from thoth.timeframes import interval_seconds
            duration=interval_seconds(cfg.timeframe);start=int(now)//duration*duration
            return [dict(time=start-duration,levels={'long_resistance_major':dict(value=101,pivot_time=0)})],300
        def packet():
            from thoth.timeframes import interval_seconds
            return dict(primary={tf:[dict(time=int(self.now[0])//interval_seconds(tf)*interval_seconds(tf),close=price[0])] for tf in ('240','D')},minute_not_needed=True)
        with patch.object(runner,'history',side_effect=history),patch.object(runner,'analysis',side_effect=analysis),patch.object(runner,'rsi_preview',side_effect=AssertionError('RSI must not arm Order Block strategy')):
            first=runner.staged_observation('BTCUSDT',self.cfg,packet(),self.now[0]);self.assertFalse(first['primary_events'])
            runner.scan_cycle+=1;self.now[0]+=5;price[0]=102
            second=runner.staged_observation('BTCUSDT',self.cfg,packet(),self.now[0])
        self.assertEqual({event['timeframe'] for event in second['primary_events']},{'240','D'})
        self.assertTrue(all(':sr:' in source for event in second['primary_events'] for source in event['sources']))
        self.assertEqual(second['source_errors'],{})

    def test_observation_rollback_dedupe_restart_and_conflict(self):
        runner=Runner(self.feed);runner.scan_cycle=1
        first=runner.order_block_observation('BTCUSDT',self.cfg,self.rows(False),self.now[0]);self.assertIsNone(first['signal'])
        self.now[0]+=5;runner.scan_cycle+=1
        second=runner.order_block_observation('BTCUSDT',self.cfg,self.rows(True),self.now[0]);self.assertEqual(second['signal'],'Long')
        with sqlite3.connect(runner.history_path) as db:
            saved=json.loads(db.execute('SELECT payload FROM order_block_checkpoints').fetchone()[0])
        self.assertFalse(saved['state']['buy'])  # Forming block was never committed.
        self.now[0]+=5;runner.scan_cycle+=1
        self.assertIsNone(runner.order_block_observation('BTCUSDT',self.cfg,self.rows(True),self.now[0])['signal'])
        restarted=Runner(self.feed);self.now[0]+=5
        self.assertIsNone(restarted.order_block_observation('BTCUSDT',self.cfg,self.rows(True),self.now[0])['signal'])
        self.now[0]+=5;restarted.scan_cycle+=1
        self.assertIsNone(restarted.order_block_observation('BTCUSDT',self.cfg,self.rows(True),self.now[0])['signal'])

    def test_three_losses_lock_only_coin_then_require_fresh_sr_after_cooldown(self):
        self.arm()
        for _ in range(3):
            self.observation(side='Long');order=next(o for o in reversed(list(self.exchange.orders.values())) if not o['reduceOnly'])
            self.exchange.fill(order['orderLinkId']);self.service.monitor();self.exchange.prices['BTCUSDT']-=.1
            self.service._close('BTCUSDT',self.exchange.active['BTCUSDT'],'Fixture stop')
            for o in list(self.exchange.orders.values()):
                if o['reduceOnly'] and o['orderStatus']=='New':self.exchange.fill(o['orderLinkId'])
            self.service.monitor()
        gate=self.service.state['entry_gates']['BTCUSDT'];self.assertEqual(gate['phase'],'cooldown');self.assertTrue(self.service.state['entries'])
        self.arm('ETHUSDT');self.assertEqual(self.service.state['entry_gates']['ETHUSDT']['phase'],'armed')
        count=len(self.exchange.orders);self.now[0]=gate['cooldown_until']+1
        self.observation(side='Long');self.assertEqual(len(self.exchange.orders),count)
        self.assertEqual(gate['phase'],'wait_fresh')
        self.assertNotIn('BTCUSDT',self.feed.staged_armed_symbols())
