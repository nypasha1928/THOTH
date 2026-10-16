"""Guarded testnet orchestration. Production exchange orders are not supported.

The dashboard must explicitly activate testnet orders; credentials, a password,
a dedicated account acknowledgement and a server-side feature flag are required.
After a restart only risk management resumes; new entries need manual activation.
"""
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from urllib.request import urlopen
from .verification import source_fingerprint,strategy_fingerprint,execution_fingerprint

from .account_risk import new_risk, sample, closure, day
from .bybit_testnet import TestnetClient, BybitError, dec, order_payload, stop_price
from .credentials import runtime_secret
from .execution import ExecutionJournal
from .models import TestnetSettings, Settings


class TestnetSupervisor:
    def __init__(self,path,enabled=False,password_configured=False,client_factory=TestnetClient,credentials=None,clock=time.time,environment='testnet'):
        if environment not in ('testnet','live'):raise ValueError('Unsupported exchange environment')
        self.environment=environment
        self.label='LIVE' if environment=='live' else 'testnet'
        self.confirmation='ENABLE LIVE ORDERS' if environment=='live' else 'ENABLE TESTNET ORDERS'
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.enabled=enabled
        self.password_configured=password_configured
        self.factory=client_factory
        self.credentials=credentials or (lambda:(runtime_secret('THOTH_BYBIT_TESTNET_KEY'),runtime_secret('THOTH_BYBIT_TESTNET_SECRET')))
        self.clock=clock
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.client=None
        self.journal=None
        self.thread=None
        self.feed=None
        self.instrument_cache={}
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS supervisor(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM supervisor').fetchone():
                self.save(dict(config=TestnetSettings().model_dump(),account_binding=None,
                    management=False,entries=False,status='disabled',reason=self.label+' has not been activated',
                    owned={},risk=new_risk(),equity=None,positions=[],events=[],cursors={},
                    reversals={},pending_signals={},last_monitor=None,closures=[],feed_health=None))
        self.state=self.load()
        # Persistence retains exit obligations, never permission to open new orders.
        self.state['entries']=False
        self.state['pending_signals']={}
        self.state['reversals']={}
        if self.state['management']:
            self.state.update(status='recovery_required',reason='Recover managed positions before enabling new entries')
        self.save()

    def db(self):return sqlite3.connect(self.path,timeout=15)

    def load(self):
        with self.db() as db:state=json.loads(db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()[0])
        state.setdefault('pending_signals',{})
        state['config']={**TestnetSettings().model_dump(),**state['config']}
        state['risk'].setdefault('portfolio_stop',False)
        state.setdefault('daily_resume_intent',False)
        state.setdefault('flatten_reason',None)
        return state

    def save(self,state=None):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO supervisor VALUES(1,?)',(json.dumps(state or self.state,allow_nan=False),))

    def event(self,message):
        if self.environment=='live':message=message.replace('Testnet','LIVE').replace('testnet','live')
        self.state['events']=(self.state['events']+[{'time':self.clock(),'message':message}])[-100:]

    def snapshot(self):
        s=self.load()
        return {k:s[k] for k in ('config','management','entries','status','reason','equity','positions','events','last_monitor','closures','feed_health')} | {
            'closing':bool(s.get('flatten_reason')),'environment':self.environment,'enabled_on_server':self.enabled,'live_supported':self.environment=='live',
            'risk':{k:s['risk'][k] for k in ('day','baseline','loss_pct','streak','pause_until','daily_stop','baseline_required','portfolio_stop')},
            'execution': [{'symbol':r['payload']['symbol'],'phase':r['phase'],'reason':r['result'].get('reason','')} for r in self.journal.records()] if self.journal else []}

    def configure(self,cfg):
        with self.lock:
            self.state=self.load()
            if self.state['entries']:raise ValueError('Pause testnet entries before editing settings')
            if any(s not in cfg.symbols for s in self.state['owned']):raise ValueError('Close managed positions before removing their symbols')
            if self.state['owned'] and cfg.margin_mode!=self.state['config']['margin_mode']:
                raise ValueError('Cannot change margin mode while positions are open')
            self.state['config']=cfg.model_dump()
            self.state['cursors']={}
            self.save()
        return self.snapshot()

    def pages(self,path,params,limit=100):
        result=[];cursor=None;seen=set()
        for _ in range(100):
            query={**params,'limit':limit}
            if cursor:query['cursor']=cursor
            page=self.client.request('GET',path,query,private=True)
            result.extend(page['list'])
            cursor=page.get('nextPageCursor')
            if not cursor:return result
            if cursor in seen:raise ValueError('Exchange pagination did not advance')
            seen.add(cursor)
        raise ValueError('Exchange response exceeded pagination safety bound')

    def positions(self):
        rows=self.pages('/v5/position/list',dict(category='linear',settleCoin='USDT'),200)
        return {p['symbol']:p for p in rows if dec(p.get('size','0'))>0}

    def wallet(self):
        response=self.client.request('GET','/v5/account/wallet-balance',dict(accountType='UNIFIED'),private=True)
        accounts=response['list']
        if len(accounts)!=1:raise ValueError('Unified account balance unavailable')
        coins=accounts[0]['coin']
        if any(c['coin']!='USDT' and (dec(c.get('equity','0'))!=0 or dec(c.get('borrowAmount','0'))!=0) for c in coins):
            raise ValueError('Use a dedicated USDT-only testnet account')
        usdt=next((c for c in coins if c['coin']=='USDT'),None)
        if not usdt or dec(usdt.get('spotBorrow','0'))!=0 or dec(usdt.get('borrowAmount','0'))!=0:
            raise ValueError('USDT balance must be available without borrowing')
        equity=dec(usdt['equity'])
        if not equity.is_finite() or equity<0:raise ValueError('Invalid USDT equity')
        # Unified isolated accounts do not expose applicable account-wide totals;
        # USDT coin equity includes both the wallet and unrealized contract P&L.
        return float(equity)

    def transfers(self):
        since=self.state['risk'].get('baseline_ms')
        if since is None or self.state['risk']['day']!=day(self.clock()):return 0.0
        rows=self.pages('/v5/account/transaction-log',dict(accountType='UNIFIED',currency='USDT',startTime=since,endTime=int(self.clock()*1000)),50)
        total=dec(0);seen=set()
        for row in rows:
            identity=row.get('id')
            if identity is None:raise ValueError('Transaction identity unavailable')
            if identity in seen:continue
            seen.add(identity)
            if row.get('currency')!='USDT':continue
            kind=row.get('type')
            if kind in ('TRANSFER_IN','TRANSFER_OUT'):total+=dec(row['change'])
            elif kind not in ('TRADE','SETTLEMENT','DELIVERY','LIQUIDATION'):
                raise ValueError('Unexpected account cash flow; account review required')
        return float(total)

    def _preflight(self):
        if not self.enabled:raise ValueError(self.label+' execution is disabled on the server')
        if not self.password_configured:raise ValueError('Configure dashboard authentication before testnet execution')
        cfg=TestnetSettings(**self.state['config'])
        if not cfg.symbols:raise ValueError('Select at least one symbol')
        if not cfg.dedicated_account:raise ValueError('A dedicated USDT-only testnet account must be acknowledged')
        if cfg.margin_mode not in ('isolated','cross'):raise ValueError('Confirm a margin mode')
        key,secret=self.credentials()
        if not key or not secret:raise ValueError('Testnet credentials are not configured')
        client=self.factory(key,secret,armed=True)
        if client.BASE!={'testnet':'https://api-testnet.bybit.com','live':'https://api.bybit.com'}[self.environment]:raise ValueError('Exchange endpoint/environment mismatch')
        info=client.request('GET','/v5/user/query-api',{},private=True)
        perms=info.get('permissions',{})
        if not isinstance(perms.get('Wallet'),list) or any('withdraw' in str(p).lower() for p in perms['Wallet']):
            raise ValueError('Key withdrawal permissions must be disabled and verifiable')
        if str(info.get('readOnly'))!='0' or not (
            {'Order','Position'}.issubset(set(perms.get('ContractTrade',[]))) or 'DerivativesTrade' in perms.get('Derivatives',[])):
            raise ValueError('Key must have contract order and position permissions')
        uid=info.get('userID')
        if not uid:raise ValueError('Cannot bind testnet execution to an account')
        binding=hashlib.sha256(('bybit-'+self.environment+':'+str(uid)).encode()).hexdigest()
        if self.state['account_binding'] and self.state['account_binding']!=binding:
            raise ValueError('Credentials belong to a different testnet account')
        account=client.request('GET','/v5/account/info',{},private=True)
        expected='ISOLATED_MARGIN' if cfg.margin_mode=='isolated' else 'REGULAR_MARGIN'
        if account.get('marginMode')!=expected:raise ValueError('Bybit margin mode differs from the selected setting; change it in Bybit first')
        if account.get('unifiedMarginStatus') not in (5,6):raise ValueError('Unified Trading Account 2.0 is required')
        self.client=client
        self.journal=ExecutionJournal(Path(self.path).with_suffix('.execution.sqlite'),client,environment=self.environment)
        active=self.positions()
        known={r['payload']['symbol']:r for r in self.journal.records() if not r['payload'].get('reduceOnly') and r['phase'] not in ('closed','rejected','cancelled')}
        if any(p.get('positionIdx')!=0 for p in active.values()):raise ValueError('Only one-way positions are supported')
        for symbol,p in active.items():
            owner=self.state['owned'].get(symbol)
            intent=known.get(symbol)
            if not owner and not intent:raise ValueError('Existing positions are not owned by THOTH')
            if owner and (p['side']!=owner['side'] or dec(p['size'])>dec(owner['qty'])):
                raise ValueError('Managed position was changed outside THOTH')
            if not owner and (p['side']!=intent['payload']['side'] or dec(p['size'])>dec(intent['payload']['qty'])):
                raise ValueError('Position does not match a recorded THOTH intent')
        orders=self.pages('/v5/order/realtime',dict(category='linear',settleCoin='USDT',openOnly=0),50)
        identities={r['link'] for r in self.journal.records()}
        for order in orders:
            protection=(order.get('stopOrderType') in ('StopLoss','TakeProfit','PartialStopLoss','PartialTakeProfit') and order['symbol'] in active)
            if order.get('orderLinkId') not in identities and not protection:
                raise ValueError('Cancel non-THOTH orders before activating this dedicated account')
        self.wallet()
        self.state['account_binding']=binding
        self.state['real_testnet_exchange']=(type(client) is TestnetClient and client.transport is urlopen)
        self.state['source_fingerprint']=source_fingerprint()
        self.state['execution_fingerprint']=execution_fingerprint()
        self.state['strategy_fingerprint']=strategy_fingerprint(cfg)
        return cfg

    def activate(self,confirmation,recovery=False):
        with self.lock:
            self.state=self.load()
            if confirmation!=self.confirmation:raise ValueError('Explicit testnet confirmation is required')
            if not recovery and self.state['risk']['daily_stop'] and self.state['risk']['day']==day(self.clock()):
                raise ValueError('Daily loss lock cannot be bypassed')
            if not recovery and self.state['risk'].get('pause_until') and self.clock()<self.state['risk']['pause_until']:
                raise ValueError('Consecutive-loss pause cannot be bypassed')
            self.state.update(entries=False,status='paused',reason='Checking testnet account prerequisites')
            self.save()
            cfg=self._preflight()
            if not recovery and self.state['risk']['portfolio_stop']:
                if self.positions() or self.state['owned']:raise ValueError('Wait for all position-stop exits to settle before restarting')
                self.state['risk']['portfolio_stop']=False
            if not recovery and self.state['flatten_reason']:
                if self.positions() or self.state['owned'] or any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records()):
                    raise ValueError('Wait for close-all orders and results to settle before restarting')
                self.state['flatten_reason']=None
            self.state['management']=True
            self.state.update(entries=not recovery,status='paused' if recovery else 'running',reason='Restart recovery; new entries paused' if recovery else '')
            self.save()
            try:self.monitor()
            except Exception:
                self._lock('safety_lock','Testnet monitoring failed; new entries disabled, inspect the account')
                raise ValueError('Testnet monitoring unavailable; entries remain disabled') from None
            self.event('Testnet risk management recovered' if recovery else 'Testnet entries enabled')
            self.save()
        return self.snapshot()

    def pause(self):
        with self.lock:
            self.state=self.load();self.state.update(entries=False,status='paused',reason='Entries paused; risk management remains active',pending_signals={},reversals={},daily_resume_intent=False);self.save()
        return self.snapshot()

    def _lock(self,status,reason):
        if self.environment=='live':reason=reason.replace('Testnet','LIVE').replace('testnet','live')
        changed=self.state['status']!=status or self.state['reason']!=reason
        self.state.update(entries=False,status=status,reason=reason,pending_signals={},reversals={})
        if changed:self.event(reason)
        self.save()

    def _adopt(self,record,active):
        if record['payload'].get('reduceOnly'):return
        symbol=record['payload']['symbol']
        if symbol in self.state['owned']:return
        if record['phase'] not in ('protected','stop_error','mismatch'):return
        p=active.get(symbol)
        order=self.client.reconcile(symbol,record['link'])
        if not order:return
        filled=dec(order.get('cumExecQty','0'))
        if not filled.is_finite() or filled<=0 or filled>dec(record['payload']['qty']):return
        if order.get('side',record['payload']['side'])!=record['payload']['side']:return
        if p and (dec(p['size'])!=filled or p['side']!=record['payload']['side']):return
        # A position can hit its attached stop before our first poll. Own the
        # confirmed fill even when already flat so its realized result settles.
        if not p and order.get('orderStatus') not in ('Filled','Cancelled','PartiallyFilledCanceled'):return
        entry=dec(p['avgPrice'] if p else order.get('avgPrice','0'))
        if not entry.is_finite() or entry<=0:return
        leverage=dec((p or {}).get('leverage') or self.state['config']['leverage'])
        self.state['owned'][symbol]=dict(link=record['link'],side=record['payload']['side'],qty=str(filled),entry=str(entry),
            stop_dollars=record['stop_dollars'],margin=str(entry*filled/leverage),opened_ms=int(order['createdTime']),
            close_link=None,close_attempt=0,reason=None,protection_confirmed=record['phase']=='protected',
            source_fingerprint=self.state.get('source_fingerprint'),execution_fingerprint=self.state.get('execution_fingerprint'),strategy_fingerprint=self.state.get('strategy_fingerprint'),
            real_exchange=self.state.get('real_testnet_exchange',False))
        self.save()

    def _cancel_entries(self):
        for record in self.journal.records():
            if record['payload'].get('reduceOnly') or record['phase'] in ('protected','closed','cancelled','rejected'):continue
            try:self.client.request('POST','/v5/order/cancel',dict(category='linear',symbol=record['payload']['symbol'],orderLinkId=record['link']),private=True)
            except Exception:pass  # Retain lock; monitoring will reconcile any late fills.

    def _close(self,symbol,p,reason):
        owner=self.state['owned'][symbol]
        if p['side']!=owner['side'] or p.get('positionIdx')!=0 or dec(p['size'])>dec(owner['qty']):
            self._lock('safety_lock','Position ownership changed; manual account reconciliation required');return
        if owner['close_link']:
            record=self.journal.poll(owner['close_link'])
            if record['phase'] not in ('closed','cancelled','rejected','mismatch'):return
            if record['phase']=='closed':return
            # A terminal partial close may leave a smaller residual position.
            order=self.client.reconcile(symbol,owner['close_link'])
            if not order or order.get('orderStatus') not in ('Filled','Cancelled','PartiallyFilledCanceled','Rejected','Deactivated'):return
            owner['close_link']=None
        if owner['close_attempt']>=5:
            self._lock('safety_lock','Close retries exhausted; check Bybit immediately');return
        owner['close_attempt']+=1
        identifier=owner['link']+':close:'+str(owner['close_attempt'])
        payload=self.client.close_payload(symbol,'Long' if p['side']=='Buy' else 'Short',p['size'],identifier)
        owner.update(close_link=payload['orderLinkId'],reason=reason)
        self.save()  # Preserve deterministic close identity before submission.
        self.journal.submit(payload)
        self.event(symbol+': reduce-only close requested ('+reason+')')
        self.save()

    def _settle(self,symbol,owner,cfg):
        rows=[]
        start=owner['opened_ms'];end=int(self.clock()*1000)
        if end-start>729*86400000:raise ValueError('Closed trade exceeds supported history window')
        while start<=end:
            boundary=min(end,start+7*86400000-1)
            rows.extend(self.pages('/v5/position/closed-pnl',dict(category='linear',symbol=symbol,startTime=start,endTime=boundary),100))
            start=boundary+1
        rows=[r for r in rows if int(r['createdTime'])>=owner['opened_ms'] and r.get('execType')=='Trade']
        unique={r['orderId']:r for r in rows}
        if sum((dec(r['closedSize']) for r in unique.values()),dec(0))!=dec(owner['qty']):
            return False  # Delay entries until complete realized results are visible.
        pnl=float(sum((dec(r['closedPnl']) for r in unique.values()),dec(0)))
        closure(self.state['risk'],owner['link'],pnl,cfg,self.clock())
        self.state['closures']=(self.state['closures']+[dict(symbol=symbol,pnl=pnl,closed_at=self.clock(),reason=owner['reason'] or 'Exchange stop/close',protection_confirmed=owner.get('protection_confirmed',False),source_fingerprint=owner.get('source_fingerprint'),execution_fingerprint=owner.get('execution_fingerprint'),strategy_fingerprint=owner.get('strategy_fingerprint'),real_exchange=owner.get('real_exchange',False))])[-200:]
        # Mark entry terminal so startup does not treat old fills as pending ownership.
        self.journal.update(owner['link'],'closed',{'reason':'Position flat and realized P&L reconciled'})
        del self.state['owned'][symbol]
        self.event(symbol+': position flat; realized P&L reconciled')
        self.save()
        return True

    def monitor(self):
        with self.lock:
            if not self.client or not self.state['management']:return
            cfg=TestnetSettings(**self.state['config'])
            # Order acknowledgements, fills and protection remain separate states.
            for record in self.journal.records():
                if record['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch'):
                    self.journal.poll(record['link'])
            active=self.positions()
            for record in self.journal.records():self._adopt(record,active)
            for symbol,p in active.items():
                if symbol not in self.state['owned']:
                    self._lock('safety_lock','Unowned exchange position detected; new entries blocked')
                    return
            for symbol,owner in list(self.state['owned'].items()):
                p=active.get(symbol)
                if not p:
                    try:self._settle(symbol,owner,cfg)
                    except Exception:self._lock('settlement_pending','Closed trade result unavailable; new entries blocked')
                elif p['side']!=owner['side'] or p.get('positionIdx')!=0 or dec(p['size'])>dec(owner['qty']):
                    self._lock('safety_lock','Exchange position changed outside THOTH');return
                else:
                    record=self.journal.get(owner['link'])
                    if record['phase']=='stop_error':
                        self._lock('protection_failure','Protective stop failed; closing managed exposure')
                        self._close(symbol,p,'Protective stop failure')
                    elif record['phase']=='protected' and not owner['close_link']:
                        desired=stop_price('Long' if p['side']=='Buy' else 'Short',p['avgPrice'],p['size'],owner['stop_dollars'],record['instrument']['priceFilter']['tickSize'])
                        if dec(p.get('stopLoss','0'))!=dec(desired):
                            self._lock('protection_failure','Protective stop changed or missing; closing managed exposure')
                            self._close(symbol,p,'Protective stop missing')
            equity=self.wallet()
            risk=sample(self.state['risk'],equity,self.transfers(),cfg,self.clock())
            self.state.update(equity=equity,last_monitor=self.clock(),positions=[dict(symbol=s,side='Long' if p['side']=='Buy' else 'Short',qty=p['size'],entry=p['avgPrice'],mark=p.get('markPrice'),pnl=float(p.get('unrealisedPnl','0')),stop=p.get('stopLoss'),stop_dollars=self.state['owned'].get(s,{}).get('stop_dollars'),opened_at=self.state['owned'].get(s,{}).get('opened_ms')) for s,p in active.items()])
            if risk=='daily_stop':
                if self.state['status']!='daily_stop':self.state['daily_resume_intent']=self.state['entries']
                self._lock('daily_stop','Daily account loss limit reached; closing all THOTH positions')
                self._cancel_entries()
                for symbol,p in active.items():self._close(symbol,p,'Daily loss limit')
            elif risk=='baseline_required':self._lock('baseline_required','Midnight sample missed; review the new daily baseline while flat')
            elif risk=='cooldown' and self.state['status'] not in ('daily_stop','safety_lock','protection_failure','baseline_required'):
                self.state.update(status='cooldown',reason='Consecutive-loss pause; stops remain managed')
            elif self.state['status']=='cooldown':self.state.update(status='running' if self.state['entries'] else 'paused',reason='')
            if self.state['status']=='daily_stop' and not self.state['risk']['daily_stop']:
                resume=cfg.daily_resume_automatic and self.state['daily_resume_intent']
                self.state.update(status='running' if resume else 'paused',entries=resume,reason='' if resume else 'New UTC day; manually enable entries')
            if cfg.portfolio_loss_enabled and risk!='daily_stop':
                losers=[(s,p) for s,p in active.items() if float(p.get('unrealisedPnl','0'))<0]
                losses=sum(-float(p['unrealisedPnl']) for _,p in losers)
                selected=list(active) if cfg.portfolio_loss_basis=='all_margin' else [s for s,_ in losers]
                margin=sum(float(self.state['owned'][s].get('margin',dec(self.state['owned'][s]['entry'])*dec(self.state['owned'][s]['qty'])/cfg.leverage)) for s in selected if s in self.state['owned'])
                if margin>0 and losses/margin*100>=cfg.portfolio_loss_pct:self.state['risk']['portfolio_stop']=True
            if self.state['risk']['portfolio_stop'] and risk!='daily_stop':
                self._lock('portfolio_stop','Overall position loss limit; manual restart required')
                self._cancel_entries()
                for symbol,p in active.items():
                    if symbol in self.state['owned']:self._close(symbol,p,'Overall position loss limit')
            for symbol,p in active.items():
                owner=self.state['owned'].get(symbol)
                if not owner:continue
                if owner['close_link']:self._close(symbol,p,owner['reason'] or 'Pending close')
                elif float(p.get('unrealisedPnl','0'))<=-float(owner['stop_dollars']):self._close(symbol,p,'Individual dollar loss limit')
            if self.state['flatten_reason']:
                self._cancel_entries()
                for symbol,p in active.items():
                    if symbol in self.state['owned']:self._close(symbol,p,self.state['flatten_reason'])
                if not active and not self.state['owned'] and not any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records()):
                    self.state['flatten_reason']=None
            pending=self.journal.records()
            if any(r['phase']=='unknown' for r in pending):
                self._lock('safety_lock','Order outcome is unknown; reconcile before new entries')
            self._reversals(cfg)
            self._drain(cfg)
            self.save()

    def close_all(self):
        with self.lock:
            self.state=self.load()
            if not self.client or not self.state['management']:raise ValueError('Activate testnet risk management first')
            self._lock('paused','Manual close all; new entries disabled')
            self.state['flatten_reason']='Manual close all'
            self.save()
            self._cancel_entries()
            active=self.positions()
            for symbol,p in active.items():
                if symbol in self.state['owned']:self._close(symbol,p,'Manual close all')
            self.save()
        return self.snapshot()

    def establish_baseline(self):
        with self.lock:
            self.state=self.load()
            if not self.client:raise ValueError('Connect testnet management first')
            if self.state['risk']['day']==day(self.clock()) and self.state['risk']['daily_stop']:
                raise ValueError('Today\'s daily loss lock cannot be reset')
            if self.positions() or self.state['owned']:raise ValueError('Account must be flat and all results reconciled')
            if any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records()):raise ValueError('Resolve all orders before setting a baseline')
            sample(self.state['risk'],self.wallet(),0,TestnetSettings(**self.state['config']),self.clock(),initial=True)
            self.state.update(status='paused',entries=False,reason='New daily baseline established; manually enable entries')
            self.save()
        return self.snapshot()

    def apply_signals(self,cfg,observations):
        with self.lock:
            if not self.client or not self.state['management']:return
            current=TestnetSettings(**self.state['config'])
            if cfg!=Settings(**current.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump():return
            self.monitor()
            identity=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()[:16]
            for symbol,observation in observations.items():
                key=identity+':'+symbol;stamp=observation['time'];previous=self.state['cursors'].get(key)
                if previous is not None and stamp<=previous:continue
                self.state['cursors'][key]=stamp
                self.save()  # Never replay this closed candle after a crash.
                if previous is None or not observation.get('signal') or not self.state['entries'] or self.state['status']!='running':continue
                if self.clock()-stamp>(120 if current.timeframe=='1' else 172800):continue
                side='Buy' if observation['signal']=='Long' else 'Sell'
                owner=self.state['owned'].get(symbol)
                if owner:
                    if owner['side']==side:continue
                    self.state['reversals'][symbol]=dict(side=observation['signal'],time=stamp,identity=identity)
                    p=self.positions().get(symbol)
                    if p:self._close(symbol,p,'Opposite breakout')
                    self.save();continue
                self.state['pending_signals'][symbol]=dict(side=observation['signal'],time=stamp,identity=identity)
            self._reversals(current)
            self._drain(current)
            self.save()

    def _entry(self,symbol,side,stamp,identity,cfg):
        if self.state['status']!='running' or not self.state['entries']:return
        if len(self.state['owned'])>=cfg.max_positions:return
        if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
        # Never place the next trade while a previous close awaits realized P&L.
        active=self.positions()
        if symbol in active or set(self.state['owned'])-set(active):return
        try:
            instrument=self.instrument_cache.get(symbol)
            if not instrument:instrument=self.client.instrument(symbol);self.instrument_cache[symbol]=instrument
            leverage=instrument['leverageFilter']
            if not (dec(leverage['minLeverage'])<=cfg.leverage<=dec(leverage['maxLeverage'])) or dec(cfg.leverage)%dec(leverage['leverageStep'])!=0:
                raise ValueError('Requested leverage is unsupported')
            ticker=self.client.request('GET','/v5/market/tickers',dict(category='linear',symbol=symbol))['list'][0]
            price=dec(ticker['lastPrice'])
            if cfg.stop_dollars>=cfg.position_value/cfg.leverage:
                raise ValueError('Dollar stop must be below the initial margin; liquidation could precede it')
            modes=self.client.request('GET','/v5/position/list',dict(category='linear',symbol=symbol),private=True)['list']
            if any(p.get('positionIdx')!=0 for p in modes):raise ValueError('Symbol must use one-way position mode')
            payload=order_payload(symbol,side,cfg.position_value,price,instrument,identity+':'+symbol+':'+str(stamp))
            initial_stop=stop_price(side,price,payload['qty'],cfg.stop_dollars,instrument['priceFilter']['tickSize'])
            payload.update(stopLoss=initial_stop,slTriggerBy='MarkPrice',tpslMode='Full')
            try:self.client.request('POST','/v5/position/set-leverage',dict(category='linear',symbol=symbol,buyLeverage=str(cfg.leverage),sellLeverage=str(cfg.leverage)),private=True)
            except BybitError as exc:
                if exc.code!=110043:raise
            self.journal.submit(payload,cfg.stop_dollars,instrument)
            self.event(symbol+': testnet breakout order submitted; awaiting fill/protection')
        except ValueError as exc:self.event(symbol+': entry skipped: '+str(exc))
        except Exception:self._lock('safety_lock','Testnet entry operation failed; reconcile before enabling entries')
        self.save()

    def _drain(self,cfg):
        for symbol,intent in list(self.state['pending_signals'].items()):
            if self.clock()-intent['time']>(120 if cfg.timeframe=='1' else 172800):
                del self.state['pending_signals'][symbol];continue
            if self.state['status']!='running' or not self.state['entries'] or len(self.state['owned'])>=cfg.max_positions:return
            if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
            del self.state['pending_signals'][symbol]
            self._entry(symbol,intent['side'],intent['time'],intent['identity'],cfg)
            return

    def _reversals(self,cfg):
        for symbol,reversal in list(self.state['reversals'].items()):
            if self.clock()-reversal['time']>(120 if cfg.timeframe=='1' else 172800):del self.state['reversals'][symbol];continue
            if symbol in self.state['owned']:continue
            if self.state['status']!='running' or not self.state['entries']:continue
            if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
            del self.state['reversals'][symbol]
            self._entry(symbol,reversal['side'],reversal['time'],reversal['identity'],cfg)

    def run(self):
        while not self.stop_event.is_set():
            try:
                if not self.client and self.enabled and self.password_configured and self.state['management']:
                    self.activate(self.confirmation,recovery=True)
                elif self.client:self.monitor()
            except ValueError as exc:
                with self.lock:self._lock('safety_lock',str(exc))
            except Exception:
                with self.lock:self._lock('safety_lock','Account monitoring unavailable; new entries blocked, exchange stops may remain active')
            self.stop_event.wait(2)

    def start(self):
        self.thread=threading.Thread(target=self.run,name='thoth-testnet-risk',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=30)
