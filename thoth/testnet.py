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

from .intrabar_exit import observe_exit
from .timeframes import interval_seconds,HOURLY_MINUTE_STRATEGIES
from .account_risk import new_risk, sample, closure, day, symbol_paused
from .bybit_testnet import TestnetClient, BybitError, dec, order_payload, stop_price
from .credentials import runtime_secret
from .execution import ExecutionJournal
from .diagnostics import failure_details, failure_summary
from .models import TestnetSettings, Settings
from .bybit_testnet import take_profit_price
from .trailing import FIELDS as TRAILING_FIELDS,new_trailing,advance as advance_trailing
from .portfolio_trailing import new_state as new_pnl_trailing,advance as advance_pnl_trailing


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
                self.save(dict(config=TestnetSettings().model_dump(),rsi_filters_off_v1=True,rsi_original_mode_v1=True,rsi_fixed_thresholds_v1=True,account_binding=None,
                    management=False,entries=False,status='disabled',reason=self.label+' has not been activated',
                    owned={},risk=new_risk(),equity=None,positions=[],events=[],cursors={},
                    reversals={},pending_signals={},last_monitor=None,closures=[],feed_health=None))
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS trade_history(link TEXT PRIMARY KEY,symbol TEXT NOT NULL,closed_at REAL NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS history_symbol_time ON trade_history(symbol,closed_at)')
        # Only S/R existed before strategy-tagged history was introduced.
        # Repair imported legacy rows without changing explicit strategy tags.
        with self.db() as db:
            for link,payload in db.execute("SELECT link,payload FROM trade_history WHERE link LIKE 'legacy:%'").fetchall():
                trade=json.loads(payload)
                if 'strategy' not in trade:
                    trade['strategy']='sr_breakout'
                    db.execute('UPDATE trade_history SET payload=? WHERE link=?',(json.dumps(trade),link))
        self.state=self.load()
        if not self.state.get('rsi_filters_off_v1'):
            # User-requested one-time opt-out, including previously saved filters.
            rsi={**TestnetSettings().rsi.model_dump(),**self.state['config'].get('rsi',{})}
            for key in ('volume_filter','volume_for_static','vwap_filter','sma_filter','histogram_filter'):rsi[key]=False
            self.state['config']['rsi']=rsi
            self.state['rsi_filters_off_v1']=True
            self.event('RSI filters switched off for the updated crossing rules; optional filters can be enabled in Settings')
        if not self.state.get('rsi_fixed_thresholds_v1'):
            rsi={**TestnetSettings().rsi.model_dump(),**self.state['config'].get('rsi',{})}
            self.state['rsi_fixed_previous_calculation']=dict(settings=dict(rsi),saved_at=self.clock())
            rsi.update(calculation_mode='fixed',fixed_long=56.68,fixed_short=42.38)
            self.state['config']['rsi']=rsi
            self.state['rsi_fixed_thresholds_v1']=True
            self.state['rsi_original_mode_v1']=True
            self.event('RSI fixed thresholds selected: Long 56.68 / Short 42.38; other RSI settings retained; entry permission remains manual')
        if not self.state.get('rsi_original_mode_v1'):
            # Explicit user choice: original mode once, without changing signal/style inputs.
            rsi={**TestnetSettings().rsi.model_dump(),**self.state['config'].get('rsi',{})}
            self.state['rsi_previous_calculation']=dict(settings=dict(rsi),saved_at=self.clock())
            rsi['calculation_mode']='uploaded_pine'
            self.state['config']['rsi']=rsi
            self.state['rsi_original_mode_v1']=True
            self.event('RSI switched to uploaded-original mode; Corrected ML remains selectable. Original ML thresholds are unavailable; appearance and crossing rules retained')
        for trade in self.state['closures']:
            link=trade.get('link') or 'legacy:'+hashlib.sha256(json.dumps(trade,sort_keys=True).encode()).hexdigest()
            self._record_trade(link,trade)
        # Persistence retains exit obligations, never permission to open new orders.
        self.state['rate_limit_resume']=False
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
        state.setdefault('blocked_symbols',{})
        state.setdefault('sma_exit_blocks',{})
        state.setdefault('rate_limit_resume',False)
        # First installation of selectable entry filters starts unfiltered.
        # Later explicitly saved selections survive reloads and restarts.
        if 'sma_7_50_filter_enabled' not in state['config']:
            state['config'].update(sma_enabled=False,sma_7_50_filter_enabled=False)
        state['config']={**TestnetSettings().model_dump(),**state['config']}
        for key,value in new_risk().items():state['risk'].setdefault(key,value)
        state.setdefault('daily_resume_intent',False)
        state.setdefault('flatten_reason',None)
        state.setdefault('pnl_trailing',new_pnl_trailing())
        return state

    def save(self,state=None):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO supervisor VALUES(1,?)',(json.dumps(state or self.state,allow_nan=False),))

    def event(self,message):
        if self.environment=='live':message=message.replace('Testnet','LIVE').replace('testnet','live')
        self.state['events']=(self.state['events']+[{'time':self.clock(),'message':message}])[-100:]

    def snapshot(self):
        s=self.load()
        return {k:s[k] for k in ('config','management','entries','status','reason','equity','positions','events','last_monitor','closures','feed_health')} | {
            'pnl_trailing':s['pnl_trailing'],
            'blocked_symbols':s.get('blocked_symbols',{}),'sma_exit_blocks':s.get('sma_exit_blocks',{}),'monitor_error':s.get('monitor_error'),
            'server_time':self.clock(),
            'closing':bool(s.get('flatten_reason')),'environment':self.environment,'enabled_on_server':self.enabled,'live_supported':self.environment=='live',
            'risk':{k:s['risk'][k] for k in ('day','baseline','baseline_ms','peak','peak_ms','last_sample','loss_allowance','loss_used','remaining','remaining_pct','stop_equity','loss_pct','streak','pause_until','symbols','daily_stop','baseline_required','portfolio_stop')},
            'execution': [{'symbol':r['payload']['symbol'],'phase':r['phase'],'reason':r['result'].get('reason','')} for r in self.journal.records()] if self.journal else []}

    def _record_trade(self,link,trade):
        allowed=('symbol','side','entry','qty','opened_at','closed_at','pnl','reason','strategy','entry_timeframe','exit_timeframe','protection_confirmed')
        record={key:trade[key] for key in allowed if key in trade}
        if link.startswith('legacy:') and 'strategy' not in record:record['strategy']='sr_breakout'
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO trade_history VALUES(?,?,?,?)',(link,record['symbol'],record['closed_at'],json.dumps(record)))

    def history(self,symbol=None,limit=50,offset=0):
        where=' WHERE symbol=?' if symbol else ''
        params=(symbol,) if symbol else ()
        with self.db() as db:
            count=db.execute('SELECT COUNT(*) FROM trade_history'+where,params).fetchone()[0]
            rows=db.execute('SELECT payload FROM trade_history'+where+' ORDER BY closed_at DESC,link LIMIT ? OFFSET ?',params+(limit,offset)).fetchall()
        return dict(items=[json.loads(row[0]) for row in rows],total=count,limit=limit,offset=offset)

    def analysis(self,symbol=None,strategy=None,time_basis='entry'):
        from .analytics import analyze
        with self.db() as db:
            records=[json.loads(row[0]) for row in db.execute('SELECT payload FROM trade_history'+(' WHERE symbol=?' if symbol else ''),(symbol,) if symbol else ())]
        if strategy:records=[r for r in records if r.get('strategy','unknown')==strategy]
        return analyze(records,time_basis)

    def configure(self,cfg):
        with self.lock:
            self.state=self.load()
            if self.state['entries']:raise ValueError('Pause '+self.label+' entries before editing settings')
            if any(s not in cfg.symbols for s in self.state['owned']):raise ValueError('Close managed positions before removing their symbols')
            if cfg.strategy!=self.state['config']['strategy'] or cfg.timeframe!=self.state['config']['timeframe']:
                if self.state['management'] and self.client is None:
                    raise ValueError('Recover account management before changing strategy or timeframe')
                if self.state['owned'] or (self.journal and any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records())):
                    raise ValueError('Close managed positions and reconcile pending orders before changing strategy or timeframe')
            if self.state['owned'] and cfg.margin_mode!=self.state['config']['margin_mode']:
                raise ValueError('Cannot change margin mode while positions are open')
            if not cfg.sma_exit_enabled:
                self.state['sma_exit_blocks']={}
                for owner in self.state['owned'].values():owner.pop('sma_exit_error',None)
            self.state['rate_limit_resume']=False
            pnl_keys=('pnl_trailing_enabled','pnl_trailing_activation_dollars','pnl_trailing_drop_dollars')
            if any(self.state['config'][key]!=getattr(cfg,key) for key in pnl_keys) and not self.state['pnl_trailing']['triggered']:
                self.state['pnl_trailing']=new_pnl_trailing()
                self.event('Open P&L trailing settings changed; a new trailing cycle starts at the next verified position sample')
            coin_keys=('coin_pnl_trailing_enabled','coin_pnl_trailing_activation_dollars','coin_pnl_trailing_drop_dollars')
            if any(self.state['config'].get(key)!=getattr(cfg,key) for key in coin_keys):
                for owner in self.state['owned'].values():
                    if not owner.get('coin_pnl_trailing',{}).get('triggered'):owner['coin_pnl_trailing']=new_pnl_trailing()
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
            raise ValueError('Trading requires a USDT-only account: non-USDT equity or borrowing detected')
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
        if not self.password_configured:raise ValueError('Configure dashboard authentication before '+self.label+' execution')
        cfg=TestnetSettings(**self.state['config'])
        if not cfg.symbols:raise ValueError('Select at least one symbol')
        if not cfg.dedicated_account:raise ValueError('A dedicated USDT-only '+self.label+' account must be acknowledged')
        if cfg.margin_mode not in ('isolated','cross'):raise ValueError('Confirm a margin mode')
        key,secret=self.credentials()
        if not key or not secret:raise ValueError(self.label+' credentials are not configured')
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
        if not uid:raise ValueError('Cannot bind '+self.label+' execution to an account')
        binding=hashlib.sha256(('bybit-'+self.environment+':'+str(uid)).encode()).hexdigest()
        if self.state['account_binding'] and self.state['account_binding']!=binding:
            raise ValueError('Credentials belong to a different '+self.label+' account')
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
            if confirmation.strip()!=self.confirmation:raise ValueError('Type '+self.confirmation+' to enable '+self.label+' trading')
            self.state['rate_limit_resume']=False
            if not recovery and self.state['config']['loss_streak_scope']=='account' and self.state['risk'].get('pause_until') and self.clock()<self.state['risk']['pause_until']:
                raise ValueError('Consecutive-loss pause cannot be bypassed')
            self.state.update(entries=False,status='paused',reason='Checking '+self.label+' account prerequisites')
            self.save()
            cfg=self._preflight()
            if not recovery and self.state['risk']['daily_stop'] and self.state['risk']['day']==day(self.clock()):
                sample(self.state['risk'],self.wallet(),self.transfers(),cfg,self.clock())
                self.save()
                unresolved=any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records())
                if self.positions() or self.state['owned'] or unresolved:
                    raise ValueError('Daily stop remains active: close and reconcile all managed positions and orders before manual review')
                if self.state['risk']['loss_used']>=self.state['risk']['loss_allowance']:
                    raise ValueError("Daily loss allowance is exhausted. Review the daily limit in Settings; increasing it increases the permitted daily risk. The starting equity and peak cannot be reset")
                self.state['risk']['daily_stop']=False
                self.event('Operator manually reviewed daily protection under the revised allowance; starting equity and peak preserved')
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
                raise ValueError(self.label+' monitoring unavailable; entries remain disabled') from None
            if not recovery and self.state['entries'] and self.state['status']=='running':
                for symbol in list(self.state['blocked_symbols']):
                    if symbol not in self.state['owned'] and not any(r['payload']['symbol']==symbol and r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records()):
                        del self.state['blocked_symbols'][symbol]
                        self.event(symbol+': symbol block cleared by explicit activation after settlement')
            self.event('Testnet risk management recovered' if recovery else 'Testnet entries enabled')
            self.save()
        return self.snapshot()

    def pause(self):
        with self.lock:
            self.state=self.load();self.state.update(entries=False,status='paused',reason='Entries paused; risk management remains active',pending_signals={},reversals={},daily_resume_intent=False,rate_limit_resume=False);self.save()
        return self.snapshot()

    def _lock(self,status,reason):
        if self.environment=='live':reason=reason.replace('Testnet','LIVE').replace('testnet','live')
        changed=self.state['status']!=status or self.state['reason']!=reason
        if status!='rate_limited':self.state['rate_limit_resume']=False
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
            close_link=None,close_attempt=0,reason=None,
            strategy=record.get('instrument',{}).get('thoth_strategy',self.state['config']['strategy']),
            entry_timeframe=record.get('instrument',{}).get('thoth_entry_timeframe',self.state['config']['timeframe']),
            exit_timeframe=record.get('instrument',{}).get('thoth_exit_timeframe','1' if self.state['config']['strategy'] in HOURLY_MINUTE_STRATEGIES else self.state['config']['timeframe']),trailing=new_trailing((record.get('instrument') or {}).get('thoth_trailing',{}),entry,filled,entry*filled/leverage,record['payload']['side']),protection_confirmed=record['phase']=='protected',
            take_profit=dict(enabled=bool((record.get('instrument') or {}).get('thoth_take_profit',{}).get('enabled',False)),dollars=(record.get('instrument') or {}).get('thoth_take_profit',{}).get('dollars'),verified=False),
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
        previous_pause=self.state['risk'].get('symbols',{}).get(symbol,{}).get('pause_until')
        closure(self.state['risk'],owner['link'],pnl,cfg,self.clock(),symbol)
        coin_pause=self.state['risk'].get('symbols',{}).get(symbol,{}).get('pause_until')
        if cfg.loss_streak_scope=='per_coin' and coin_pause and coin_pause!=previous_pause:
            self.event(symbol+': consecutive-loss cooldown started; new entries paused for '+str(cfg.pause_minutes)+' minutes, exits remain managed')
        trade=dict(link=owner['link'],symbol=symbol,side='Long' if owner['side']=='Buy' else 'Short',entry=float(owner['entry']),qty=float(owner['qty']),opened_at=owner['opened_ms']/1000,
            strategy=owner.get('strategy','sr_breakout'),entry_timeframe=owner.get('entry_timeframe'),exit_timeframe=owner.get('exit_timeframe'),pnl=pnl,closed_at=self.clock(),reason=owner['reason'] or 'Exchange stop/close',protection_confirmed=owner.get('protection_confirmed',False),source_fingerprint=owner.get('source_fingerprint'),execution_fingerprint=owner.get('execution_fingerprint'),strategy_fingerprint=owner.get('strategy_fingerprint'),real_exchange=owner.get('real_exchange',False))
        self._record_trade(owner['link'],trade)
        self.state['closures']=(self.state['closures']+[trade])[-200:]
        # Mark entry terminal so startup does not treat old fills as pending ownership.
        self.journal.update(owner['link'],'closed',{'reason':'Position flat and realized P&L reconciled'})
        del self.state['owned'][symbol]
        self.event(symbol+': position flat; realized P&L reconciled')
        self.save()
        return True

    def _protection_failure(self,symbol,record,reason):
        details=record['result'].get('protection_error',{})
        message=reason+(' · '+failure_summary(details) if details else '')
        if symbol not in self.state['blocked_symbols']:
            self.event(symbol+': '+message+'; symbol blocked, closing its exposure')
        self.state['blocked_symbols'][symbol]=message
        self.state['pending_signals'].pop(symbol,None)
        self.state['reversals'].pop(symbol,None)
        self.save()

    def _monitor_failure(self,exc):
        details=failure_details(exc,'account_monitor')
        self.state['monitor_error']=details
        if isinstance(exc,BybitError) and exc.code==10006 and getattr(exc,'thoth_method',None)=='GET' and self.state['status'] in ('running','cooldown','paused','rate_limited'):
            if self.state['status']!='rate_limited':self.state['rate_limit_resume']=bool(self.state['entries'])
            self._lock('rate_limited','Bybit account read rate-limited; entries waiting for verified recovery · '+failure_summary(details))
        else:
            self._lock('safety_lock','Account monitoring unavailable; new entries blocked · '+failure_summary(details))

    def _manage_trailing(self,symbol,owner,p,record,base_stop):
        trailing=owner.get('trailing',{})
        if not trailing.get('enabled'):return
        previous_active=trailing.get('active',False)
        updated,target=advance_trailing(trailing,owner['side'],owner['entry'],owner['qty'],owner['margin'],p.get('markPrice'),base_stop,record['instrument']['priceFilter']['tickSize'])
        owner['trailing']=updated
        if not previous_active and updated.get('active'):self.event(symbol+': trailing activated at '+str(updated['activation_pct'])+('% price gain' if updated.get('activation_basis')=='price' else '% initial-margin P&L'))
        self.save()  # Keep activation and favorable peak even before any stop write.
        if target is None:return
        mark=dec(p['markPrice'])
        if (owner['side']=='Buy' and mark<=dec(target)) or (owner['side']=='Sell' and mark>=dec(target)):
            self._close(symbol,p,'Trailing stop retracement');return
        if dec(target)==dec(p.get('stopLoss','0')):return
        updated['pending_stop']=target
        self.save()  # Durable intent: recover an acknowledged or ambiguous stop update.
        failure=None
        try:
            self.client.request('POST','/v5/position/trading-stop',dict(category='linear',symbol=symbol,tpslMode='Full',positionIdx=0,stopLoss=target,slTriggerBy='MarkPrice'),private=True)
        except Exception as exc:failure=exc
        # Readback is mandatory even on not-modified or transport timeout responses.
        rows=self.client.request('GET','/v5/position/list',dict(category='linear',symbol=symbol),private=True)['list']
        active=[row for row in rows if dec(row.get('size','0'))>0]
        if not active:return  # Stop may have filled; the next monitor settles the trade.
        if len(active)!=1 or active[0].get('positionIdx')!=0 or active[0].get('side')!=owner['side'] or dec(active[0]['size'])!=dec(p['size']):
            self._lock('safety_lock','Position ownership changed during trailing update');return
        if dec(active[0].get('stopLoss','0'))!=dec(target):
            details=failure_details(failure or ValueError(),'trailing_protection')
            self._protection_failure(symbol,{'result':{'protection_error':details}},'Trailing stop update unconfirmed')
            self._close(symbol,active[0],'Trailing stop verification failure');return
        updated.update(stop=target,pending_stop=None)
        p['stopLoss']=target
        self.save()
        self.event(symbol+': trailing stop verified on Bybit at '+target)

    def _manage_take_profit(self,symbol,owner,p,record):
        state=owner.get('take_profit',{})
        if not state.get('enabled') or owner['close_link']:return
        try:
            target=take_profit_price('Long' if owner['side']=='Buy' else 'Short',owner['entry'],owner['qty'],state['dollars'],record['instrument']['priceFilter']['tickSize'])
            state['price']=target
            # Exchange TP is primary; sampled gross P&L is an additional fallback.
            if dec(p.get('unrealisedPnl','0'))>=dec(state['dollars']):
                self._close(symbol,p,'Take profit (USDT target)');return
            if dec(p.get('takeProfit') or '0')==dec(target):
                if not state.get('verified'):self.event(symbol+': Bybit take profit verified at '+target+' ('+str(state['dollars'])+' USDT gross target)')
                state.update(verified=True,error=None,pending=False)
                return
            if state.get('verified'):self.event(symbol+': take profit missing or changed; restoring target, existing stop remains managed')
            state['verified']=False
            if self.clock()<state.get('retry_after',0):return
            state.update(pending=True,retry_after=self.clock()+10)
            self.save()  # Durable intent before a potentially ambiguous configuration write.
            self.client.request('POST','/v5/position/trading-stop',dict(category='linear',symbol=symbol,tpslMode='Full',positionIdx=0,takeProfit=target,tpTriggerBy='MarkPrice',tpOrderType='Market'),private=True)
            # The next fresh monitor position read verifies it, not the POST acknowledgement.
            self.save()
        except Exception as exc:
            details=failure_details(exc,'take_profit')
            if state.get('error')!=details:self.event(symbol+': take profit not yet verified; retrying, stop and other coins remain managed · '+failure_summary(details))
            state.update(verified=False,error=details,retry_after=self.clock()+10)
            self.save()

    def _manage_sma_exit(self,symbol,owner,p,cfg):
        if (not cfg.sma_exit_enabled and owner.get('strategy') not in HOURLY_MINUTE_STRATEGIES) or owner['close_link']:return
        tracker=owner.setdefault('sma_exit',{})
        if self.clock()<tracker.get('retry_after',0) or self.clock()-tracker.get('checked_at',0)<5:return
        timeframe='1' if owner.get('strategy') in HOURLY_MINUTE_STRATEGIES else cfg.timeframe
        rows=self.client.candles(symbol,timeframe,limit=51)
        reason=observe_exit(tracker,rows,timeframe,p['side'],self.clock())
        if tracker.get('waiting_for_candle') is not None:
            self.save();return
        previous_error=owner.pop('sma_exit_error',None)
        previous_block=self.state['sma_exit_blocks'].pop(symbol,None)
        if previous_error or previous_block:self.event(symbol+': SMA exit observations recovered; coin data block cleared')
        tracker.pop('retry_after',None)
        self.save()
        if reason:
            # A durable exit obligation, independent of permission to enter.
            owner['signal_exit']=reason
            self.state['reversals'].pop(symbol,None)
            self.state['pending_signals'].pop(symbol,None)
            self.save()
            self._close(symbol,p,reason)

    def _manage_coin_pnl_trailing(self,active,cfg):
        for symbol,p in active.items():
            owner=self.state['owned'][symbol]
            if owner.get('close_link') or owner.get('signal_exit'):continue
            before=owner.get('coin_pnl_trailing',new_pnl_trailing())
            updated=advance_pnl_trailing(before,p['unrealisedPnl'],True,cfg.coin_pnl_trailing_enabled,
                cfg.coin_pnl_trailing_activation_dollars,cfg.coin_pnl_trailing_drop_dollars,self.clock())
            owner['coin_pnl_trailing']=updated
            if updated['active'] and not before['active']:self.event(symbol+': per-coin P&L trailing activated')
            if updated['triggered']:
                # Durable symbol-only close obligation; retries use the normal close reconciler.
                owner['signal_exit']='Per-coin Trailing Stop'
                self.state['pending_signals'].pop(symbol,None)
                self.state['reversals'].pop(symbol,None)
                self.event(symbol+': per-coin trailing drop reached; closing this position; entry permission retained')
            self.save()

    def _manage_pnl_trailing(self,active,cfg):
        before=self.state['pnl_trailing']
        # positions() is a fresh verified, ownership-checked account read.
        # Include winners AND losers exactly as the Open LIVE P&L card does.
        pnl=sum((dec(p['unrealisedPnl']) for p in active.values()),dec(0))
        updated=advance_pnl_trailing(before,pnl,bool(active),cfg.pnl_trailing_enabled,
            cfg.pnl_trailing_activation_dollars,cfg.pnl_trailing_drop_dollars,self.clock())
        self.state['pnl_trailing']=updated
        if updated['active'] and not before['active']:
            self.event('Open P&L trailing activated at '+updated['current']+' USDT')
        if updated['triggered'] and not before['triggered']:
            # Persist the close-all obligation BEFORE cancellation/close requests.
            # Existing flatten reconciliation handles late entry fills and retries.
            self.state['flatten_reason']='Open P&L trailing stop'
            self.state.update(pending_signals={},reversals={})
            self.event('Open P&L trailing drop reached at '+updated['current']+' USDT (peak '+updated['peak']+', close threshold '+updated['threshold']+'); closing all positions; entry permission retained')
            self.save()
        observer=getattr(self,'trailing_observer',None)
        if observer is not None:
            try:observer.submit(active,cfg,updated,self.state['owned'],self.clock())
            except Exception:pass  # Observation must never affect execution or its fixed tracker.

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
                        self._protection_failure(symbol,record,'Protective stop failed')
                        self._close(symbol,p,'Protective stop failure')
                    elif record['phase']=='protected' and not owner['close_link']:
                        desired=stop_price('Long' if p['side']=='Buy' else 'Short',p['avgPrice'],p['size'],owner['stop_dollars'],record['instrument']['priceFilter']['tickSize'])
                        trailing=owner.get('trailing',{})
                        pending=trailing.get('pending_stop')
                        if pending and dec(p.get('stopLoss','0'))==dec(pending):
                            trailing.update(stop=pending,pending_stop=None)
                            self.event(symbol+': pending trailing stop verified after reconnect')
                            self.save()
                        expected=trailing.get('stop') or desired
                        if dec(p.get('stopLoss','0'))!=dec(expected):
                            self._protection_failure(symbol,record,'Protective stop changed or missing')
                            self._close(symbol,p,'Protective stop missing')
                        elif not owner.get('protection_announced'):
                            owner.update(protection_confirmed=True,protection_announced=True)
                            self.event(symbol+': position filled; Bybit stop loss verified')
                            self.save()
                        if not owner['close_link'] and dec(p.get('stopLoss','0'))==dec(expected):
                            self._manage_trailing(symbol,owner,p,record,desired)
                            self._manage_take_profit(symbol,owner,p,record)
            for symbol,owner in list(self.state['owned'].items()):
                p=active.get(symbol)
                if p:
                    try:self._manage_sma_exit(symbol,owner,p,cfg)
                    except Exception as exc:
                        # Still manage every other exit and account risk if candle reads fail.
                        details=failure_details(exc,'sma_exit')
                        owner['sma_exit_error']=details
                        retry_after=self.clock()+30
                        owner.setdefault('sma_exit',{})['retry_after']=retry_after
                        prior=self.state['sma_exit_blocks'].get(symbol,{}).get('details')
                        self.state['sma_exit_blocks'][symbol]=dict(details=details,retry_after=retry_after)
                        if prior!=details:self.event(symbol+': SMA exit monitoring unavailable; only this coin’s new entries blocked · '+failure_summary(details))
                        self.save()

            self._manage_coin_pnl_trailing(active,cfg)
            # Exit obligations run before wallet/transfer reads, which can fail independently.
            for symbol,owner in list(self.state['owned'].items()):
                p=active.get(symbol)
                if p and (owner.get('signal_exit') or owner['close_link']):
                    self._close(symbol,p,owner.get('signal_exit') or owner['reason'] or 'Pending close')
            equity=self.wallet()
            previous_day=self.state['risk']['day']
            risk=sample(self.state['risk'],equity,self.transfers(),cfg,self.clock())
            if self.state['risk']['day']!=previous_day:
                self.event('New UTC daily period '+self.state['risk']['day']+': starting equity '+format(equity,'.2f')+' USDT; fresh allowance '+format(self.state['risk']['loss_allowance'],'.2f')+' USDT; prior daily loss usage cleared')
            self.state.update(equity=equity,last_monitor=self.clock(),positions=[dict(symbol=s,side='Long' if p['side']=='Buy' else 'Short',qty=p['size'],entry=p['avgPrice'],mark=p.get('markPrice'),pnl=float(p.get('unrealisedPnl','0')),stop=p.get('stopLoss'),stop_dollars=self.state['owned'].get(s,{}).get('stop_dollars'),take_profit={**self.state['owned'].get(s,{}).get('take_profit',{'enabled':False}),'exchange_price':p.get('takeProfit')},coin_pnl_trailing=self.state['owned'].get(s,{}).get('coin_pnl_trailing',new_pnl_trailing()),trailing=self.state['owned'].get(s,{}).get('trailing',{'enabled':False}),opened_at=self.state['owned'].get(s,{}).get('opened_ms')) for s,p in active.items()])
            self._manage_pnl_trailing(active,cfg)
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
                    trailing_complete=self.state['flatten_reason']=='Open P&L trailing stop'
                    self.state['flatten_reason']=None
                    if trailing_complete:
                        self.state['pnl_trailing']=new_pnl_trailing()
                        self.event('Open P&L trailing close-all settled; new trailing cycle ready; '+('entries remain enabled subject to risk checks' if self.state['entries'] else 'entries remain paused by the operator or another risk control'))
            pending=self.journal.records()
            if any(r['phase']=='unknown' for r in pending):
                self._lock('safety_lock','Order outcome is unknown; reconcile before new entries')
            if self.state['status'] in ('rate_limited','cooldown') and risk in ('ok','cooldown'):
                resume=self.state['rate_limit_resume']
                if self.state['status']=='rate_limited' or resume:
                    self.state.update(entries=resume,rate_limit_resume=False,monitor_error=None)
                    if self.state['status']=='rate_limited':self.state.update(status='running' if resume else 'paused',reason='' if resume else 'Account reads recovered; entries remain paused')
                    self.event('Bybit account monitoring recovered; '+('previously enabled entries resumed subject to risk checks' if resume else 'entries remain paused'))
            self._reversals(cfg)
            self._drain(cfg)
            self.save()

    def close_all(self):
        with self.lock:
            self.state=self.load()
            if not self.client or not self.state['management']:raise ValueError('Activate '+self.label+' risk management first')
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
            if not self.client:raise ValueError('Connect '+self.label+' management first')
            if self.state['risk']['day']==day(self.clock()) and self.state['risk']['daily_stop']:
                raise ValueError('Today\'s daily loss lock cannot be reset')
            if self.positions() or self.state['owned']:raise ValueError('Account must be flat and all results reconciled')
            if any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records()):raise ValueError('Resolve all orders before setting a baseline')
            sample(self.state['risk'],self.wallet(),0,TestnetSettings(**self.state['config']),self.clock(),initial=True)
            self.state.update(status='paused',entries=False,reason='New daily baseline established; manually enable entries')
            self.save()
        return self.snapshot()

    def _reversal_block_reason(self,symbol):
        reasons=[]
        if symbol_paused(self.state['risk'],symbol,TestnetSettings(**self.state['config']),self.clock()):
            reasons.append('per-coin consecutive-loss cooldown')
        if symbol in self.state['blocked_symbols']:
            reasons.append('symbol blocked: '+self.state['blocked_symbols'][symbol])
        if self.state['status']!='running':
            reasons.append(self.state['status'].replace('_',' ')+(': '+self.state['reason'] if self.state['reason'] else ''))
        if not self.state['entries']:reasons.append('new entries disabled')
        return '; '.join(reasons)

    def apply_signals(self,cfg,observations):
        with self.lock:
            if not self.client or not self.state['management']:return
            current=TestnetSettings(**self.state['config'])
            if cfg!=Settings(**current.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump():return
            try:self.monitor()
            except Exception as exc:self._monitor_failure(exc)
            identity=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()[:16]
            for symbol,observation in observations.items():
                key=identity+':'+symbol;stamp=observation['time'];previous=self.state['cursors'].get(key)
                if previous is not None and stamp<=previous:continue
                self.state['cursors'][key]=stamp
                self.save()  # Never replay this closed candle after a crash.
                if self.state['flatten_reason']:continue  # Consume signals while close-all settles; do not queue replays.
                if previous is None or observation.get('signal') not in ('Long','Short'):continue
                if not 0<=self.clock()-stamp<=(30 if current.strategy in HOURLY_MINUTE_STRATEGIES else 2*interval_seconds(current.timeframe)):continue
                side='Buy' if observation['signal']=='Long' else 'Sell'
                owner=self.state['owned'].get(symbol)
                if owner:
                    if owner['side']==side:continue
                    reason='Opposite RSI signal' if current.strategy=='rsi_hourly_intrabar' else 'Opposite combined S/R or SMA 7/50 signal' if current.strategy=='sr_sma_7_50' else 'Opposite SMA crossover' if current.strategy in ('sma_cross','sma_7_50') else 'Opposite breakout'
                    owner['signal_exit']=reason
                    if current.opposite_action=='reverse' and self.state['entries'] and self.state['status']=='running' and symbol not in self.state['blocked_symbols'] and not symbol_paused(self.state['risk'],symbol,current,self.clock()):
                        self.state['reversals'][symbol]=dict(side=observation['signal'],time=stamp,identity=identity)
                        self.event(symbol+': reverse '+observation['signal']+' queued; waiting for current position to close and settle')
                    elif current.opposite_action=='reverse':
                        self.event(symbol+': opposite-signal exit requested; replacement entry blocked: '+self._reversal_block_reason(symbol))
                    self.save()  # Retain exit obligation if a position read fails.
                    try:
                        p=self.positions().get(symbol)
                        if p:self._close(symbol,p,reason)
                    except Exception as exc:self._monitor_failure(exc)
                    self.save();continue
                if self.state['entries'] and self.state['status']=='running' and symbol not in self.state['blocked_symbols'] and not symbol_paused(self.state['risk'],symbol,current,self.clock()):
                    self.state['pending_signals'][symbol]=dict(side=observation['signal'],time=stamp,identity=identity)
            self._reversals(current)
            self._drain(current)
            self.save()

    def _sma_entry_ready(self,symbol,cfg):
        block=self.state['sma_exit_blocks'].get(symbol)
        if not block or not cfg.sma_exit_enabled:return True
        if self.clock()<block['retry_after']:return False
        try:
            tracker={}
            timeframe='1' if cfg.strategy in HOURLY_MINUTE_STRATEGIES else cfg.timeframe
            observe_exit(tracker,self.client.candles(symbol,timeframe,limit=51),timeframe,'Buy',self.clock())
            if tracker.get('waiting_for_candle') is not None:return False
        except Exception as exc:
            block.update(details=failure_details(exc,'sma_exit'),retry_after=self.clock()+30)
            self.save();return False
        del self.state['sma_exit_blocks'][symbol]
        self.event(symbol+': fresh SMA exit data verified; coin entry block cleared')
        self.save();return True

    def _entry(self,symbol,side,stamp,identity,cfg):
        if symbol_paused(self.state['risk'],symbol,cfg,self.clock()):return
        if symbol in self.state['blocked_symbols']:return
        if self.state['flatten_reason'] or self.state['status']!='running' or not self.state['entries']:return
        if len(self.state['owned'])>=cfg.max_positions:return
        if not self._sma_entry_ready(symbol,cfg):return
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
            if cfg.trailing_enabled:
                reference=price if cfg.trailing_distance_basis=='price' else price/cfg.leverage
                if reference*dec(cfg.trailing_distance_pct)/100<dec(instrument['priceFilter']['tickSize']):
                    raise ValueError('Trailing distance is smaller than one price tick')
            initial_stop=stop_price(side,price,payload['qty'],cfg.stop_dollars,instrument['priceFilter']['tickSize'])
            payload.update(stopLoss=initial_stop,slTriggerBy='MarkPrice',tpslMode='Full')
            if cfg.take_profit_enabled:payload.update(takeProfit=take_profit_price(side,price,payload['qty'],cfg.take_profit_dollars,instrument['priceFilter']['tickSize']),tpTriggerBy='MarkPrice',tpOrderType='Market')
            try:self.client.request('POST','/v5/position/set-leverage',dict(category='linear',symbol=symbol,buyLeverage=str(cfg.leverage),sellLeverage=str(cfg.leverage)),private=True)
            except BybitError as exc:
                if exc.code!=110043:raise
            metadata={**instrument,'thoth_take_profit':{'enabled':cfg.take_profit_enabled,'dollars':cfg.take_profit_dollars},'thoth_strategy':cfg.strategy,'thoth_entry_timeframe':cfg.timeframe,'thoth_exit_timeframe':'1' if cfg.strategy in HOURLY_MINUTE_STRATEGIES else cfg.timeframe,'thoth_trailing':{key:getattr(cfg,key) for key in TRAILING_FIELDS}}
            self.journal.submit(payload,cfg.stop_dollars,metadata)
            self.event(symbol+(': RSI intrabar signal' if cfg.strategy=='rsi_hourly_intrabar' else ': SMA 7/50 crossover' if cfg.strategy=='sma_7_50' else ': S/R + SMA 7/50 signal' if cfg.strategy=='sr_sma_7_50' else ': SMA 7/200 crossover' if cfg.strategy=='sma_cross' else ': intrabar S/R breakout' if cfg.strategy in HOURLY_MINUTE_STRATEGIES else ': S/R breakout')+' order submitted; awaiting fill/protection')
        except ValueError as exc:self.event(symbol+': entry skipped: '+str(exc))
        except Exception:self._lock('safety_lock','Testnet entry operation failed; reconcile before enabling entries')
        self.save()

    def _drain(self,cfg):
        for symbol,intent in list(self.state['pending_signals'].items()):
            if symbol_paused(self.state['risk'],symbol,cfg,self.clock()):
                del self.state['pending_signals'][symbol];continue
            if self.clock()-intent['time']>(30 if cfg.strategy in HOURLY_MINUTE_STRATEGIES else 2*interval_seconds(cfg.timeframe)):
                del self.state['pending_signals'][symbol];continue
            if self.state['flatten_reason'] or self.state['status']!='running' or not self.state['entries'] or len(self.state['owned'])>=cfg.max_positions:return
            if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
            del self.state['pending_signals'][symbol]
            self._entry(symbol,intent['side'],intent['time'],intent['identity'],cfg)
            return

    def _reversals(self,cfg):
        for symbol,reversal in list(self.state['reversals'].items()):
            if symbol_paused(self.state['risk'],symbol,cfg,self.clock()):
                del self.state['reversals'][symbol]
                self.event(symbol+": replacement entry blocked by this coin’s consecutive-loss cooldown")
                continue
            if self.clock()-reversal['time']>(30 if cfg.strategy in HOURLY_MINUTE_STRATEGIES else 2*interval_seconds(cfg.timeframe)):
                del self.state['reversals'][symbol]
                self.event(symbol+': reverse signal expired; no replacement position opened')
                continue
            if symbol in self.state['owned']:continue
            if self.state['flatten_reason'] or self.state['status']!='running' or not self.state['entries']:continue
            if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
            if len(self.state['owned'])>=cfg.max_positions:continue
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
            except Exception as exc:
                with self.lock:self._monitor_failure(exc)
            self.stop_event.wait(2)

    def start(self):
        self.thread=threading.Thread(target=self.run,name='thoth-testnet-risk',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=30)
