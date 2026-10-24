import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from thoth.bybit_testnet import TestnetClient,BybitError
from thoth.models import TestnetSettings,Settings
from thoth.testnet import TestnetSupervisor


class Exchange(TestnetClient):
    """Stateful exchange simulator; no network transport or real account."""
    def __init__(self,now):
        super().__init__('fixture','fixture',armed=True)
        self.now=now;self.balance=100.;self.active={};self.orders={};self.closed=[];self.logs=[]
        self.prices={'BTCUSDT':100.,'ETHUSDT':100.,'SOLUSDT':100.}
        self.fail_stop=False;self.uid=123;self.calls=[];self.margin='ISOLATED_MARGIN'

    def instrument(self,symbol):
        return dict(symbol=symbol,status='Trading',contractType='LinearPerpetual',settleCoin='USDT',
            lotSizeFilter=dict(qtyStep='.01',minOrderQty='.01',minNotionalValue='5',maxMktOrderQty='1000'),
            priceFilter=dict(tickSize='.1'),leverageFilter=dict(minLeverage='1',maxLeverage='100',leverageStep='1'))

    def request(self,method,path,params,private=False):
        self.calls.append((method,path,deepcopy(params)))
        if path=='/v5/user/query-api':return dict(userID=self.uid,readOnly=0,permissions={'Wallet':[],'ContractTrade':['Order','Position']})
        if path=='/v5/account/info':return dict(marginMode=self.margin,unifiedMarginStatus=5)
        if path=='/v5/account/wallet-balance':
            pnl=sum((self.prices[s]-float(p['avgPrice']))*float(p['size'])*(1 if p['side']=='Buy' else -1) for s,p in self.active.items())
            return {'list':[{'coin':[dict(coin='USDT',equity=str(self.balance+pnl),walletBalance=str(self.balance),spotBorrow='0')]}]}
        if path=='/v5/account/transaction-log':return {'list':deepcopy(self.logs)}
        if path=='/v5/position/list':
            rows=[]
            for s,p in self.active.items():
                if params.get('symbol',s)!=s:continue
                row=deepcopy(p);row.update(symbol=s,markPrice=str(self.prices[s]),unrealisedPnl=str((self.prices[s]-float(p['avgPrice']))*float(p['size'])*(1 if p['side']=='Buy' else -1)))
                rows.append(row)
            return {'list':rows}
        if path=='/v5/order/realtime' or path=='/v5/order/history':
            if params.get('orderLinkId'):rows=[self.orders[params['orderLinkId']]] if params['orderLinkId'] in self.orders else []
            else:rows=[o for o in self.orders.values() if o['orderStatus'] in ('New','PartiallyFilled')]
            return {'list':deepcopy(rows)}
        if path=='/v5/market/kline':
            from thoth.timeframes import interval_seconds
            duration=interval_seconds(params['interval'])
            start=int(self.now())//duration*duration
            return {'list':[[str((start-i*duration)*1000),'100','100','100','100','1'] for i in range(params['limit'])]}
        if path=='/v5/market/tickers':return {'list':[dict(lastPrice=str(self.prices[params['symbol']]))]}
        if path=='/v5/position/set-leverage':return {}
        if path=='/v5/order/create':
            link=params['orderLinkId']
            self.orders[link]=dict(params,orderId=link,orderStatus='New',cumExecQty='0',createdTime=str(int(self.now()*1000)))
            return {'orderId':link}
        if path=='/v5/order/cancel':
            if params['orderLinkId'] in self.orders:self.orders[params['orderLinkId']]['orderStatus']='Cancelled'
            return {}
        if path=='/v5/position/trading-stop':
            if self.fail_stop:raise BybitError('fixture stop failure')
            for key in ('stopLoss','takeProfit'):
                if key in params:self.active[params['symbol']][key]=params[key]
            return {}
        if path=='/v5/position/closed-pnl':
            return {'list':[r for r in self.closed if r['symbol']==params['symbol'] and params['startTime']<=int(r['createdTime'])<=params['endTime']]}
        raise AssertionError('Unexpected endpoint '+path)

    def fill(self,link):
        order=self.orders[link];symbol=order['symbol'];price=self.prices[symbol];qty=float(order['qty'])
        if order['reduceOnly']:
            p=self.active.pop(symbol);qty=min(qty,float(p['size']))
            pnl=(price-float(p['avgPrice']))*qty*(1 if p['side']=='Buy' else -1)
            self.balance+=pnl
            self.closed.append(dict(orderId=link,symbol=symbol,closedSize=str(qty),closedPnl=str(pnl),execType='Trade',createdTime=str(int(self.now()*1000))))
        else:
            self.active[symbol]=dict(size=str(qty),side=order['side'],positionIdx=0,avgPrice=str(price),stopLoss=order.get('stopLoss','0'),takeProfit=order.get('takeProfit','0'),liqPrice='0')
        order.update(orderStatus='Filled',cumExecQty=str(qty),avgPrice=str(price))


class TestnetIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1792152000.]
        self.exchange=Exchange(lambda:self.now[0])
        self.path=Path(self.temp.name)/'testnet.sqlite'
        self.service=self.create()
        self.cfg=TestnetSettings(dedicated_account=True,stop_dollars=1)
        self.service.configure(self.cfg)

    def create(self,enabled=True):
        return TestnetSupervisor(self.path,enabled=enabled,password_configured=True,
            client_factory=lambda *a,**k:self.exchange,credentials=lambda:('fixture-key','fixture-secret'),clock=lambda:self.now[0])

    def tearDown(self):self.temp.cleanup()

    def activate(self):return self.service.activate('ENABLE TESTNET ORDERS')

    def signal(self,stamp,side='Long',symbol='BTCUSDT'):
        cfg=Settings(**self.cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
        self.service.apply_signals(cfg,{symbol:dict(time=stamp,mark=100,signal=side)})

    def enter(self,symbol='BTCUSDT',side='Long'):
        self.activate();self.signal(self.now[0]-60,None,symbol)
        self.now[0]+=60;self.signal(self.now[0]-60,side,symbol)
        link=next(o['orderLinkId'] for o in self.exchange.orders.values() if not o['reduceOnly'] and o['symbol']==symbol)
        self.exchange.fill(link);self.service.monitor()
        return link

    def test_signal_skip_audit_paused_queue_expiry_and_recovery(self):
        import json
        self.activate()
        self.signal(self.now[0]-60,None)
        self.service.state.update(entries=False,status='paused',reason='Operator paused entries')
        self.service.save()
        self.now[0]+=60
        self.signal(self.now[0]-60,'Long')
        with self.service.db() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM signal_decisions')]
        self.assertIn('new entries disabled',rows[-1]['reason'])
        self.assertFalse(self.exchange.orders)
        self.assertFalse(self.service.state['entries'])
        count=len(rows)
        self.signal(self.now[0]-60,'Long')
        with self.service.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM signal_decisions').fetchone()[0],count)
        self.service.state['pending_signals']['ETHUSDT']=dict(side='Short',time=self.now[0]-1000,identity='fixture')
        self.service._drain(self.cfg)
        with self.service.db() as db:
            last=json.loads(db.execute('SELECT payload FROM signal_decisions ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('expired in entry queue',last['reason'])
        restarted=self.create()
        with restarted.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM signal_decisions').fetchone()[0],count+1)

    def test_definite_rejection_does_not_pause_other_symbols(self):
        from unittest.mock import patch
        for stage in ('instrument','leverage','order'):
            with self.subTest(stage=stage):
                self.activate()
                original_request=self.exchange.request
                original_instrument=self.exchange.instrument
                def request(method,path,params,private=False):
                    if params.get('symbol')=='BTCUSDT' and path==({'leverage':'/v5/position/set-leverage','order':'/v5/order/create'}.get(stage)):
                        raise BybitError('Rejected fixture',code=110003)
                    return original_request(method,path,params,private)
                def instrument(symbol):
                    if symbol=='BTCUSDT' and stage=='instrument':raise BybitError('Invalid symbol',code=10001)
                    return original_instrument(symbol)
                self.service.instrument_cache.clear()
                with patch.object(self.exchange,'request',side_effect=request), patch.object(self.exchange,'instrument',side_effect=instrument):
                    self.service._entry('BTCUSDT','Long',self.now[0],stage,self.cfg)
                    self.assertTrue(self.service.state['entries'])
                    self.assertEqual(self.service.state['status'],'running')
                    self.assertIn('other coins continue',self.service.state['events'][-1]['message'])
                    self.service._entry('ETHUSDT','Long',self.now[0],stage+'-other',self.cfg)
                    self.assertTrue(any(o['symbol']=='ETHUSDT' for o in self.exchange.orders.values()))
                # Remove this unfilled fixture order before the next scenario.
                self.exchange.orders.clear()
                with self.service.journal.db() as db:db.execute('DELETE FROM execution')

    def fill_closes(self):
        for link,o in list(self.exchange.orders.items()):
            if o['reduceOnly'] and o['orderStatus']=='New':self.exchange.fill(link)
        self.now[0]+=1;self.service.monitor()

    def test_explicit_activation_and_server_gate(self):
        with self.assertRaises(ValueError):self.service.activate('yes')
        disabled=self.create(enabled=False)
        with self.assertRaises(ValueError):disabled.activate('ENABLE TESTNET ORDERS')
        self.assertFalse(disabled.snapshot()['entries'])
        self.assertFalse(any(c[0]=='POST' for c in self.exchange.calls))

    def test_full_entry_fill_stop_and_restart_management(self):
        link=self.enter()
        self.assertEqual(self.service.journal.get(link)['phase'],'protected')
        self.assertIn('BTCUSDT',self.service.state['owned'])
        restarted=self.create()
        self.assertFalse(restarted.snapshot()['entries'])
        restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        self.assertEqual(len(self.exchange.active),1)
        self.assertFalse(restarted.snapshot()['entries'])

    def test_daily_loss_closes_winners_and_locks_restart(self):
        self.enter()
        self.signal(self.now[0]-60,None,'ETHUSDT');self.now[0]+=60;self.signal(self.now[0]-60,'Long','ETHUSDT')
        eth=next(k for k,o in self.exchange.orders.items() if o['symbol']=='ETHUSDT' and not o['reduceOnly'])
        self.exchange.fill(eth);self.service.monitor()
        self.exchange.prices.update(BTCUSDT=90,ETHUSDT=102)
        self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual({o['symbol'] for o in closes},{'BTCUSDT','ETHUSDT'})
        self.assertEqual(self.service.snapshot()['status'],'daily_stop')
        self.fill_closes()
        self.assertEqual(self.exchange.active,{})
        with self.assertRaises(ValueError):self.activate()
        with self.assertRaises(ValueError):self.service.establish_baseline()

    def test_failed_protection_requests_emergency_reduce_only_close(self):
        self.activate();self.signal(self.now[0]-60,None);self.now[0]+=60;self.signal(self.now[0]-60)
        link=next(iter(self.exchange.orders));self.exchange.fill(link)
        self.exchange.active['BTCUSDT']['stopLoss']='0'
        self.exchange.fail_stop=True
        self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual(len(closes),1)
        self.assertEqual(closes[0]['side'],'Sell')
        self.assertTrue(self.service.snapshot()['entries'])
        self.assertIn('BTCUSDT',self.service.state['blocked_symbols'])
        self.fill_closes()
        self.assertEqual(self.exchange.active,{})

    def test_reversal_waits_for_close_and_realized_pnl(self):
        self.enter();self.now[0]+=60;self.signal(self.now[0]-60,'Short')
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
        self.fill_closes()
        self.now[0]+=1;self.signal(self.now[0]-60,'Short')
        sells=[o for o in self.exchange.orders.values() if o['side']=='Sell' and not o['reduceOnly']]
        self.assertEqual(len(sells),1)
        self.assertEqual(len(self.service.snapshot()['closures']),1)

    def test_different_account_and_unowned_position_are_rejected(self):
        self.activate();self.exchange.uid=999
        with self.assertRaises(ValueError):self.activate()
        self.assertFalse(self.service.snapshot()['entries'])
        self.exchange.uid=123
        self.exchange.active['SOLUSDT']=dict(size='1',side='Buy',positionIdx=0,avgPrice='100',stopLoss='99')
        with self.assertRaises(ValueError):self.activate()

    def test_withdrawal_and_margin_modes_are_not_changed_automatically(self):
        self.exchange.margin='REGULAR_MARGIN'
        with self.assertRaises(ValueError):self.activate()
        self.assertFalse(any(c[0]=='POST' for c in self.exchange.calls))


    def test_opposite_exit_survives_account_lock_and_wallet_failure(self):
        from unittest.mock import patch
        self.enter()
        self.service._lock('safety_lock','Fixture account failure')
        self.now[0]+=60
        with patch.object(self.service,'wallet',side_effect=TimeoutError()):
            self.signal(self.now[0]-60,'Short')
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual(len(closes),1)
        self.assertEqual(closes[0]['side'],'Sell')
        self.assertFalse(self.service.state['entries'])
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)

    def test_symbol_protection_failure_does_not_disable_other_symbol(self):
        self.enter()
        self.exchange.active['BTCUSDT']['stopLoss']='0'
        self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.fill_closes()
        self.signal(self.now[0]-60,None,'ETHUSDT')
        self.now[0]+=60;self.signal(self.now[0]-60,'Long','ETHUSDT')
        self.assertTrue(any(o['symbol']=='ETHUSDT' and not o['reduceOnly'] for o in self.exchange.orders.values()))


    def test_opposite_exit_intent_retries_after_position_read_failure(self):
        from unittest.mock import patch
        self.enter();self.now[0]+=60
        with patch.object(self.service,'positions',side_effect=TimeoutError()):
            self.signal(self.now[0]-60,'Short')
        self.assertEqual(self.service.load()['owned']['BTCUSDT']['signal_exit'],'Opposite breakout')
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.service.monitor()
        self.assertEqual(len([o for o in self.exchange.orders.values() if o['reduceOnly']]),1)
        self.assertFalse(self.service.state['entries'])

    def test_stale_opposite_signal_does_not_close_during_lock(self):
        self.enter();self.service._lock('safety_lock','Fixture')
        self.now[0]+=1000
        self.signal(self.now[0]-500,'Short')
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_changed_ownership_is_never_closed_by_opposite_signal(self):
        self.enter();self.exchange.active['BTCUSDT']['side']='Sell'
        self.now[0]+=60;self.signal(self.now[0]-60,'Short')
        self.assertFalse(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.assertFalse(self.service.state['entries'])

    def test_other_owned_symbol_can_exit_while_failed_symbol_closes(self):
        self.enter()
        self.signal(self.now[0]-60,None,'ETHUSDT')
        self.now[0]+=60;self.signal(self.now[0]-60,'Long','ETHUSDT')
        eth=next(k for k,o in self.exchange.orders.items() if o['symbol']=='ETHUSDT' and not o['reduceOnly'])
        self.exchange.fill(eth);self.service.monitor()
        self.exchange.active['BTCUSDT']['stopLoss']='0'
        self.service.monitor()
        self.assertTrue(self.service.state['entries'])
        self.assertFalse(any(o['symbol']=='ETHUSDT' and o['reduceOnly'] for o in self.exchange.orders.values()))
        self.now[0]+=60;self.signal(self.now[0]-60,'Short','ETHUSDT')
        self.assertEqual({o['symbol'] for o in self.exchange.orders.values() if o['reduceOnly']},{'BTCUSDT','ETHUSDT'})
        self.assertNotIn('ETHUSDT',self.service.state['blocked_symbols'])

    def test_protection_verified_event_is_once_per_owned_trade_and_survives_restart(self):
        self.enter()
        message='BTCUSDT: position filled; Bybit stop loss verified'
        self.assertEqual(sum(e['message']==message for e in self.service.state['events']),1)
        self.service.monitor();self.service.monitor()
        restarted=self.create();restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        self.assertEqual(sum(e['message']==message for e in restarted.state['events']),1)

    def test_missing_stop_never_announces_verified(self):
        self.activate();self.signal(self.now[0]-60,None)
        self.now[0]+=60;self.signal(self.now[0]-60)
        self.exchange.fill(next(iter(self.exchange.orders)))
        self.exchange.active['BTCUSDT']['stopLoss']='0';self.exchange.fail_stop=True
        self.service.monitor()
        self.assertFalse(any('stop loss verified' in e['message'] for e in self.service.state['events']))

    def test_close_only_option_never_opens_reverse_order(self):
        self.cfg=self.cfg.model_copy(update={'opposite_action':'close_only'})
        self.service.configure(self.cfg)
        self.enter();self.now[0]+=60;self.signal(self.now[0]-60,'Short')
        self.fill_closes()
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
        self.assertEqual(self.service.state['reversals'],{})

    def test_strategy_cannot_change_with_open_position(self):
        self.enter();self.service.pause()
        with self.assertRaisesRegex(ValueError,'Close managed positions'):
            self.service.configure(self.cfg.model_copy(update={'strategy':'sma_cross'}))

    def test_history_retains_trade_details_and_deduplicates_restart(self):
        self.enter();self.now[0]+=60;self.signal(self.now[0]-60,'Short');self.fill_closes()
        result=self.service.history()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['symbol'],'BTCUSDT')
        self.assertEqual(result['items'][0]['side'],'Long')
        self.assertEqual(result['items'][0]['strategy'],'sr_breakout')
        self.assertGreater(result['items'][0]['qty'],0)
        self.assertEqual(self.create().history()['total'],1)
        self.assertEqual(self.service.history('ETHUSDT')['total'],0)

    def test_sma_signal_to_live_engine_reversal_and_stop_protection(self):
        from thoth.indicator import evaluate
        self.cfg=self.cfg.model_copy(update={'strategy':'sma_cross'})
        self.service.configure(self.cfg);self.activate()
        origin=self.now[0]-201*60
        prices=[100.]*200+[110.]*10+[80.]*10
        rows=[dict(time=origin+i*60,open=p,high=p+1,low=p-1,close=p,volume=10) for i,p in enumerate(prices)]
        output=evaluate(rows,self.cfg,now=rows[-1]['time']+60)
        self.signal(output[199]['time'],None)
        self.signal(output[200]['time'],output[200]['signal'])
        entry=next(iter(self.exchange.orders));self.exchange.fill(entry);self.service.monitor()
        short=next(bar for bar in output if bar['signal']=='Short')
        self.now[0]=short['time']+60;self.signal(short['time'],'Short')
        self.fill_closes()
        reversals=[(link,o) for link,o in self.exchange.orders.items() if o['side']=='Sell' and not o['reduceOnly']]
        self.assertEqual(len(reversals),1)
        self.exchange.fill(reversals[0][0]);self.service.monitor()
        self.assertEqual(self.exchange.active['BTCUSDT']['side'],'Sell')
        self.assertNotEqual(self.exchange.active['BTCUSDT']['stopLoss'],'0')
        self.assertEqual(self.service.history()['items'][0]['strategy'],'sma_cross')

    def test_legacy_history_import_is_idempotent_and_missing_fields_stay_unknown(self):
        self.service.state['closures']=[dict(symbol='ETHUSDT',closed_at=self.now[0],pnl=-.12,reason='Old close')]
        self.service.save()
        self.assertEqual(self.create().history()['total'],1)
        history=self.create().history()
        self.assertEqual(history['total'],1)
        self.assertNotIn('side',history['items'][0])
        self.assertEqual(history['items'][0]['strategy'],'sr_breakout')
        self.assertEqual(history['items'][0]['pnl'],-.12)

    def test_existing_legacy_history_strategy_is_backfilled_without_overwriting_tags(self):
        import json
        trades=[('legacy:old',dict(symbol='ETHUSDT',closed_at=10,pnl=-.3)),
                ('legacy:tagged',dict(symbol='ETHUSDT',closed_at=20,pnl=.2,strategy='sma_cross')),
                ('unidentified',dict(symbol='BTCUSDT',closed_at=30,pnl=0))]
        with self.service.db() as db:
            db.executemany('INSERT INTO trade_history VALUES(?,?,?,?)',[(link,t['symbol'],t['closed_at'],json.dumps(t)) for link,t in trades])
        result=self.create().history()['items']
        self.assertNotIn('strategy',result[0])
        self.assertEqual(result[1]['strategy'],'sma_cross')
        self.assertEqual(result[2]['strategy'],'sr_breakout')
        self.assertEqual(result[2]['pnl'],-.3)
        self.assertEqual(self.create().history()['items'],result)

    def test_blocked_reversal_activity_identifies_pause_reason(self):
        self.enter();self.service.pause();self.now[0]+=60
        self.signal(self.now[0]-60,'Short')
        messages=[e['message'] for e in self.service.state['events'] if 'replacement entry blocked:' in e['message']]
        self.assertEqual(len(messages),1)
        self.assertIn('paused',messages[0])
        self.assertIn('new entries disabled',messages[0])
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
        self.assertEqual(len([o for o in self.exchange.orders.values() if o['reduceOnly']]),1)

    def test_block_reason_distinguishes_cooldown_and_symbol_restriction(self):
        self.service.state.update(entries=True,status='cooldown',reason='Consecutive-loss pause; stops remain managed')
        self.assertIn('Consecutive-loss pause',self.service._reversal_block_reason('BTCUSDT'))
        self.service.state.update(status='running',reason='')
        self.service.state['blocked_symbols']['BTCUSDT']='Trailing stop update unconfirmed'
        self.assertEqual(self.service._reversal_block_reason('BTCUSDT'),'symbol blocked: Trailing stop update unconfirmed')
        self.assertEqual(self.service._reversal_block_reason('ETHUSDT'),'')

    def test_per_coin_pause_does_not_block_other_symbol_reversal(self):
        self.enter('ETHUSDT')
        from thoth.account_risk import closure
        for i in range(3):closure(self.service.state['risk'],'fixture-loss-'+str(i),-1,self.cfg,self.now[0],'BTCUSDT')
        self.service.save();self.service.monitor()
        self.assertEqual(self.service.state['status'],'running')
        self.now[0]+=60;self.signal(self.now[0]-60,'Short','ETHUSDT')
        self.fill_closes()
        self.assertTrue(any(o['symbol']=='ETHUSDT' and o['side']=='Sell' and not o['reduceOnly'] for o in self.exchange.orders.values()))
        self.signal(self.now[0]-60,None,'BTCUSDT');self.now[0]+=60;self.signal(self.now[0]-60,'Long','BTCUSDT')
        self.assertFalse(any(o['symbol']=='BTCUSDT' and not o['reduceOnly'] for o in self.exchange.orders.values()))

    def test_coin_cooldown_still_allows_exit_but_no_same_coin_replacement(self):
        self.enter()
        from thoth.account_risk import closure
        for i in range(3):closure(self.service.state['risk'],'fixture-loss-'+str(i),-1,self.cfg,self.now[0],'BTCUSDT')
        self.service.save();self.now[0]+=60;self.signal(self.now[0]-60,'Short')
        self.assertTrue(any(o['reduceOnly'] for o in self.exchange.orders.values()))
        self.fill_closes()
        self.assertEqual(len([o for o in self.exchange.orders.values() if not o['reduceOnly']]),1)
        self.assertTrue(self.service.state['entries'])
        self.assertTrue(any('per-coin consecutive-loss cooldown' in e['message'] for e in self.service.state['events']))

    def test_latched_daily_limit_can_be_manually_reviewed_after_flat_and_revised_allowance(self):
        self.enter();self.exchange.prices['BTCUSDT']=90;self.service.monitor();self.fill_closes()
        baseline=self.service.state['risk']['baseline'];peak=self.service.state['risk']['peak']
        self.assertTrue(self.service.state['risk']['daily_stop'])
        with self.assertRaisesRegex(ValueError,'allowance is exhausted'):self.activate()
        self.cfg=self.cfg.model_copy(update={'daily_loss_pct':10})
        self.service.configure(self.cfg)
        self.assertTrue(self.service.state['risk']['daily_stop'])
        self.activate()
        self.assertFalse(self.service.state['risk']['daily_stop'])
        self.assertEqual(self.service.state['risk']['baseline'],baseline)
        self.assertEqual(self.service.state['risk']['peak'],peak)
        self.assertTrue(self.service.state['entries'])

    def test_daily_peak_budget_and_coin_cooldown_survive_restart(self):
        self.enter();self.exchange.prices['BTCUSDT']=110;self.service.monitor()
        from thoth.account_risk import closure
        for i in range(3):closure(self.service.state['risk'],'fixture-loss-'+str(i),-1,self.cfg,self.now[0],'ETHUSDT')
        self.service.save()
        old=self.service.snapshot()['risk']
        restarted=self.create();restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        new=restarted.snapshot()['risk']
        self.assertEqual(new['peak'],old['peak'])
        self.assertEqual(new['loss_allowance'],old['loss_allowance'])
        self.assertEqual(new['symbols']['ETHUSDT']['pause_until'],old['symbols']['ETHUSDT']['pause_until'])
        self.assertFalse(restarted.state['entries'])

    def test_legacy_global_cooldown_does_not_become_all_coin_pause_after_migration(self):
        self.enter('ETHUSDT')
        self.service.state['config'].pop('loss_streak_scope')
        self.service.state['risk'].pop('symbols',None)
        self.service.state['risk'].update(streak=3,pause_until=self.now[0]+3600)
        self.service.save()
        restarted=self.create();restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        self.assertFalse(restarted.state['entries'])
        self.assertEqual(restarted.state['config']['loss_streak_scope'],'per_coin')
        restarted.activate('ENABLE TESTNET ORDERS')
        self.assertTrue(restarted.state['entries'])
        self.assertEqual(restarted.state['status'],'running')
        self.assertEqual(restarted.state['risk']['symbols'],{})

    def test_peak_daily_protection_closes_winning_positions_without_expanding_budget(self):
        self.cfg=self.cfg.model_copy(update={'daily_loss_pct':5})
        self.service.configure(self.cfg)
        self.enter('BTCUSDT')
        self.signal(self.now[0]-60,None,'ETHUSDT');self.now[0]+=60;self.signal(self.now[0]-60,'Long','ETHUSDT')
        eth=next(k for k,o in self.exchange.orders.items() if o['symbol']=='ETHUSDT' and not o['reduceOnly'])
        self.exchange.fill(eth);self.service.monitor()
        self.exchange.prices.update(BTCUSDT=200,ETHUSDT=102);self.service.monitor()
        self.assertEqual(self.service.state['risk']['peak'],151)
        self.assertEqual(self.service.state['risk']['loss_allowance'],5)
        self.assertEqual(self.service.state['risk']['stop_equity'],146)
        self.exchange.prices['BTCUSDT']=190;self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual({o['symbol'] for o in closes},{'BTCUSDT','ETHUSDT'})
        self.assertEqual(self.service.state['status'],'daily_stop')
        self.fill_closes()
        self.assertEqual(self.exchange.balance,146)
        self.assertFalse(self.service.state['entries'])

    def test_increasing_daily_limit_does_not_unlock_before_positions_settle(self):
        self.enter();self.exchange.prices['BTCUSDT']=90;self.service.monitor()
        self.cfg=self.cfg.model_copy(update={'daily_loss_pct':10});self.service.configure(self.cfg)
        with self.assertRaisesRegex(ValueError,'close and reconcile'):
            self.activate()
        self.assertTrue(self.service.state['risk']['daily_stop'])
        self.assertFalse(self.service.state['entries'])

class TestnetRecovery(unittest.TestCase):
    setUp=TestnetIntegration.setUp
    tearDown=TestnetIntegration.tearDown
    create=TestnetIntegration.create
    activate=TestnetIntegration.activate
    signal=TestnetIntegration.signal
    enter=TestnetIntegration.enter
    fill_closes=TestnetIntegration.fill_closes
    # Inherit the realistic exchange setup, but add failure-path scenarios.
    def test_daily_stop_restart_still_recovers_exits(self):
        self.enter();self.exchange.prices['BTCUSDT']=90;self.service.monitor()
        restarted=self.create();restarted.activate('ENABLE TESTNET ORDERS',recovery=True)
        self.assertFalse(restarted.snapshot()['entries'])
        self.assertEqual(restarted.snapshot()['status'],'daily_stop')
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual(len(closes),1)

    def test_three_losses_pause_and_cannot_be_manually_bypassed(self):
        self.cfg=TestnetSettings(dedicated_account=True,daily_loss_pct=20,loss_streak_scope='account')
        self.service.configure(self.cfg)
        for i in range(3):
            self.exchange.prices['BTCUSDT']=100
            self.activate();self.signal(self.now[0]-60,None)
            self.now[0]+=60;self.signal(self.now[0]-60,'Long')
            entry=[k for k,o in self.exchange.orders.items() if not o['reduceOnly'] and o['orderStatus']=='New'][-1]
            self.exchange.fill(entry);self.service.monitor()
            self.exchange.prices['BTCUSDT']=98;self.service.monitor();self.fill_closes()
        self.assertEqual(self.service.snapshot()['status'],'cooldown')
        with self.assertRaises(ValueError):self.activate()
        self.now[0]+=3601;self.service.monitor()
        self.assertEqual(self.service.snapshot()['status'],'running')

    def test_concurrent_signals_queue_until_first_fill_is_protected(self):
        self.activate()
        cfg=Settings(**self.cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
        stamp=self.now[0]-60
        self.service.apply_signals(cfg,{s:dict(time=stamp,mark=100,signal=None) for s in ('BTCUSDT','ETHUSDT')})
        self.now[0]+=60
        self.service.apply_signals(cfg,{s:dict(time=stamp+60,mark=100,signal='Long') for s in ('BTCUSDT','ETHUSDT')})
        self.assertEqual(len(self.exchange.orders),1)
        self.exchange.fill(next(iter(self.exchange.orders)));self.service.monitor()
        self.assertEqual(len(self.exchange.orders),2)

    def test_capital_transfer_does_not_mask_daily_loss(self):
        self.activate()
        self.exchange.balance=107
        self.exchange.logs=[dict(id='transfer-fixture',currency='USDT',type='TRANSFER_IN',change='10')]
        self.service.monitor()
        self.assertEqual(self.service.snapshot()['status'],'daily_stop')
        self.assertEqual(self.service.snapshot()['risk']['loss_pct'],3)


class TestnetPortfolioStop(unittest.TestCase):
    setUp=TestnetIntegration.setUp
    tearDown=TestnetIntegration.tearDown
    create=TestnetIntegration.create
    activate=TestnetIntegration.activate
    signal=TestnetIntegration.signal
    enter=TestnetIntegration.enter
    fill_closes=TestnetIntegration.fill_closes

    def test_position_loss_stop_requires_flat_manual_restart(self):
        self.cfg=TestnetSettings(dedicated_account=True,portfolio_loss_enabled=True,portfolio_loss_pct=20)
        self.service.configure(self.cfg);self.enter()
        self.exchange.prices['BTCUSDT']=99
        self.service.monitor()
        self.assertEqual(self.service.snapshot()['status'],'portfolio_stop')
        with self.assertRaises(ValueError):self.activate()
        self.fill_closes()
        self.assertTrue(self.service.snapshot()['risk']['portfolio_stop'])
        result=self.activate()
        self.assertFalse(result['risk']['portfolio_stop'])
        self.assertEqual(result['status'],'running')


class TestnetFillRaces(unittest.TestCase):
    setUp=TestnetIntegration.setUp
    tearDown=TestnetIntegration.tearDown
    create=TestnetIntegration.create
    activate=TestnetIntegration.activate
    signal=TestnetIntegration.signal
    fill_closes=TestnetIntegration.fill_closes

    def submit_entry(self):
        self.activate();self.signal(self.now[0]-60,None);self.now[0]+=60;self.signal(self.now[0]-60,'Long')
        return next(iter(self.exchange.orders))

    def test_attached_stop_can_close_before_first_position_poll(self):
        link=self.submit_entry();self.exchange.fill(link)
        self.exchange.prices['BTCUSDT']=98
        close=self.exchange.close_payload('BTCUSDT','Long','.5','fixture-native-stop')
        self.exchange.submit(close);self.exchange.fill(close['orderLinkId'])
        self.service.monitor()
        self.assertEqual(self.service.state['owned'],{})
        self.assertEqual(len(self.service.snapshot()['closures']),1)
        self.assertEqual(self.service.snapshot()['risk']['streak'],1)
        self.assertEqual(self.service.journal.get(link)['phase'],'closed')

    def test_close_all_latch_catches_fill_after_cancel_request(self):
        link=self.submit_entry()
        original=self.exchange.request
        def cancel_race(method,path,params,private=False):
            if path=='/v5/order/cancel' and params['orderLinkId']==link:
                self.exchange.fill(link)
                return {}
            return original(method,path,params,private=private)
        self.exchange.request=cancel_race
        self.service.close_all()
        self.assertTrue(self.service.state['flatten_reason'])
        self.assertTrue(self.service.snapshot()['closing'])
        self.service.monitor()
        self.assertEqual(len([o for o in self.exchange.orders.values() if o['reduceOnly']]),1)
        self.fill_closes()
        self.assertEqual(self.exchange.active,{})
        self.assertFalse(self.service.state['flatten_reason'])
        self.assertFalse(self.service.snapshot()['closing'])
        self.assertFalse(self.service.snapshot()['entries'])
