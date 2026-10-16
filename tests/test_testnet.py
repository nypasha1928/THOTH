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
            self.active[params['symbol']]['stopLoss']=params['stopLoss'];return {}
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
            self.active[symbol]=dict(size=str(qty),side=order['side'],positionIdx=0,avgPrice=str(price),stopLoss=order.get('stopLoss','0'),liqPrice='0')
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
        self.exchange.fail_stop=True
        self.service.monitor()
        closes=[o for o in self.exchange.orders.values() if o['reduceOnly']]
        self.assertEqual(len(closes),1)
        self.assertEqual(closes[0]['side'],'Sell')
        self.assertFalse(self.service.snapshot()['entries'])
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
        self.cfg=TestnetSettings(dedicated_account=True,daily_loss_pct=20)
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
