import math,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from thoth.crypto_ob_strength import StrengthState,THRESHOLDS,STRATEGY,LIST,validate_entry,DISABLED_EXITS
from thoth.order_block import OrderBlockState
from thoth.models import Settings,TestnetSettings
from thoth.indicator import evaluate
from thoth.testnet import TestnetSupervisor
from thoth.testnet_feed import FeedAdapter
from thoth.runner import Runner
from test_testnet import Exchange

D=3600;BASE=1790000000//86400*86400-22*D

def candles(n=200):
 rows=[];previous=100
 for i in range(n):
  close=100+15*math.sin(i*.8)+4*math.sin(i*.31)
  rows.append(dict(time=BASE+i*D,open=previous,high=max(previous,close)+2,low=min(previous,close)-2,close=close,volume=100))
  previous=close
 return rows

class StrengthTests(unittest.TestCase):
 def test_policy_catalogue_and_other_profiles_unchanged(self):
  cfg=TestnetSettings(strategy=STRATEGY,symbols=list(THRESHOLDS),timeframe='1',stop_dollars=.15,opposite_action='close_only',**{f:True for f in DISABLED_EXITS})
  self.assertEqual(cfg.timeframe,'60');self.assertEqual(cfg.stop_dollars,.75);self.assertEqual(cfg.opposite_action,'reverse')
  self.assertTrue(all(not getattr(cfg,f) for f in DISABLED_EXITS))
  self.assertFalse(cfg.order_block.supply_breakout or cfg.order_block.demand_breakdown)
  self.assertEqual(cfg.max_positions,3) # no hardcoded maximum or forced leverage/sizing
  self.assertEqual(cfg.saved_symbol_lists[LIST],list(THRESHOLDS));self.assertEqual(len(THRESHOLDS),15)
  self.assertEqual({s:THRESHOLDS[s] for s in ['BNBUSDT','AVAXUSDT','OPUSDT','POLUSDT','ENAUSDT']},{'BNBUSDT':2.58,'AVAXUSDT':.07,'OPUSDT':.00235,'POLUSDT':.00087,'ENAUSDT':.004})
  with self.assertRaises(ValueError):Settings(strategy=STRATEGY,symbols=['BTCUSDT'])
  other=Settings(strategy='sma_7_50',symbols=['BTCUSDT'],stop_dollars=.4,coin_breakeven_enabled=True)
  self.assertEqual(other.stop_dollars,.4);self.assertTrue(other.coin_breakeven_enabled)
 def test_upgrade_preserves_saved_subset_and_account_risk_settings(self):
  legacy=list(THRESHOLDS)[:10]
  cfg=TestnetSettings(strategy=STRATEGY,symbols=legacy,active_symbol_list=LIST,
      saved_symbol_lists={LIST:legacy},stop_dollars=1,max_positions=7,leverage=18,
      portfolio_loss_enabled=True,portfolio_loss_pct=35,portfolio_loss_basis='all_margin')
  self.assertEqual(cfg.stop_dollars,.75)
  self.assertEqual(cfg.symbols,legacy);self.assertEqual(cfg.saved_symbol_lists[LIST],legacy)
  self.assertEqual(cfg.max_positions,7);self.assertEqual(cfg.leverage,18)
  self.assertEqual(cfg.portfolio_loss_pct,35);self.assertEqual(cfg.portfolio_loss_basis,'all_margin')
  self.assertTrue(cfg.portfolio_loss_enabled)
  for symbol in ['BNBUSDT','AVAXUSDT','OPUSDT','POLUSDT','ENAUSDT']:
   candidate=Settings(strategy=STRATEGY,symbols=[symbol])
   self.assertEqual(candidate.symbols,[symbol]);self.assertEqual(candidate.stop_dollars,.75)
 def test_closed_only_checkpoint_and_no_break_signals(self):
  rows=candles();state=StrengthState('ETHUSDT');actual=[]
  for i,row in enumerate(rows):
   if i%23==0:state=StrengthState('ETHUSDT',json.loads(json.dumps(state.checkpoint())))
   actual.append(state.step(row))
  cfg=Settings(strategy=STRATEGY,symbols=['ETHUSDT'])
  self.assertEqual(actual,evaluate(rows,cfg,rows[-1]['time']+D,symbol='ETHUSDT'))
  self.assertEqual(len(evaluate(rows,cfg,rows[-1]['time']+D-1,symbol='ETHUSDT')),len(rows)-1)
  signals=[r for r in actual if r['signal']];self.assertGreater(len(signals),4)
  for r in signals:
   self.assertGreaterEqual(r['crypto_ob_strength']['block_width'],7.45)
   self.assertTrue(set(r['entry_sources'])<={'ob:bullish_formed','ob:bearish_formed'})
  unsupported=StrengthState('NVDAUSDT')
  self.assertFalse(any(unsupported.step(r)['signal'] for r in rows))
 def test_per_coin_thresholds_used_instead_of_global_width(self):
  rows=candles();low=StrengthState('LDOUSDT');eth=StrengthState('ETHUSDT')
  lo=[low.step(r) for r in rows];hi=[eth.step(r) for r in rows]
  self.assertGreater(sum(bool(r['signal']) for r in lo),sum(bool(r['signal']) for r in hi))
  native=OrderBlockState(interval='60',formation_filter=False,minimum_strength=7.45)
  for row,result in zip(rows,hi):
   n=native.step(row);events=[e for e in n['events'] if e in ('bullish_formed','bearish_formed')]
   self.assertEqual(result['entry_sources'],['ob:'+e for e in events] if n['ready'] else [])
 def test_entry_rechecks_symbol_side_boundaries_and_close(self):
  state=StrengthState('ETHUSDT');r=next(x for x in [state.step(row) for row in candles()] if x['signal'])
  now=r['time']+D+5;ctx={'crypto_ob_strength':r['crypto_ob_strength']}
  validate_entry('ETHUSDT',r['signal'],ctx,now)
  for symbol,side,context,stamp in [('BTCUSDT',r['signal'],ctx,now),('ETHUSDT','wrong',ctx,now),('ETHUSDT',r['signal'],None,now),('ETHUSDT',r['signal'],ctx,now+D)]:
   with self.assertRaises(ValueError):validate_entry(symbol,side,context,stamp)

class ExecutionTests(unittest.TestCase):
 def test_runner_baseline_entry_reversal_stop_and_history(self):
  rows=candles();machine=StrengthState('ETHUSDT');outputs=[machine.step(r) for r in rows]
  indices=[i for i,r in enumerate(outputs) if r['signal']]
  first=indices[0];opposite=next(i for i in indices if i>first and outputs[i]['signal']!=outputs[first]['signal'])
  now=[rows[first]['time']+D-5]
  with tempfile.TemporaryDirectory() as tmp:
   exchange=Exchange(lambda:now[0]);service=TestnetSupervisor(Path(tmp)/'live.sqlite',enabled=True,password_configured=True,client_factory=lambda *a,**k:exchange,credentials=lambda:('fixture','fixture'),clock=lambda:now[0])
   service.configure(TestnetSettings(strategy=STRATEGY,symbols=['ETHUSDT'],dedicated_account=True,daily_loss_pct=90,consecutive_losses=99));service.activate(service.confirmation)
   feed=FeedAdapter(service);runner=Runner(feed,fetch=feed.fetch)
   def fetch(symbol,interval,limit=1000,end=None):
    self.assertEqual(interval,'60');return [r for r in rows if r['time']<=int(now[0])//D*D][-limit:]
   def fill():
    order=list(exchange.orders.values())[-1];exchange.fill(order['orderLinkId']);now[0]+=.1;service.monitor()
   with patch.object(exchange,'candles',side_effect=fetch),patch('thoth.runner.time.time',side_effect=lambda:now[0]),patch('thoth.runner.time.monotonic',side_effect=lambda:now[0]):
    runner.cycle(paced=False);self.assertFalse(exchange.orders) # baseline, no forming entry
    now[0]=rows[first]['time']+D+5;runner.cycle(paced=False)
    self.assertEqual(len(exchange.orders),1,(runner.health,service.state['events'][-5:]))
    record=service.journal.records()[0];self.assertEqual(float(record['stop_dollars']),.75)
    self.assertEqual(record['instrument']['thoth_crypto_ob_strength']['minimum_strength'],7.45)
    self.assertIn('stopLoss',record['payload']);fill()
    owner=service.state['owned']['ETHUSDT'];self.assertEqual(owner['crypto_ob_strength']['signal_bar'],rows[first]['time'])
    now[0]+=10;runner.cycle(paced=False);self.assertEqual(len(exchange.orders),1)
    now[0]=rows[opposite]['time']+D+5;runner.cycle(paced=False)
    self.assertEqual(len(exchange.orders),2,service.state['events'][-8:]);self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly'])
    fill();self.assertEqual(len(exchange.orders),3,service.state['events'][-8:])
    trade=service.history()['items'][0];self.assertIn('strength-qualified',trade['reason']);self.assertIn('crypto_ob_strength',trade)
    self.assertEqual(list(exchange.orders.values())[-1]['side'],'Sell' if outputs[opposite]['signal']=='Short' else 'Buy')
    fill()
    with patch.object(exchange,'candles',side_effect=AssertionError('No legacy SMA exit')):service.monitor()
    position=exchange.active['ETHUSDT'];qty=float(position['size'])
    exchange.prices['ETHUSDT']+=(-1 if position['side']=='Buy' else 1)*1.1/qty
    now[0]+=2;service.monitor()
    self.assertEqual(len(exchange.orders),4);self.assertTrue(list(exchange.orders.values())[-1]['reduceOnly'])
    fill();self.assertEqual(len(exchange.orders),4) # stop does not auto-reenter or reverse
    self.assertEqual(service.history()['items'][0]['reason'],'Individual dollar loss limit')
