"""Guarded testnet orchestration. Production exchange orders are not supported.

The dashboard must explicitly activate testnet orders; credentials, a password,
a dedicated account acknowledgement and a server-side feature flag are required.
After a restart only risk management resumes; new entries need manual activation.
"""

from .crypto_ob_strength import THRESHOLDS as CRYPTO_OB_THRESHOLDS,STOP_DOLLARS as CRYPTO_OB_STOP
from . import trailing_diagnostics
from .breakeven import advance as advance_breakeven
from .database import connect
from .staged_entry import STAGED_STRATEGIES,ORDER_BLOCK_STRATEGY,HOUR_GATE,TWO_DIRECTION,CLOSED_TWO_DIRECTION,TWO_DIRECTION_STRATEGIES,DELAYED_30M,DELAYED_STRATEGIES,RSI_STRATEGY
from copy import deepcopy
from .entry_schedule import reason as schedule_reason,status as schedule_status,daily_exit_period
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.request import urlopen
from .verification import source_fingerprint,strategy_fingerprint,execution_fingerprint

from .intrabar_exit import observe_exit
from .timeframes import interval_seconds,HOURLY_MINUTE_STRATEGIES,SHORT_LIVED_SIGNAL_STRATEGIES
from .account_risk import new_risk, sample, closure, day, symbol_paused
from .bybit_testnet import TestnetClient, BybitError, AmbiguousOrder, dec, order_payload, stop_price
from .credentials import runtime_secret
from .execution import ExecutionJournal
from .diagnostics import failure_details, failure_summary
from .models import TestnetSettings, Settings
from .bybit_testnet import take_profit_price
from .trailing import FIELDS as TRAILING_FIELDS,new_trailing,advance as advance_trailing
from .equity_trailing import PERIODS as EQUITY_PERIODS,LABELS as EQUITY_LABELS,advance as advance_equity_trailing,active_locks as equity_locks,window as equity_window
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
        self.ob_exit_session=uuid.uuid4().hex
        self.ob_exit_source=source_fingerprint()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS supervisor(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS entry_pauses(id INTEGER PRIMARY KEY AUTOINCREMENT,time REAL NOT NULL,payload TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM supervisor').fetchone():
                self.save(dict(config=TestnetSettings().model_dump(),rsi_filters_off_v1=True,rsi_original_mode_v1=True,rsi_fixed_thresholds_v1=True,account_binding=None,
                    management=False,entries=False,status='disabled',reason=self.label+' has not been activated',
                    owned={},risk=new_risk(),equity=None,positions=[],events=[],cursors={},
                    reversals={},pending_signals={},last_monitor=None,closures=[],feed_health=None))
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS trade_history(link TEXT PRIMARY KEY,symbol TEXT NOT NULL,closed_at REAL NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS history_symbol_time ON trade_history(symbol,closed_at)')
            db.execute('CREATE TABLE IF NOT EXISTS signal_decisions(id INTEGER PRIMARY KEY,time REAL,symbol TEXT,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS scan_checks(id INTEGER PRIMARY KEY,time REAL,symbol TEXT,payload TEXT)')
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
        self.state['equity_trailing_resume_intent']=False
        self.state['pending_signals']={}
        self.state['reversals']={}
        self.state['delayed_30m']={}
        if self.state['management']:
            self.state.update(status='recovery_required',reason='Recover managed positions before enabling new entries')
        self.save()

    def db(self):return connect(self.path,timeout=15)

    def load(self):
        with self.db() as db:state=json.loads(db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()[0])
        state.setdefault('pending_signals',{})
        state.setdefault('delayed_30m',{})
        state.setdefault('entry_gates',{})
        state.setdefault('blocked_symbols',{})
        state.setdefault('sma_exit_blocks',{})
        state.setdefault('order_block_exit_blocks',{})
        state.setdefault('rate_limit_resume',False)
        # First installation of selectable entry filters starts unfiltered.
        # Later explicitly saved selections survive reloads and restarts.
        if 'sma_7_50_filter_enabled' not in state['config']:
            state['config'].update(sma_enabled=False,sma_7_50_filter_enabled=False)
        if 'saved_symbol_lists' not in state['config']:
            from .tradfi import default_symbol_lists
            state['config']['saved_symbol_lists']={**default_symbol_lists(),'main':list(state['config']['symbols'])}
            state['config']['active_symbol_list']='main'
        state['config']={**TestnetSettings().model_dump(),**state['config']}
        from .tradfi import clean_selections,EXCLUDED_SYMBOLS
        from .tradfi import default_symbol_lists
        state['config']['saved_symbol_lists']={**default_symbol_lists(),**state['config']['saved_symbol_lists']}
        clean_selections(state['config'])
        for name in ('pending_signals','reversals','entry_gates','delayed_30m'):
            for symbol in EXCLUDED_SYMBOLS:state.get(name,{}).pop(symbol,None)
        for key,value in new_risk().items():state['risk'].setdefault(key,value)
        state.setdefault('daily_resume_intent',False)
        state.setdefault('flatten_reason',None)
        state.setdefault('schedule_exit',dict(active=False))
        state.setdefault('pnl_trailing',new_pnl_trailing())
        state.setdefault('equity_trailing',{})
        state.setdefault('equity_cashflows',{})
        state.setdefault('equity_trailing_resume_intent',False)
        return state

    def save(self,state=None):
        current=state or self.state
        with self.db() as db:
            previous=db.execute('SELECT payload FROM supervisor WHERE id=1').fetchone()
            if self.environment=='live' and previous and json.loads(previous[0]).get('entries') and not current['entries']:
                # Persist notification intent atomically with the pause. No network on this path.
                reason=('Server restarted; new entries require manual activation' if current['status']=='recovery_required'
                    else current.get('reason') or current.get('flatten_reason') or current['status'])
                db.execute('INSERT INTO entry_pauses(time,payload) VALUES(?,?)',
                    (self.clock(),json.dumps(dict(title='THOTH: new entries paused',body=str(reason)[:400],paused_at=self.clock()))))
                db.execute('DELETE FROM entry_pauses WHERE id <= (SELECT COALESCE(MAX(id),0)-1000 FROM entry_pauses)')
            db.execute('INSERT OR REPLACE INTO supervisor VALUES(1,?)',(json.dumps(current,allow_nan=False),))

    def event(self,message):
        if self.environment=='live':message=message.replace('Testnet','LIVE').replace('testnet','live')
        self.state['events']=(self.state['events']+[{'time':self.clock(),'message':message}])[-100:]

    def snapshot(self):
        s=self.load()
        return {k:s[k] for k in ('config','management','entries','status','reason','equity','positions','events','last_monitor','closures','feed_health')} | {
            'pnl_trailing':s['pnl_trailing'],'equity_trailing':s['equity_trailing'],'entry_gates':s.get('entry_gates',{}),'delayed_30m':s.get('delayed_30m',{}),
            'blocked_symbols':s.get('blocked_symbols',{}),'sma_exit_blocks':s.get('sma_exit_blocks',{}),'order_block_exit_blocks':s.get('order_block_exit_blocks',{}),'monitor_error':s.get('monitor_error'),
            'monitor_failures':s.get('monitor_failures',[]),
            'entry_schedule':self._schedule_status(s),
            'exit_rules':{'closed_1m_sma7_14_cross':s['config']['strategy']=='volume_delta_1h_windows_sma7_14_exit','closed_1m_sma7':s['config']['strategy']=='volume_delta_1h_midpoint_sma7_exit','close_only':True},
            'entry_filters':{'crypto_ob_strength':{'enabled':s['config']['strategy']=='crypto_ob_strength_1h_close','thresholds':dict(CRYPTO_OB_THRESHOLDS),'timeframe':'60','stop_dollars':CRYPTO_OB_STOP,'formations_only':True},'major_pullback_intrabar':{'enabled':s['config']['strategy']=='major_pullback_30m_intrabar','source_timeframe':'30','confirmation_timeframe':'15','sma200':False,'max_distance_atr':.25,'entry_buffer_bps':2,'fixed_entry_windows':False},'trend_breakout':{'enabled':s['config']['strategy']=='trend_major_breakout_30m_close','signal_timeframe':'30','max_distance_atr':.25,'entry_buffer_bps':2,'risk_mode':'saved_account_settings'},'trend_pullback':{'enabled':s['config']['strategy']=='trend_major_pullback_30m_close','signal_timeframe':'30','max_distance_atr':.25,'entry_buffer_bps':2,'risk_mode':'saved_account_settings'},'sma200':{'enabled':s['config']['strategy'] in ('order_block_sr_1h_early','trend_major_pullback_30m_close','trend_major_breakout_30m_close'),'period':200,'timeframe':'60','basis':'completed hourly candles','equality':'block','insufficient_data':'block'},'volume_delta':{'enabled':s['config']['strategy'] in ('volume_delta_intrabar','volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'),'sma7_50':s['config']['volume_delta']['sma_filter'],'bull_type':s['config']['volume_delta']['bull_type'],'bear_type':s['config']['volume_delta']['bear_type'],'bullish':s['config']['volume_delta']['bullish'],'bearish':s['config']['volume_delta']['bearish']}},
            'last_portfolio_stop':s.get('last_portfolio_stop'),
            'hourly_entry_slot':dict(enabled=s['config']['strategy']=='ob_sr_minute_hourly',
                used_count=len(self._minute_hour_symbols(s)) if s['config']['strategy']=='ob_sr_minute_hourly' else 0,
                limit=s['config']['max_positions'],entry_window_minutes=s['config'].get('minute_entry_window_minutes',5),
                entry_window_open=self.clock()%3600<s['config'].get('minute_entry_window_minutes',5)*60,
                window_ends_at=int(self.clock())//3600*3600+s['config'].get('minute_entry_window_minutes',5)*60,
                resets_at=(int(self.clock())//3600+1)*3600),
            'server_time':self.clock(),
            'closing':bool(s.get('flatten_reason') or s.get('schedule_exit',{}).get('active')),'environment':self.environment,'enabled_on_server':self.enabled,'live_supported':self.environment=='live',
            'risk':{k:s['risk'][k] for k in ('day','baseline','baseline_ms','peak','peak_ms','last_sample','loss_allowance','loss_used','remaining','remaining_pct','stop_equity','loss_pct','streak','pause_until','symbols','daily_stop','baseline_required','portfolio_stop')},
            'execution': [{'symbol':r['payload']['symbol'],'phase':r['phase'],'reason':r['result'].get('reason',''),'protection_error':r['result'].get('protection_error')} for r in self.journal.records()] if self.journal else []}

    def _record_trade(self,link,trade):
        allowed=('symbol','side','entry','qty','opened_at','closed_at','pnl','reason','strategy','entry_timeframe','exit_timeframe','protection_confirmed','entry_sources','signal_stretch','trailing_diagnostics','coin_breakeven','crypto_ob_strength','price_sma_cross','sr_unfiltered','trend_pullback','trend_breakout','major_pullback_intrabar')
        record={key:trade[key] for key in allowed if key in trade}
        if link.startswith('legacy:') and 'strategy' not in record:record['strategy']='sr_breakout'
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO trade_history VALUES(?,?,?,?)',(link,record['symbol'],record['closed_at'],json.dumps(record)))

    def history(self,symbol=None,limit=50,offset=0):
        where=' WHERE symbol=?' if symbol else ''
        params=(symbol,) if symbol else ()
        with self.db() as db:
            count=db.execute('SELECT COUNT(*) FROM trade_history'+where,params).fetchone()[0]
            rows=db.execute('SELECT link,payload FROM trade_history'+where+' ORDER BY closed_at DESC,link LIMIT ? OFFSET ?',params+(limit,offset)).fetchall()
            from .signal_sources import trade_records
            records=trade_records(db,rows)
        return dict(items=records,total=count,limit=limit,offset=offset)

    def analysis(self,symbol=None,strategy=None,time_basis='entry',source=None):
        from .analytics import analyze
        with self.db() as db:
            from .signal_sources import trade_records
            records=trade_records(db,db.execute('SELECT link,payload FROM trade_history'+(' WHERE symbol=?' if symbol else ''),(symbol,) if symbol else ()).fetchall())
        if strategy:records=[r for r in records if r.get('strategy','unknown')==strategy]
        return analyze(records,time_basis,source)

    def configure(self,cfg):
        from .crypto_ob_strength import apply_exit_preset
        cfg=apply_exit_preset(cfg)
        with self.lock:
            self.state=self.load()
            if self.state['entries']:raise ValueError('Pause '+self.label+' entries before editing settings')
            from .tradfi import EXCLUDED_SYMBOLS
            if any(s not in cfg.symbols and s not in EXCLUDED_SYMBOLS for s in self.state['owned']):raise ValueError('Close managed positions before removing their symbols')
            if cfg.strategy!=self.state['config']['strategy'] or cfg.timeframe!=self.state['config']['timeframe']:
                if self.state['management'] and self.client is None:
                    raise ValueError('Recover account management before changing strategy or timeframe')
                if self.state['owned'] or (self.journal and any(r['phase'] not in ('closed','cancelled','rejected') for r in self.journal.records())):
                    raise ValueError('Close managed positions and reconcile pending orders before changing strategy or timeframe')
            if self.state['owned'] and cfg.margin_mode!=self.state['config']['margin_mode']:
                raise ValueError('Cannot change margin mode while positions are open')
            if cfg.strategy!='sma_7_50_intrabar' or not cfg.sma_ob_exit_enabled:
                self.state['order_block_exit_blocks']={}
                for owner in self.state['owned'].values():owner.pop('order_block_exit_error',None)
            if not cfg.sma_exit_enabled:
                self.state['sma_exit_blocks']={}
                for owner in self.state['owned'].values():owner.pop('sma_exit_error',None)
            if cfg.strategy in STAGED_STRATEGIES:
                before=Settings(**{k:v for k,v in self.state['config'].items() if k in Settings.model_fields})
                changed=before.strategy!=cfg.strategy or strategy_fingerprint(before)!=strategy_fingerprint(cfg)
                for symbol,gate in self.state.get('entry_gates',{}).items():
                    if gate['phase']=='armed' and (changed or symbol not in set(before.symbols)&set(cfg.symbols)):
                        gate.update(phase='waiting',direction=None,armed_at=None,sources=[],primary_timeframe=None)
            self.state['delayed_30m']={}
            self.state['rate_limit_resume']=False
            pnl_keys=('pnl_trailing_enabled','pnl_trailing_activation_dollars','pnl_trailing_drop_dollars')
            if any(self.state['config'][key]!=getattr(cfg,key) for key in pnl_keys) and not self.state['pnl_trailing']['triggered']:
                self.state['pnl_trailing']=new_pnl_trailing()
                self.event('Open P&L trailing settings changed; a new trailing cycle starts at the next verified position sample')
            breakeven_keys=('coin_breakeven_enabled','coin_breakeven_trigger_dollars','coin_breakeven_action_dollars')
            if any(self.state['config'].get(key)!=getattr(cfg,key) for key in breakeven_keys):
                for owner in self.state['owned'].values():
                    if not owner.get('coin_breakeven',{}).get('triggered'):
                        owner['coin_breakeven']={}
                        trailing_diagnostics.event(owner,'breakeven_settings_reset',self.clock())
            coin_keys=('coin_pnl_trailing_enabled','coin_pnl_trailing_activation_dollars','coin_pnl_trailing_drop_dollars')
            if any(self.state['config'].get(key)!=getattr(cfg,key) for key in coin_keys):
                for owner in self.state['owned'].values():
                    if not owner.get('coin_pnl_trailing',{}).get('triggered'):
                        trailing_diagnostics.event(owner,'settings_reset',self.clock(),previous_state=deepcopy(owner.get('coin_pnl_trailing',{})))
                        owner['coin_pnl_trailing']=new_pnl_trailing()
            for period in EQUITY_PERIODS:
                key=period+'_equity_trailing_enabled'
                if not self.state['equity_trailing'].get(period,{}).get('triggered') and bool(self.state['config'].get(key))!=getattr(cfg,key):
                    self.state['equity_trailing'].pop(period,None)
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
        now=self.clock();end=int(now*1000)
        periods=any(self.state['config'].get(p+'_equity_trailing_enabled',False) for p in EQUITY_PERIODS) or bool(equity_locks(self.state['equity_trailing'],now))
        baseline=self.state['risk'].get('baseline_ms')
        daily_current=baseline is not None and self.state['risk']['day']==day(now)
        if not periods and not daily_current:return 0.0
        since=baseline if daily_current else end
        if periods:since=min(since,self.state.get('equity_cashflow_cursor_ms',end)-60000)
        since=max(since,end-7*86400000)
        rows=self.pages('/v5/account/transaction-log',dict(accountType='UNIFIED',currency='USDT',startTime=since,endTime=end),50)
        total=dec(0);seen=set();ledger=dict(self.state['equity_cashflows'])
        for row in rows:
            identity=row.get('id')
            if identity is None:raise ValueError('Transaction identity unavailable')
            if identity in seen:continue
            seen.add(identity)
            if row.get('currency')!='USDT':continue
            kind=row.get('type')
            if kind in ('TRANSFER_IN','TRANSFER_OUT'):
                change=dec(row['change'])
                if not change.is_finite():raise ValueError('Invalid account cash flow')
                if periods:
                    stamp=int(row['transactionTime'])
                    if not 0<=stamp<=end:raise ValueError('Invalid transfer timestamp')
                    ledger[str(identity)]=dict(time=stamp,change=str(change))
                else:total+=change
            elif kind not in ('TRADE','SETTLEMENT','DELIVERY','LIQUIDATION'):
                raise ValueError('Unexpected account cash flow; account review required')
        if periods:
            ledger={key:value for key,value in ledger.items() if value['time']>=end-8*86400000}
            if len(ledger)>10000:raise ValueError('Transfer ledger exceeds safety bound')
            self.state.update(equity_cashflows=ledger,equity_cashflow_cursor_ms=end)
            total=sum((dec(value['change']) for value in ledger.values() if daily_current and value['time']>=baseline),dec(0))
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
        self.journal=ExecutionJournal(Path(self.path).with_suffix('.execution.sqlite'),client,environment=self.environment,clock=self.clock)
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
            if not recovery and equity_locks(self.state['equity_trailing'],self.clock()):
                raise ValueError('Equity trailing stop pause remains active until its UTC period ends')
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
            self.state=self.load();self.state.update(entries=False,status='paused',reason='Entries paused; risk management remains active',pending_signals={},reversals={},delayed_30m={},daily_resume_intent=False,rate_limit_resume=False,equity_trailing_resume_intent=False);self.save()
        return self.snapshot()

    def _lock(self,status,reason):
        if self.environment=='live':reason=reason.replace('Testnet','LIVE').replace('testnet','live')
        changed=self.state['status']!=status or self.state['reason']!=reason
        if status!='rate_limited':self.state['rate_limit_resume']=False
        self.state.update(entries=False,status=status,reason=reason,pending_signals={},reversals={},delayed_30m={})
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
        self.journal.record_timing(record['link'],fill_price=entry,filled_qty=filled,fill_confirmed_at=self.clock(),exchange_created_at=int(order['createdTime'])/1000)
        from .signal_stretch import with_fill
        stretch=with_fill(record.get('instrument',{}).get('thoth_signal_stretch'),
            {**record.get('result',{}).get('entry_timing',{}),'fill_price':entry,'fill_confirmed_at':self.clock()})
        self.state['owned'][symbol]=dict(signal_stretch=stretch,link=record['link'],side=record['payload']['side'],qty=str(filled),entry=str(entry),
            stop_dollars=record['stop_dollars'],margin=str(entry*filled/leverage),opened_ms=int(order['createdTime']),
            close_link=None,close_attempt=0,reason=None,
            entry_sources=record.get('instrument',{}).get('thoth_entry_sources',[]),crypto_ob_strength=record.get('instrument',{}).get('thoth_crypto_ob_strength'),price_sma_cross=record.get('instrument',{}).get('thoth_price_sma_cross'),sr_unfiltered=record.get('instrument',{}).get('thoth_sr_unfiltered'),trend_pullback=record.get('instrument',{}).get('thoth_trend_pullback'),trend_breakout=record.get('instrument',{}).get('thoth_trend_breakout'),major_pullback_intrabar=record.get('instrument',{}).get('thoth_major_pullback_intrabar'),
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
        trailing_diagnostics.event(owner,'close_attempt',self.clock(),reason=reason,link=payload['orderLinkId'],attempt=owner['close_attempt'])
        self.save()  # Preserve deterministic close identity before submission.
        try:
            result=self.journal.submit(payload)
            trailing_diagnostics.event(owner,'close_submission_result',self.clock(),phase=result.get('phase') if isinstance(result,dict) else None)
        except Exception as exc:
            trailing_diagnostics.event(owner,'close_error',self.clock(),details=failure_details(exc,'close'))
            self.save()
            raise
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
        new_closure=closure(self.state['risk'],owner['link'],pnl,cfg,self.clock(),symbol)
        if new_closure and owner.get('strategy') in STAGED_STRATEGIES and owner.get('strategy') not in DELAYED_STRATEGIES:
            from .staged_entry import new_gate,result
            if owner.get('strategy') in TWO_DIRECTION_STRATEGIES:
                from .two_direction_gate import new_gate
            gate=self.state.setdefault('entry_gates',{}).setdefault(symbol,new_gate())
            if result(gate,pnl,self.clock(),cfg.consecutive_losses,cfg.pause_minutes):
                self.state['pending_signals'].pop(symbol,None);self.state['reversals'].pop(symbol,None)
                self.event(symbol+': two-stage gate locked after '+str(gate['losses'])+' consecutive losses; cooldown '+str(cfg.pause_minutes)+' minutes, then a NEW '+('CLOSED 30m Order Block / S/R' if cfg.strategy==CLOSED_TWO_DIRECTION else '1h window Order Block / S/R' if cfg.strategy==TWO_DIRECTION else 'CLOSED 1h Order Block / S/R' if cfg.strategy==HOUR_GATE else '4h/Daily')+' signal is required')
            elif pnl>0:self.event(symbol+': profitable closure; two-stage loss count reset to zero; '+('two-direction sequence retained' if cfg.strategy in TWO_DIRECTION_STRATEGIES else 'waiting for NEW CLOSED 1h permission' if cfg.strategy==HOUR_GATE else 'confirmation remains armed'))
            if cfg.strategy==HOUR_GATE and gate['phase']!='cooldown':
                self._consume_hour_permission(symbol,gate)
                self.event(symbol+': position closed; hourly permission consumed; a NEW CLOSED 1h Order Block or S/R signal is required')
        coin_pause=self.state['risk'].get('symbols',{}).get(symbol,{}).get('pause_until')
        if cfg.loss_streak_scope=='per_coin' and coin_pause and coin_pause!=previous_pause:
            self.event(symbol+': consecutive-loss cooldown started; new entries paused for '+str(cfg.pause_minutes)+' minutes, exits remain managed')
        trade=dict(link=owner['link'],symbol=symbol,side='Long' if owner['side']=='Buy' else 'Short',entry=float(owner['entry']),qty=float(owner['qty']),opened_at=owner['opened_ms']/1000,
            crypto_ob_strength=owner.get('crypto_ob_strength'),price_sma_cross=owner.get('price_sma_cross'),sr_unfiltered=owner.get('sr_unfiltered'),trend_pullback=owner.get('trend_pullback'),trend_breakout=owner.get('trend_breakout'),major_pullback_intrabar=owner.get('major_pullback_intrabar'),coin_breakeven=deepcopy(owner.get('coin_breakeven')),trailing_diagnostics=deepcopy(owner.get('trailing_diagnostics')),signal_stretch=owner.get('signal_stretch'),entry_sources=owner.get('entry_sources',[]),strategy=owner.get('strategy','sr_breakout'),entry_timeframe=owner.get('entry_timeframe'),exit_timeframe=owner.get('exit_timeframe'),pnl=pnl,closed_at=self.clock(),reason=owner['reason'] or 'Exchange stop/close',protection_confirmed=owner.get('protection_confirmed',False),source_fingerprint=owner.get('source_fingerprint'),execution_fingerprint=owner.get('execution_fingerprint'),strategy_fingerprint=owner.get('strategy_fingerprint'),real_exchange=owner.get('real_exchange',False))
        if trade.get('trailing_diagnostics') is not None:
            trade['trailing_diagnostics']['settled_at']=self.clock()
            trade['trailing_diagnostics']['exit_reason']=trade['reason']
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
        for owner in self.state['owned'].values():
            trailing_diagnostics.event(owner,'monitor_error',self.clock(),details=details)
        self.state['monitor_error']=details
        self.state['monitor_failures']=(self.state.get('monitor_failures',[])+[dict(time=self.clock(),details=details)])[-100:]
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

    def _order_block_exit_rows(self,symbol,tracker):
        last=tracker.get('closed_state',{}).get('last_time')
        limit=1000 if last is None or tracker.get('source')!=self.ob_exit_source else min(1000,max(60,int((self.clock()-last)//14400)+3))
        return self.client.candles(symbol,'240',limit=limit)

    def _manage_order_block_exit(self,symbol,owner,p,cfg):
        if owner.get('strategy')!='sma_7_50_intrabar' or not cfg.sma_ob_exit_enabled or owner['close_link'] or owner.get('signal_exit'):return
        tracker=owner.setdefault('order_block_exit',{})
        if self.clock()<tracker.get('retry_after',0) or self.clock()-tracker.get('checked_at',0)<5:return
        from .order_block_exit import observe
        rows=self._order_block_exit_rows(symbol,tracker);now=self.clock()
        reason=observe(tracker,rows,p['side'],now,session=self.ob_exit_session,source=self.ob_exit_source,
            max_gap=min(300,max(30,len(self.state['owned'])/4+20)),opened_ms=owner.get('opened_ms'))
        tracker.update(checked_at=now)
        if tracker.get('waiting_for_candle') is not None:
            self.save();return
        tracker.pop('retry_after',None)
        recovered=owner.pop('order_block_exit_error',None)
        block=self.state['order_block_exit_blocks'].pop(symbol,None)
        if recovered or block:self.event(symbol+': 4h Order Block exit observations recovered; coin entry block cleared')
        if tracker['ready'] and not tracker.get('announced'):
            tracker['announced']=True
            self.event(symbol+': unfiltered 4h Order Block exit ready; waiting for '+('Bearish' if p['side']=='Buy' else 'Bullish')+' formation; block breaks ignored')
        if reason:owner['signal_exit']=reason
        self.save()  # Persist both observation consumption and exit obligation before close requests.

    def _order_block_entry_ready(self,symbol,cfg):
        if cfg.strategy!='sma_7_50_intrabar' or not cfg.sma_ob_exit_enabled:return True
        block=self.state['order_block_exit_blocks'].get(symbol)
        if not block:return True
        if self.clock()<block['retry_after']:return False
        try:
            from .order_block_exit import observe
            tracker={};rows=self._order_block_exit_rows(symbol,tracker)
            observe(tracker,rows,'Buy',self.clock(),session=self.ob_exit_session,source=self.ob_exit_source)
            if not tracker['ready']:return False
        except Exception as exc:
            block.update(details=failure_details(exc,'order_block_exit'),retry_after=self.clock()+30)
            self.save();return False
        del self.state['order_block_exit_blocks'][symbol]
        self.event(symbol+': fresh unfiltered 4h Order Block exit data verified; coin entry block cleared')
        self.save();return True

    def _manage_closed_sma7_exit(self,symbol,owner,p):
        if owner.get('strategy')=='volume_delta_1h_windows_sma7_14_exit':
            from .closed_sma7_14_exit import observe
            limit=32
        else:
            from .closed_sma7_exit import observe
            limit=8
        if owner['close_link'] or owner.get('signal_exit'):return
        tracker=owner.setdefault('sma_exit',{})
        if self.clock()<tracker.get('retry_after',0) or self.clock()-tracker.get('checked_at',0)<5:return
        record=self.journal.get(owner['link'])
        if 'entry_confirmed_at' not in tracker:
            from .entry_diagnostics import number
            filled=number(record.get('result',{}).get('entry_timing',{}).get('fill_observed_at'))
            # Missing fill timing must never fall back to the earlier order time.
            tracker['entry_confirmed_at']=filled if filled is not None and 0<filled<=self.clock() else self.clock()
        entered=tracker['entry_confirmed_at']
        rows=self.client.candles(symbol,'1',limit=limit)
        received_at=self.clock()
        reason=observe(tracker,rows,p['side'],self.clock(),entered,sampled_at=received_at)
        if tracker.get('waiting_for_candle') is not None:
            self.save();return
        previous_error=owner.pop('sma_exit_error',None)
        previous_block=self.state['sma_exit_blocks'].pop(symbol,None)
        tracker.pop('retry_after',None)
        if previous_error or previous_block:self.event(symbol+': closed 1m SMA7 exit data recovered')
        if reason:
            owner['signal_exit']=reason
            self.state['pending_signals'].pop(symbol,None);self.state['reversals'].pop(symbol,None)
            self.save()  # Persist a close-only obligation before exchange writes.
            self._close(symbol,p,reason)
        self.save()

    def _manage_sma_exit(self,symbol,owner,p,cfg):
        if owner.get('strategy') in ('volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit'):return self._manage_closed_sma7_exit(symbol,owner,p)
        if owner.get('strategy') in ('crypto_ob_strength_1h_close','major_pullback_30m_intrabar','trend_major_pullback_30m_close','trend_major_breakout_30m_close','price_sma50_30m_close','ob_sr_minute_hourly','sma_7_50_intrabar','sr_volume_intrabar','sr_4h_daily_unfiltered','volume_delta_intrabar','volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit','order_block_intrabar','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close','order_block_sr_1h_early',*STAGED_STRATEGIES):return
        if (not cfg.sma_exit_enabled and owner.get('strategy') not in HOURLY_MINUTE_STRATEGIES) or owner['close_link']:return
        tracker=owner.setdefault('sma_exit',{})
        if self.clock()<tracker.get('retry_after',0) or self.clock()-tracker.get('checked_at',0)<5:return
        timeframe='1' if owner.get('strategy') in HOURLY_MINUTE_STRATEGIES else cfg.timeframe
        rows=self.client.candles(symbol,timeframe,limit=51)
        inverse=owner.get('strategy')=='sr_inverse_intrabar'
        exit_side=({'Buy':'Sell','Sell':'Buy'}[p['side']] if inverse else p['side'])
        reason=observe_exit(tracker,rows,timeframe,exit_side,self.clock())
        if reason and inverse:reason='Inverse '+reason
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

    def _manage_coin_breakeven(self,active,cfg):
        for symbol,p in active.items():
            owner=self.state['owned'][symbol]
            if owner.get('close_link') or owner.get('signal_exit'):continue
            before=owner.get('coin_breakeven',{})
            updated=advance_breakeven(before,p['unrealisedPnl'],cfg.coin_breakeven_enabled,
                cfg.coin_breakeven_trigger_dollars,cfg.coin_breakeven_action_dollars,self.clock())
            owner['coin_breakeven']=updated
            if updated.get('active') and not before.get('active'):
                self.event(symbol+': breakeven armed; fixed close floor '+updated['action']+' USDT')
                trailing_diagnostics.event(owner,'breakeven_armed',self.clock(),state=deepcopy(updated))
            if updated.get('triggered'):
                owner['signal_exit']='Per-coin Breakeven'
                self.state['pending_signals'].pop(symbol,None);self.state['reversals'].pop(symbol,None)
                self.event(symbol+': breakeven floor reached; closing only this position')
                trailing_diagnostics.event(owner,'breakeven_triggered',self.clock(),state=deepcopy(updated))
            self.save()

    def _manage_coin_pnl_trailing(self,active,cfg):
        for symbol,p in active.items():
            owner=self.state['owned'][symbol]
            if owner.get('close_link') or owner.get('signal_exit'):continue
            before=owner.get('coin_pnl_trailing',new_pnl_trailing())
            updated=advance_pnl_trailing(before,p['unrealisedPnl'],True,cfg.coin_pnl_trailing_enabled,
                cfg.coin_pnl_trailing_activation_dollars,cfg.coin_pnl_trailing_drop_dollars,self.clock())
            owner['coin_pnl_trailing']=updated
            trailing_diagnostics.sample(owner,before,updated,p,cfg,self.clock())
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
            if not self.state['flatten_reason']:self.state['flatten_reason']='Open P&L trailing stop'
            self.state.update(pending_signals={},reversals={})
            self.event('Open P&L trailing drop reached at '+updated['current']+' USDT (peak '+updated['peak']+', close threshold '+updated['threshold']+'); closing all positions; entry permission retained')
            self.save()
        observer=getattr(self,'trailing_observer',None)
        if observer is not None:
            try:observer.submit(active,cfg,updated,self.state['owned'],self.clock())
            except Exception:pass  # Observation must never affect execution or its fixed tracker.

    def _manage_equity_trailing(self,equity,cfg):
        states=self.state['equity_trailing'];now=self.clock();was_locked=bool(equity_locks(states,now))
        for period in EQUITY_PERIODS:
            before=states.get(period,{})
            net=sum((dec(value['change']) for value in self.state['equity_cashflows'].values()
                if value['time']>=(before.get('baseline_ms',int(now*1000)) if before.get('period_start')==equity_window(period,now)[0] else int(now*1000))),dec(0))
            state=advance_equity_trailing(before,equity,net,getattr(cfg,period+'_equity_trailing_enabled'),
                getattr(cfg,period+'_equity_trailing_drop_dollars'),period,now)
            states[period]=state
            if state.get('triggered') and not before.get('triggered'):
                reason=EQUITY_LABELS[period]
                if not was_locked:self.state['equity_trailing_resume_intent']=self.state['entries']
                was_locked=True
                if not self.state['flatten_reason']:self.state['flatten_reason']=reason
                self.state.update(entries=False,pending_signals={},reversals={},delayed_30m={},daily_resume_intent=False,rate_limit_resume=False)
                self.event(reason+' reached; closing all positions; entries paused until UTC period end '+str(state['period_end']))
                self.save()  # Persist pause and close-all obligation before exchange writes.
        self._enforce_equity_pause()

    def _enforce_equity_pause(self,allow_resume=False,cfg=None):
        locks=equity_locks(self.state['equity_trailing'],self.clock())
        if locks:
            self.state.update(entries=False,pending_signals={},reversals={},delayed_30m={},daily_resume_intent=False,rate_limit_resume=False)
            if self.state['status'] in ('running','paused','equity_trailing_stop','cooldown'):
                self.state.update(status='equity_trailing_stop',reason=' / '.join(EQUITY_LABELS[key] for key in locks)+'; entries paused until all triggered UTC periods end')
        elif allow_resume and (self.state['status']=='equity_trailing_stop' or self.state['equity_trailing_resume_intent']):
            if self.state['status']=='equity_trailing_stop' and self.state['flatten_reason']:
                self.state['reason']='Equity trailing period ended; waiting for close-all settlement'
                return
            risk=self.state['risk']
            eligible=(self.state['status']=='equity_trailing_stop' and not self.state['flatten_reason']
                and not any(risk.get(key) for key in ('daily_stop','portfolio_stop','baseline_required'))
                and not (cfg.loss_streak_scope=='account' and (risk.get('pause_until') or 0)>self.clock()))
            resume=bool(eligible and cfg.equity_trailing_resume_automatic and self.state['equity_trailing_resume_intent'])
            self.state['equity_trailing_resume_intent']=False
            if self.state['status']=='equity_trailing_stop':
                self.state.update(entries=resume,status='running' if resume else 'paused',
                    reason='' if resume else 'Equity trailing period ended; manually enable entries after review')
                self.event('Equity trailing pause expired; '+('previously enabled entries resumed; waiting for NEW signals' if resume else 'entries remain paused'))

    def _schedule_entry_reason(self,cfg):
        if self.state.get('schedule_exit',{}).get('active'):
            return 'Scheduled daily cutoff: closing positions; waiting for confirmed flat and settlement'
        return schedule_reason(cfg,self.clock())

    def _schedule_status(self,state):
        result=schedule_status(TestnetSettings(**state['config']),self.clock())
        pending=state.get('schedule_exit',{}).get('active',False)
        result.update(flatten_pending=pending,daily_exit_enabled=state['config'].get('entry_daily_exit_enabled',False))
        if pending:result.update(enabled=True,applies=True,blocked=True,reason='Scheduled cutoff exit in progress; awaiting confirmed flat',next_open_at=None)
        return result

    def _manage_schedule_exit(self,active,cfg):
        period=daily_exit_period(cfg,self.clock())
        obligation=self.state.setdefault('schedule_exit',dict(active=False))
        records=self.journal.records() if period or obligation.get('active') else []
        pending=[r for r in records if not r['payload'].get('reduceOnly') and r['phase'] not in ('protected','closed','cancelled','rejected')]
        targets=[]
        if period:
            targets=[s for s,o in self.state['owned'].items() if float(o.get('opened_ms',0))/1000<period['reopen_at']]
            pending_before=[]
            for record in pending:
                timing=record.get('result',{}).get('entry_timing',{})
                submitted=timing.get('submit_started_at') or (record.get('instrument') or {}).get('thoth_entry_diagnostics',{}).get('qualified_at')
                if submitted is None or float(submitted)<period['reopen_at']:pending_before.append(record)
            if (targets or pending_before) and not obligation.get('active'):
                obligation=dict(active=True,started_at=self.clock(),cutoff_at=period['cutoff_at'],reason='Scheduled daily cutoff ('+cfg.entry_daily_start+' New York)')
                self.state['schedule_exit']=obligation
                self.state.update(pending_signals={},reversals={})
                self.event(obligation['reason']+': closing all managed positions and cancelling pending entries')
                self.save()  # Durable exit obligation BEFORE any exchange writes.
        if not obligation.get('active'):return
        self.state.update(pending_signals={},reversals={})
        self._cancel_entries()
        errors={}
        for symbol,position in active.items():
            if symbol not in self.state['owned']:continue
            try:self._close(symbol,position,obligation['reason'])
            except Exception as exc:
                errors[symbol]=failure_details(exc,'scheduled_exit')
                if obligation.get('errors',{}).get(symbol)!=errors[symbol]:
                    self.event(symbol+': scheduled close not confirmed; monitoring will reconcile/retry')
        obligation['errors']=errors
        if not active and not self.state['owned'] and not pending:
            obligation.update(active=False,completed_at=self.clock())
            self.event('Scheduled daily cutoff: all managed positions confirmed flat and entry outcomes settled')
        self.save()

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
            self._manage_schedule_exit(active,cfg)
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
                    try:self._manage_order_block_exit(symbol,owner,p,cfg)
                    except Exception as exc:
                        details=failure_details(exc,'order_block_exit')
                        owner['order_block_exit_error']=details
                        retry_after=self.clock()+30
                        owner.setdefault('order_block_exit',{})['retry_after']=retry_after
                        prior=self.state['order_block_exit_blocks'].get(symbol,{}).get('details')
                        self.state['order_block_exit_blocks'][symbol]=dict(details=details,retry_after=retry_after)
                        if prior!=details:self.event(symbol+': Order Block exit monitoring unavailable; only this coin’s new entries blocked · '+failure_summary(details))
                        self.save()
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

            self._manage_coin_breakeven(active,cfg)
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
            self.state.update(equity=equity,last_monitor=self.clock(),positions=[dict(symbol=s,side='Long' if p['side']=='Buy' else 'Short',qty=p['size'],entry=p['avgPrice'],mark=p.get('markPrice'),pnl=float(p.get('unrealisedPnl','0')),stop=p.get('stopLoss'),stop_dollars=self.state['owned'].get(s,{}).get('stop_dollars'),take_profit={**self.state['owned'].get(s,{}).get('take_profit',{'enabled':False}),'exchange_price':p.get('takeProfit')},coin_breakeven=self.state['owned'].get(s,{}).get('coin_breakeven',{}),coin_pnl_trailing=self.state['owned'].get(s,{}).get('coin_pnl_trailing',new_pnl_trailing()),trailing=self.state['owned'].get(s,{}).get('trailing',{'enabled':False}),opened_at=self.state['owned'].get(s,{}).get('opened_ms')) for s,p in active.items()])
            self._manage_equity_trailing(equity,cfg)
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
                if margin>0 and losses/margin*100>=cfg.portfolio_loss_pct:
                    if not self.state['risk']['portfolio_stop']:
                        self.state['last_portfolio_stop']=dict(time=self.clock(),losses=losses,margin=margin,
                            loss_pct=losses/margin*100,limit_pct=cfg.portfolio_loss_pct,basis=cfg.portfolio_loss_basis,
                            threshold_dollars=margin*cfg.portfolio_loss_pct/100)
                    self.state['risk']['portfolio_stop']=True
            if self.state['risk']['portfolio_stop'] and risk!='daily_stop':
                detail=self.state.get('last_portfolio_stop')
                explanation=('Overall position loss limit: $'+format(detail['losses'],'.4f')+' losses / $'+format(detail['margin'],'.4f')+
                    (' margin of losing positions' if detail['basis']=='losing_margin' else ' margin of all open positions')+
                    ' = '+format(detail['loss_pct'],'.2f')+'% (limit '+format(detail['limit_pct'],'.2f')+'%); manual restart required') if detail else 'Overall position loss limit; manual restart required'
                self._lock('portfolio_stop',explanation)
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
            self._enforce_equity_pause(allow_resume=True,cfg=cfg)
            self._release_delayed_30m(cfg)
            self._reversals(cfg)
            self._drain(cfg)
            self.save()

    def close_all(self):
        with self.lock:
            self.state=self.load()
            if not self.client or not self.state['management']:raise ValueError('Activate '+self.label+' risk management first')
            self.state['equity_trailing_resume_intent']=False
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

    def _signal_decision(self,symbol,reason,observation):
        cfg=Settings(**{k:v for k,v in self.state['config'].items() if k in Settings.model_fields}).model_dump()
        identity=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()[:16]
        stamp=observation.get('time')
        alert_key='thoth-'+hashlib.sha256((identity+':'+symbol+':'+str(stamp)).encode()).hexdigest()[:28]
        details=dict(alert_key=alert_key,time=self.clock(),symbol=symbol,reason=reason,
            strategy=self.state['config']['strategy'],signal=observation.get('signal'),
            signal_stretch=(observation.get('entry_context') or {}).get('signal_stretch'),
            entry_diagnostics=(observation.get('entry_context') or {}).get('entry_diagnostics'),
            observed_at=observation.get('time'),diagnostic=observation.get('signal_diagnostic'),source_signal=observation.get('source_signal'),entry_sources=observation.get('entry_sources',[]))
        if observation.get('source_status') is not None:details['source_status']=observation['source_status']
        from .signal_alerts import is_no_signal_check
        if is_no_signal_check(details):
            with self.db() as db:
                db.execute('INSERT INTO scan_checks(time,symbol,payload) VALUES(?,?,?)',(details['time'],symbol,json.dumps(details,allow_nan=False)))
                db.execute('DELETE FROM scan_checks WHERE id NOT IN (SELECT id FROM scan_checks ORDER BY id DESC LIMIT 5000)')
            return  # Analysis diagnostics do not create signal events or notifications.
        with self.db() as db:
            db.execute('INSERT INTO signal_decisions(time,symbol,payload) VALUES(?,?,?)',
                (details['time'],symbol,json.dumps(details,allow_nan=False)))
            db.execute('DELETE FROM signal_decisions WHERE id NOT IN (SELECT id FROM signal_decisions ORDER BY id DESC LIMIT 5000)')
        self.event(symbol+': signal '+reason)

    def _reversal_block_reason(self,symbol):
        reasons=[]
        scheduled=self._schedule_entry_reason(TestnetSettings(**self.state['config']))
        if scheduled:reasons.append(scheduled)
        if symbol_paused(self.state['risk'],symbol,TestnetSettings(**self.state['config']),self.clock()):
            reasons.append('per-coin consecutive-loss cooldown')
        if symbol in self.state['order_block_exit_blocks']:
            reasons.append('Order Block exit monitoring unavailable for this coin')
        if symbol in self.state['blocked_symbols']:
            reasons.append('symbol blocked: '+self.state['blocked_symbols'][symbol])
        if self.state['status']!='running':
            reasons.append(self.state['status'].replace('_',' ')+(': '+self.state['reason'] if self.state['reason'] else ''))
        if not self.state['entries']:reasons.append('new entries disabled')
        return '; '.join(reasons)

    def _hour_permission_used(self,symbol,gate):
        armed=gate.get('armed_at')
        if armed is None or not self.journal:return False
        with self.journal.db() as db:
            rows=db.execute("SELECT instrument,result FROM execution WHERE json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_strategy')=? AND phase!='rejected'",(symbol,HOUR_GATE)).fetchall()
        for instrument,result in rows:
            instrument=json.loads(instrument);result=json.loads(result)
            if result.get('not_submitted'):continue
            stamp=instrument.get('thoth_permission_armed_at')
            if stamp is None:stamp=(instrument.get('thoth_entry_diagnostics') or {}).get('permission_armed_at')
            if stamp is not None:
                if stamp==armed:return True
                continue
            # Older journal entries predate arm metadata. Their submission time
            # still proves whether this saved permission already placed a trade.
            submitted=(result.get('entry_timing') or {}).get('submit_started_at')
            if submitted is not None and submitted>=armed:return True
        return False

    def _consume_hour_permission(self,symbol,gate):
        bar=gate.get('permission_closed_bar')
        if bar is None:bar=int(gate['armed_at'])//3600*3600-3600
        gate.update(phase='consumed',permission_closed_bar=bar,
            consumed_closed_bar=max(bar,gate.get('consumed_closed_bar',-1)))
        self.state['pending_signals'].pop(symbol,None);self.state['reversals'].pop(symbol,None)

    def _two_direction_observation(self,symbol,observation,cfg):
        from .two_direction_gate import new_gate,arm,decision,finish
        if cfg.strategy==CLOSED_TWO_DIRECTION:
            from .closed_two_direction import arm
        gate=self.state.setdefault('entry_gates',{}).setdefault(symbol,new_gate())
        for key,value in new_gate().items():gate.setdefault(key,value)
        owner=self.state['owned'].get(symbol)
        enabled=not self._reversal_block_reason(symbol) and not self.state['flatten_reason']
        for message in arm(gate,observation.get('primary_events',[]),self.clock(),enabled,owner):
            self.state['pending_signals'].pop(symbol,None);self.state['reversals'].pop(symbol,None)
            self._signal_decision(symbol,message,dict(time=observation['time'],signal=None,entry_sources=gate['sources']))
        result={**observation,'signal':None}
        if gate['phase']=='armed' and owner and not gate['directions']:
            gate['directions']=[dict(side='Long' if owner['side']=='Buy' else 'Short',link=owner['link'])]
        if symbol in self.state['reversals'] or (owner and (owner['close_link'] or not owner.get('protection_confirmed'))):return result
        with self.journal.db() as db:
            unresolved=db.execute("SELECT 1 FROM execution WHERE json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.reduceOnly')=0 AND phase IN ('prepared','unknown','submitted','partial','stop_error','mismatch') LIMIT 1",(symbol,)).fetchone()
        if unresolved:return result
        if owner:
            record=self.journal.get(owner['link']) or {}
            filled=(record.get('result') or {}).get('entry_timing',{}).get('fill_observed_at')
            owner.setdefault('two_direction_filled_at',filled if isinstance(filled,(float,int)) and filled>0 else self.clock())
            if observation['time']<=owner['two_direction_filled_at']:return result
        side=observation.get('volume_signal')
        if cfg.strategy==CLOSED_TWO_DIRECTION and (observation.get('minute_closed_at') is None or observation['minute_closed_at']<=max(gate.get('armed_at') or 0,owner.get('two_direction_filled_at',0) if owner else 0)):return result
        action=decision(gate,side,observation['time'],self.clock())
        if not action:return result
        context=dict(arm_id=gate['arm_id'],armed_at=gate['armed_at'],primary_timeframe=gate['primary_timeframe'],confirmation_timeframe='1',
            direction_index=len(gate['directions']),entry_diagnostics={**(observation.get('entry_diagnostics') or {}),'permission_armed_at':gate['armed_at']})
        if cfg.strategy==CLOSED_TWO_DIRECTION:context['signal_closed_at']=observation['minute_closed_at']
        sources=gate['sources']+observation.get('volume_sources',[])
        if action=='exit':
            finish(gate);self.state['pending_signals'].pop(symbol,None);self.state['reversals'].pop(symbol,None)
            self.event(symbol+': third 1m direction; close only, waiting for NEW 1h window permission')
            if not owner:return result
        return {**result,'signal':side,'entry_sources':sources,'entry_context':context,'two_direction_action':action}

    def _delayed_30m_observation(self,symbol,observation,cfg):
        from .delayed_30m import candidate,ready
        if observation.get('observation_channel')=='delayed_release':
            item=(observation.get('entry_context') or {}).get('delayed_30m') or {}
            if not item or observation.get('signal')!=item.get('side') or not ready(item,cfg,self.clock()):return {**observation,'signal':None}
            return observation
        raw=observation.get('delayed_capture')
        blocked=self._reversal_block_reason(symbol) or ('close-all in progress' if self.state['flatten_reason'] else '')
        if raw and blocked:
            self._signal_decision(symbol,'skipped: delayed 30m signal capture blocked: '+blocked,dict(time=observation['time'],signal=None,signal_diagnostic=raw.get('signal_diagnostic'),entry_sources=['htf:30:'+source for source in raw.get('entry_sources',[])]))
        if raw and not blocked:
            item=candidate(raw,cfg,self.clock())
            if item:
                self.state.setdefault('delayed_30m',{})[symbol]=item
                from datetime import datetime
                from zoneinfo import ZoneInfo
                at=datetime.fromtimestamp(item['due_at'],ZoneInfo('America/New_York')).strftime('%H:%M New York')
                self._signal_decision(symbol,'CLOSED 30m '+item['side']+' saved; scheduled entry '+at+' (:07 / :37)',
                    dict(time=observation['time'],signal=None,signal_diagnostic=item.get('diagnostic'),entry_sources=['htf:30:'+source for source in item['sources']]))
            else:
                self._signal_decision(symbol,'skipped: delayed signal outside capture window',dict(time=observation['time'],signal=raw.get('signal'),signal_diagnostic=raw.get('signal_diagnostic'),entry_sources=['htf:30:'+source for source in raw.get('entry_sources',[])]))
        # Capture/block decisions above already contain the full source; do not overwrite them.
        return {**observation,'signal':None,'signal_diagnostic':None if raw else observation.get('signal_diagnostic')}

    def _release_delayed_30m(self,cfg):
        if cfg.strategy not in DELAYED_STRATEGIES:return
        from .delayed_30m import ready
        settings=Settings(**cfg.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump()
        for symbol,item in sorted(list(self.state.setdefault('delayed_30m',{}).items()),key=lambda pair:(pair[1]['due_at'],pair[1]['captured_at'])):
            now=self.clock()
            if now<item['due_at']:continue
            if not ready(item,cfg,now):
                self.state['delayed_30m'].pop(symbol,None)
                self._signal_decision(symbol,'skipped: delayed :07 / :37 entry window expired or settings changed',dict(time=now,signal=item['side'],entry_sources=['htf:30:'+source for source in item['sources']],signal_diagnostic=item.get('diagnostic')));continue
            blocked=self._reversal_block_reason(symbol)
            if blocked or self.state['flatten_reason']:
                self.state['delayed_30m'].pop(symbol,None)
                self._signal_decision(symbol,'skipped: delayed entry blocked: '+(blocked or 'close-all in progress'),dict(time=now,signal=item['side'],entry_sources=['htf:30:'+source for source in item['sources']],signal_diagnostic=item.get('diagnostic')));continue
            owner=self.state['owned'].get(symbol)
            if not owner and (len(self.state['owned'])>=cfg.max_positions or any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records())):continue
            self.state['delayed_30m'].pop(symbol,None);self.save() # Consume before any exchange write or crash.
            self.apply_signals(settings,{symbol:dict(time=now,signal=item['side'],mark=None,entry_sources=['htf:30:'+source for source in item['sources']],
                observation_channel='delayed_release',signal_diagnostic=item.get('diagnostic'),entry_context=dict(primary_timeframe='30',delayed_30m=item))},monitor_account=False)

    def _staged_observation(self,symbol,observation,cfg):
        if cfg.strategy in DELAYED_STRATEGIES:return self._delayed_30m_observation(symbol,observation,cfg)
        if cfg.strategy in TWO_DIRECTION_STRATEGIES:return self._two_direction_observation(symbol,observation,cfg)
        from .staged_entry import new_gate,apply
        gate=self.state.setdefault('entry_gates',{}).setdefault(symbol,new_gate())
        if cfg.strategy==HOUR_GATE and gate['phase']=='armed' and self._hour_permission_used(symbol,gate):
            self._consume_hour_permission(symbol,gate)
            self.event(symbol+': CLOSED 1h permission already used; waiting for a NEW closed-hour signal after this position')
        enabled=bool(self.state['entries'] and self.state['status']=='running' and not self.state['flatten_reason']
            and symbol not in self.state['blocked_symbols'] and not symbol_paused(self.state['risk'],symbol,cfg,self.clock()))
        volume=dict(signal=observation.get('volume_signal'),time=observation['time'],entry_sources=observation.get('volume_sources',[]))
        confirmation,events=apply(gate,observation.get('primary_events',[]),volume,self.clock(),enabled=enabled,
            position_open=symbol in self.state['owned'],primary_label='Order Block or S/R' if cfg.strategy==HOUR_GATE else 'S/R' if cfg.strategy==ORDER_BLOCK_STRATEGY else 'S/R or RSI',
            confirmation_label='Order Block or S/R' if cfg.strategy==HOUR_GATE else 'Order Block' if cfg.strategy==ORDER_BLOCK_STRATEGY else 'Volume Delta',confirmation_timeframe=cfg.timeframe,primary_window='CLOSED 1h' if cfg.strategy==HOUR_GATE else '4h/Daily',single_use=cfg.strategy==HOUR_GATE)
        if cfg.strategy==HOUR_GATE and symbol in self.state['owned']:
            confirmation=None
            owner=self.state['owned'][symbol];bar=observation.get('closed_exit_bar')
            if (observation.get('exit_signal') in ('Long','Short') and bar==int(self.clock())//60*60-60
                    and bar+60>owner['opened_ms']/1000):
                confirmation=dict(signal=observation['exit_signal'],entry_sources=observation.get('exit_sources',[]),entry_context=None)
            observation={**observation,'signal_diagnostic':None}
        for message in events:
            self._signal_decision(symbol,'two-stage gate: '+message,dict(time=observation['time'],signal=None,entry_sources=gate['sources']))
        if confirmation and cfg.strategy==HOUR_GATE and observation.get('entry_diagnostics'):
            confirmation['entry_context']={**(confirmation.get('entry_context') or {}),'entry_diagnostics':{**observation['entry_diagnostics'],'permission_armed_at':gate.get('armed_at')}}
        return {**observation,'signal':None,**(confirmation or {})}

    def apply_signals(self,cfg,observations,*,monitor_account=True):
        with self.lock:
            if not self.client or not self.state['management']:return
            current=TestnetSettings(**self.state['config'])
            if cfg!=Settings(**current.model_dump(exclude={'margin_mode','dedicated_account'})).model_dump():return
            last_monitor=self.state.get('last_monitor')
            if monitor_account or last_monitor is None or not 0<=self.clock()-last_monitor<=15:
                try:self.monitor()
                except Exception as exc:self._monitor_failure(exc)
            identity=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()[:16]
            for symbol,observation in observations.items():
                key=identity+':'+symbol
                if current.strategy in TWO_DIRECTION_STRATEGIES or current.strategy in DELAYED_STRATEGIES:
                    channel=observation.get('observation_channel') or ('hour_permission' if observation.get('primary_events') else 'minute_signal')
                    if channel not in ('hour_permission','minute_signal','delayed_capture','delayed_release'):continue
                    key+=':'+channel
                stamp=observation['time'];previous=self.state['cursors'].get(key)
                if previous is not None and stamp<=previous:continue
                if current.strategy in TWO_DIRECTION_STRATEGIES and previous is None:observation={**observation,'volume_signal':None}
                if current.strategy in STAGED_STRATEGIES:observation=self._staged_observation(symbol,observation,current)
                self.state['cursors'][key]=stamp
                self.save()  # Never replay this closed candle after a crash.
                if observation.get('inverse_delay_event'):self.event(symbol+': '+observation['inverse_delay_event'])
                diagnostic=observation.get('signal_diagnostic')
                if observation.get('signal') not in ('Long','Short'):
                    if diagnostic:self._signal_decision(symbol,'skipped: '+diagnostic['reason'],observation)
                    continue
                if self.state['flatten_reason']:
                    self._signal_decision(symbol,'skipped: close-all still settling',observation);continue
                if previous is None and not (current.strategy in DELAYED_STRATEGIES and observation.get('observation_channel')=='delayed_release'):
                    self._signal_decision(symbol,'skipped: initial execution baseline; old signal not replayed',observation);continue
                if not 0<=self.clock()-stamp<=(30 if current.strategy in SHORT_LIVED_SIGNAL_STRATEGIES else 2*interval_seconds(current.timeframe)):
                    self._signal_decision(symbol,'skipped: signal expired',observation);continue
                entry_context=observation.get('entry_context')
                if current.strategy=='crypto_ob_strength_1h_close':
                    from .crypto_ob_strength import validate_entry
                    try:validate_entry(symbol,observation['signal'],entry_context,self.clock())
                    except ValueError as exc:
                        self._signal_decision(symbol,'skipped: '+str(exc),observation);continue
                if entry_context and entry_context.get('entry_diagnostics'):
                    entry_context={**entry_context,'entry_diagnostics':{**entry_context['entry_diagnostics'],'queued_at':self.clock()}}
                side='Buy' if observation['signal']=='Long' else 'Sell'
                owner=self.state['owned'].get(symbol)
                if owner:
                    if owner['side']==side:
                        self._signal_decision(symbol,'skipped: same-direction position already open',observation);continue
                    if current.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit') and (len(self.state['owned'])>=current.max_positions or self._reversal_block_reason(symbol)):
                        self._signal_decision(symbol,'skipped: replacement entry unavailable; existing exits remain active',observation);continue
                    reason='Opposite strength-qualified CLOSED 1h Order Block formation' if current.strategy=='crypto_ob_strength_1h_close' else 'Opposite CLOSED 15m-confirmed 30m Major bounce/rejection' if current.strategy=='major_pullback_30m_intrabar' else 'Opposite CLOSED 30m trend-aligned Major breakout' if current.strategy=='trend_major_breakout_30m_close' else 'Opposite CLOSED 30m trend-aligned Major pullback' if current.strategy=='trend_major_pullback_30m_close' else 'Opposite CLOSED 30m price / SMA50 cross' if current.strategy=='price_sma50_30m_close' else 'Opposite delayed CLOSED 30m signal' if current.strategy in DELAYED_STRATEGIES else ('Third CLOSED 1m direction (close only)' if observation.get('two_direction_action')=='exit' else 'Second CLOSED 1m direction (reverse)') if current.strategy==CLOSED_TWO_DIRECTION else ('Third 1m Order Block / S/R direction (close only)' if observation.get('two_direction_action')=='exit' else 'Second 1m Order Block / S/R direction (reverse)') if current.strategy in TWO_DIRECTION_STRATEGIES else 'Opposite :30 Volume Delta signal' if current.strategy in ('volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit') else 'Opposite CLOSED 1m Order Block / S/R signal' if current.strategy==HOUR_GATE else 'Opposite 1m Order Block / S/R signal' if current.strategy=='ob_sr_minute_hourly' else 'Opposite SMA 7/50 intrabar crossover' if current.strategy=='sma_7_50_intrabar' else 'Opposite 4h/Daily S/R breakout or bounce/rejection' if current.strategy=='sr_4h_daily_unfiltered' else 'Opposite S/R volume signal' if current.strategy=='sr_volume_intrabar' else 'Opposite Order Block / S/R signal' if current.strategy in ('order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close','order_block_sr_1h_early') else 'Opposite Order Block signal' if current.strategy in (ORDER_BLOCK_STRATEGY,'order_block_intrabar') else 'Opposite Volume Delta signal' if current.strategy in ('sma_7_50_intrabar','sr_volume_intrabar','sr_4h_daily_unfiltered','volume_delta_intrabar','volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit','order_block_intrabar','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close','order_block_sr_1h_early',*STAGED_STRATEGIES) else 'Opposite inverse S/R signal' if current.strategy=='sr_inverse_intrabar' else 'Opposite Daily S/R or RSI signal' if current.strategy=='sr_rsi_daily' else 'Opposite RSI signal' if current.strategy=='rsi_hourly_intrabar' else 'Opposite combined S/R or SMA 7/50 signal' if current.strategy=='sr_sma_7_50' else 'Opposite SMA crossover' if current.strategy in ('sma_cross','sma_7_50') else 'Opposite breakout'
                    owner['signal_exit']=reason
                    if current.opposite_action=='reverse' and observation.get('two_direction_action')!='exit' and not self._schedule_entry_reason(current) and self.state['entries'] and self.state['status']=='running' and symbol not in self.state['blocked_symbols'] and not symbol_paused(self.state['risk'],symbol,current,self.clock()):
                        replacement_context={**(entry_context or {}),'queued_reversal':True} if current.strategy in TWO_DIRECTION_STRATEGIES else entry_context
                        self.state['reversals'][symbol]=dict(side=observation['signal'],time=stamp,identity=identity,context=replacement_context)
                        self.event(symbol+': reverse '+observation['signal']+' queued; waiting for current position to close and settle')
                    elif current.opposite_action=='reverse' and observation.get('two_direction_action')!='exit':
                        self.event(symbol+': opposite-signal exit requested; replacement entry blocked: '+self._reversal_block_reason(symbol))
                    self._signal_decision(symbol,('opposite CLOSED 1m signal: ' if current.strategy==HOUR_GATE else 'opposite signal: ')+('reversal queued' if symbol in self.state['reversals'] else 'exit requested; '+(self._reversal_block_reason(symbol) or ('third direction is close-only' if observation.get('two_direction_action')=='exit' else 'close-only setting'))),observation)
                    self.save()  # Retain exit obligation if a position read fails.
                    try:
                        p=self.positions().get(symbol)
                        if p:self._close(symbol,p,reason)
                    except Exception as exc:self._monitor_failure(exc)
                    self.save();continue
                scheduled=self._schedule_entry_reason(current)
                if scheduled:
                    self._signal_decision(symbol,'skipped: '+scheduled,observation);continue
                hour_block=self._minute_hour_block_reason(symbol,current)
                if hour_block:
                    self._signal_decision(symbol,'skipped: '+hour_block,observation);continue
                if self._sr_volume_window_used(symbol,current):
                    self._signal_decision(symbol,'skipped: one entry per coin per 4-hour candle already used',observation);continue
                if self.state['entries'] and self.state['status']=='running' and symbol not in self.state['blocked_symbols'] and not symbol_paused(self.state['risk'],symbol,current,self.clock()):
                    self.state['pending_signals'][symbol]=dict(side=observation['signal'],time=stamp,identity=identity,context=entry_context)
                    self._signal_decision(symbol,'queued: '+observation['signal']+'; awaiting entry checks',observation)
                else:self._signal_decision(symbol,'skipped: '+self._reversal_block_reason(symbol),observation)
            self._reversals(current)
            self._drain(current)
            self.save()

    def _sma_entry_ready(self,symbol,cfg):
        block=self.state['sma_exit_blocks'].get(symbol)
        if cfg.strategy in ('volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit') and block:
            if self.clock()<block['retry_after']:return False
            try:
                if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'):
                    from .closed_sma7_14_exit import observe
                    limit=32
                else:
                    from .closed_sma7_exit import observe
                    limit=8
                tracker={};rows=self.client.candles(symbol,'1',limit=limit);received_at=self.clock()
                observe(tracker,rows,'Buy',self.clock(),0,sampled_at=received_at)
                if tracker.get('waiting_for_candle') is not None:return False
            except Exception:return False
            del self.state['sma_exit_blocks'][symbol];self.save();return True
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

    def _minute_hour_symbols(self,state=None):
        """Durable reservations plus accepted/uncertain intents in this UTC hour."""
        state=self.state if state is None else state
        start=int(self.clock())//3600*3600
        symbols={symbol for symbol,window in state.get('minute_hour_entries',{}).items() if window==start}
        if self.journal:
            with self.journal.db() as db:
                symbols.update(row[0] for row in db.execute("SELECT DISTINCT json_extract(payload,'$.symbol') FROM execution WHERE phase!='rejected' AND COALESCE(json_extract(result,'$.not_submitted'),0)=0 AND json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_minute_hour_window')=?",(start,)))
        return symbols

    def _minute_hour_block_reason(self,symbol,cfg):
        if cfg.strategy!='ob_sr_minute_hourly':return None
        start=int(self.clock())//3600*3600
        if self.clock()>=start+cfg.minute_entry_window_minutes*60:
            return 'hourly entry window closed; waiting for a NEW signal in the next hour'
        used=self._minute_hour_symbols()
        if symbol in used:return 'this coin already entered in this UTC hour'
        if len(used)>=cfg.max_positions:return 'hourly total entry limit reached ('+str(cfg.max_positions)+'); exits remain active'
        return None

    def _sr_volume_window_used(self,symbol,cfg):
        if cfg.strategy!='sr_volume_intrabar':return False
        start=int(self.clock())//14400*14400
        if self.state.get('sr_volume_entry_windows',{}).get(symbol)==start:return True
        owner=self.state['owned'].get(symbol,{})
        if owner.get('strategy')==cfg.strategy and int(owner.get('opened_ms',0))/1000>=start:return True
        # Include already-settled entries from before this protection was deployed.
        with self.db() as db:
            for payload, in db.execute('SELECT payload FROM trade_history WHERE symbol=? AND closed_at>=?',(symbol,start)):
                trade=json.loads(payload)
                if trade.get('strategy')==cfg.strategy and float(trade.get('opened_at') or 0)>=start:return True
        if self.journal:
            for record in self.journal.records():
                if record['payload']['symbol']!=symbol or record['payload'].get('reduceOnly') or record['phase']=='rejected' or record.get('result',{}).get('not_submitted'):continue
                if (record.get('instrument') or {}).get('thoth_sr_volume_window')==start:return True
        return False

    def _early_hour_used(self,symbol,cfg):
        if cfg.strategy!='order_block_sr_1h_early':return False
        bar=int(self.clock())//3600*3600
        if self.state.get('early_entry_windows',{}).get(symbol)==bar:return True
        with self.journal.db() as db:
            return db.execute("SELECT 1 FROM execution WHERE phase!='rejected' AND COALESCE(json_extract(result,'$.not_submitted'),0)=0 AND json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_early_window')=? LIMIT 1",(symbol,bar)).fetchone() is not None

    def _midpoint_hour_used(self,symbol):
        bar=int(self.clock())//3600*3600
        with self.journal.db() as db:
            return db.execute("SELECT 1 FROM execution WHERE phase!='rejected' AND COALESCE(json_extract(result,'$.not_submitted'),0)=0 AND json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_midpoint_window')=? LIMIT 1",(symbol,bar)).fetchone() is not None

    def _volume_window_used(self,symbol,start):
        with self.journal.db() as db:
            return db.execute("SELECT 1 FROM execution WHERE phase!='rejected' AND COALESCE(json_extract(result,'$.not_submitted'),0)=0 AND json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.reduceOnly')=0 AND json_extract(instrument,'$.thoth_volume_window')=? LIMIT 1",(symbol,start)).fetchone() is not None

    def _entry(self,symbol,side,stamp,identity,cfg,context=None):
        from .tradfi import EXCLUDED_SYMBOLS
        if symbol in EXCLUDED_SYMBOLS:return
        checks_started_at=self.clock();check_timing={}
        scheduled=self._schedule_entry_reason(cfg)
        if scheduled:
            self._signal_decision(symbol,'skipped: '+scheduled,dict(time=stamp,signal=side));return
        reservations={key:deepcopy(self.state.get(key)) for key in ('entry_gates','early_entry_windows','sr_volume_entry_windows','minute_hour_entries')} if (cfg.entry_daily_enabled or cfg.entry_weekend_enabled or cfg.strategy=='order_block_sr_1h_early') else {}
        if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'):
            from .volume_delta_windows import fresh as window_fresh,window_start
            if not window_fresh(context or {},cfg,self.clock()) or self._volume_window_used(symbol,window_start(self.clock())):
                self._signal_decision(symbol,'skipped: Volume Delta window decision expired or this coin already entered this window',dict(time=stamp,signal=side));return
        if cfg.strategy in ('volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit'):
            from .volume_delta_midpoint import window as midpoint_window
            observed=(context or {}).get('midpoint_observed_at')
            if (not midpoint_window(self.clock()) or (context or {}).get('midpoint_bar')!=int(self.clock())//3600*3600 or observed is None or not midpoint_window(observed) or not 0<=self.clock()-observed<=30 or self._midpoint_hour_used(symbol)):
                self._signal_decision(symbol,'skipped: :30 Volume Delta decision expired, unavailable or this coin already entered this hour',dict(time=stamp,signal=side));return
        if cfg.strategy=='order_block_sr_1h_early':
            from .early_hour import window,direction_check
            checked=direction_check((context or {}).get('early_price'),side,(context or {}).get('sma200_baseline'),self.clock())
            if not checked['passed']:
                self._signal_decision(symbol,'skipped: '+checked['reason'],dict(time=stamp,signal=side));return
            if not window(self.clock()) or self._early_hour_used(symbol,cfg):
                self._signal_decision(symbol,'skipped: early window closed or this coin already entered this hour',dict(time=stamp,signal=side));return
        hour_block=self._minute_hour_block_reason(symbol,cfg)
        if hour_block:
            self._signal_decision(symbol,'skipped: '+hour_block,dict(time=stamp,signal=side));return
        if equity_locks(self.state['equity_trailing'],self.clock()):
            self._signal_decision(symbol,'skipped: equity trailing period pause active',{'time':stamp,'signal':side});return
        if self._sr_volume_window_used(symbol,cfg):
            self._signal_decision(symbol,'skipped: one entry per coin per 4-hour candle already used',{'time':stamp,'signal':side});return
        if cfg.strategy in STAGED_STRATEGIES and cfg.strategy not in DELAYED_STRATEGIES:
            gate=self.state.get('entry_gates',{}).get(symbol,{})
            if cfg.strategy==HOUR_GATE and gate.get('phase')=='armed' and self._hour_permission_used(symbol,gate):
                self._consume_hour_permission(symbol,gate)
            if gate.get('phase')!='armed' or not context or context.get('arm_id')!=gate.get('arm_id'):
                self._signal_decision(symbol,'skipped: two-stage gate is not armed for this signal',dict(time=stamp,signal=side));return
        if cfg.strategy in DELAYED_STRATEGIES:
            from .delayed_30m import ready
            saved=(context or {}).get('delayed_30m') or {}
            if not saved or saved.get('side')!=side or not ready(saved,cfg,self.clock()):
                self._signal_decision(symbol,'skipped: delayed :07 / :37 entry expired or unavailable',dict(time=stamp,signal=side));return
        if cfg.strategy in TWO_DIRECTION_STRATEGIES:
            from .two_direction_gate import entry_fresh
            if cfg.strategy==CLOSED_TWO_DIRECTION:
                from .closed_two_direction import entry_fresh
            if not entry_fresh(stamp,context,self.clock()):
                self._signal_decision(symbol,'skipped: 1m entry signal expired',dict(time=stamp,signal=side));return
        if cfg.strategy in (HOUR_GATE,'ob_sr_minute_hourly') and int(stamp)//60!=int(self.clock())//60:
            self._signal_decision(symbol,'skipped: 1m entry signal candle expired',dict(time=stamp,signal=side));return
        if symbol_paused(self.state['risk'],symbol,cfg,self.clock()):return
        if symbol in self.state['blocked_symbols']:return
        if self.state['flatten_reason'] or self.state['status']!='running' or not self.state['entries']:return
        if len(self.state['owned'])>=cfg.max_positions:return
        if not self._order_block_entry_ready(symbol,cfg):
            self._signal_decision(symbol,'skipped: Order Block exit monitoring unavailable for this coin',{'time':stamp,'signal':side});return
        if not self._sma_entry_ready(symbol,cfg):
            self._signal_decision(symbol,'skipped: SMA exit monitoring unavailable for this coin',{'time':stamp,'signal':side});return
        if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
        # Never place the next trade while a previous close awaits realized P&L.
        active=self.positions()
        if symbol in active or set(self.state['owned'])-set(active):return
        try:
            if cfg.strategy in ('order_block_intrabar','order_block_sr_intrabar','order_block_sr_1h'):
                from .order_block import entry_volume
                from .timeframes import interval_seconds
                duration=interval_seconds(cfg.timeframe);start=int(self.clock())//duration*duration
                if not context or context.get('order_block_bar')!=start:
                    raise ValueError('Entry signal is not from the current '+('1h' if cfg.timeframe=='60' else '4h')+' candle')
                check_timing['volume_recheck_started_at']=self.clock()
                volume_rows=self.client.candles(symbol,cfg.timeframe,limit=20)
                check_timing['volume_recheck_completed_at']=self.clock()
                if int(self.clock())//duration*duration!=start or not volume_rows or volume_rows[-1]['time']!=start or not entry_volume(volume_rows,cfg.timeframe)['passed']:
                    raise ValueError('Source-timeframe Volume must exceed MA 20 before entry')
            if cfg.strategy in ('order_block_sr_1h_inverted_close','order_block_sr_1h_close'):
                expected=int(self.clock())//3600*3600-3600
                if not context or context.get('closed_signal_bar')!=expected:
                    raise ValueError('Entry signal is not from the latest CLOSED 1h candle')
            if cfg.strategy=='crypto_ob_strength_1h_close':
                from .crypto_ob_strength import validate_entry
                validate_entry(symbol,side,context,self.clock())
            check_timing['instrument_lookup_started_at']=self.clock()
            instrument=self.instrument_cache.get(symbol)
            check_timing['instrument_rules_cached']=bool(instrument)
            if not instrument:instrument=self.client.instrument(symbol);self.instrument_cache[symbol]=instrument
            check_timing['instrument_lookup_completed_at']=self.clock()
            leverage=instrument['leverageFilter']
            if not (dec(leverage['minLeverage'])<=cfg.leverage<=dec(leverage['maxLeverage'])) or dec(cfg.leverage)%dec(leverage['leverageStep'])!=0:
                raise ValueError('Requested leverage is unsupported')
            check_timing['ticker_started_at']=self.clock()
            ticker=self.client.request('GET','/v5/market/tickers',dict(category='linear',symbol=symbol))['list'][0]
            check_timing['ticker_completed_at']=self.clock()
            price=dec(ticker['lastPrice'])
            pullback_check=None
            if cfg.strategy in ('trend_major_pullback_30m_close','trend_major_breakout_30m_close','major_pullback_30m_intrabar'):
                trend_label='Major Pullback 30m / 15m No SMA' if cfg.strategy=='major_pullback_30m_intrabar' else 'Trend + Major Breakout' if cfg.strategy=='trend_major_breakout_30m_close' else 'Trend + Major Pullback'
                if cfg.strategy=='major_pullback_30m_intrabar':
                    from .major_pullback_intrabar import recheck as pullback_recheck
                elif cfg.strategy=='trend_major_breakout_30m_close':
                    from .trend_breakout_live import recheck as pullback_recheck
                else:
                    from .trend_pullback_live import recheck as pullback_recheck
                pullback_check=pullback_recheck(side,context,self.clock(),price)
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
            check_timing['leverage_started_at']=self.clock()
            try:self.client.request('POST','/v5/position/set-leverage',dict(category='linear',symbol=symbol,buyLeverage=str(cfg.leverage),sellLeverage=str(cfg.leverage)),private=True)
            except BybitError as exc:
                if exc.code!=110043:raise
            check_timing['leverage_completed_at']=self.clock()
            from .signal_sources import recorded_source_map
            with self.db() as db:entry_sources=recorded_source_map(db,payload['orderLinkId']).get(payload['orderLinkId'],[])
            if not entry_sources and cfg.strategy=='price_sma50_30m_close':entry_sources=['price_sma:50']
            if not entry_sources and cfg.strategy in ('sma_cross','sma_7_50'):
                entry_sources=['sma:7_200' if cfg.strategy=='sma_cross' else 'sma:7_50']
            metadata={**instrument,'thoth_entry_sources':entry_sources,'thoth_take_profit':{'enabled':cfg.take_profit_enabled,'dollars':cfg.take_profit_dollars},'thoth_strategy':cfg.strategy,'thoth_entry_timeframe':'1' if cfg.strategy in (HOUR_GATE,*TWO_DIRECTION_STRATEGIES,'ob_sr_minute_hourly') else (context or {}).get('primary_timeframe',cfg.timeframe),'thoth_exit_timeframe':'1' if cfg.strategy in (HOUR_GATE,*TWO_DIRECTION_STRATEGIES,'ob_sr_minute_hourly','volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit') else 'none' if cfg.strategy in ('major_pullback_30m_intrabar','trend_major_pullback_30m_close','trend_major_breakout_30m_close','sma_7_50_intrabar','sr_volume_intrabar','sr_4h_daily_unfiltered','volume_delta_intrabar','volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit','order_block_intrabar','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close','order_block_sr_1h_early',*STAGED_STRATEGIES) else '1' if cfg.strategy in HOURLY_MINUTE_STRATEGIES else cfg.timeframe,'thoth_trailing':{key:getattr(cfg,key) for key in TRAILING_FIELDS}}
            if cfg.strategy in DELAYED_STRATEGIES:metadata['thoth_delayed_30m']=(context or {})['delayed_30m']
            if cfg.strategy=='major_pullback_30m_intrabar':metadata['thoth_major_pullback_intrabar']={**context['major_pullback_intrabar'],'submission_quote_check':pullback_check}
            if cfg.strategy=='trend_major_breakout_30m_close':metadata['thoth_trend_breakout']={**context['trend_breakout'],'submission_quote_check':pullback_check}
            if cfg.strategy=='trend_major_pullback_30m_close':metadata['thoth_trend_pullback']={**context['trend_pullback'],'submission_quote_check':pullback_check}
            if (context or {}).get('sr_unfiltered'):metadata['thoth_sr_unfiltered']=context['sr_unfiltered']
            if (context or {}).get('price_sma_cross'):metadata['thoth_price_sma_cross']=context['price_sma_cross']
            if (context or {}).get('crypto_ob_strength'):metadata['thoth_crypto_ob_strength']=context['crypto_ob_strength']
            if (context or {}).get('signal_stretch'):metadata['thoth_signal_stretch']=context['signal_stretch']
            metadata['thoth_entry_diagnostics']={**((context or {}).get('entry_diagnostics') or {}),
                'checks_started_at':checks_started_at,'checks_completed_at':self.clock(),**check_timing}
            if cfg.strategy in ('volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit','volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'):
                if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'):
                    from .volume_delta_windows import recheck as midpoint_recheck
                    minute_limit=61
                else:
                    from .volume_delta_midpoint import recheck as midpoint_recheck
                    minute_limit=32
                fresh=self.client.candles(symbol,'60',limit=2)
                lower=self.client.candles(symbol,'1',limit=minute_limit)
                checked=midpoint_recheck(fresh,lower,cfg,side,context or {},self.clock())
                price=dec(fresh[-1]['close'])
                payload=order_payload(symbol,side,cfg.position_value,price,instrument,identity+':'+symbol+':'+str(stamp))
                payload.update(stopLoss=stop_price(side,price,payload['qty'],cfg.stop_dollars,instrument['priceFilter']['tickSize']),slTriggerBy='MarkPrice',tpslMode='Full')
                if cfg.take_profit_enabled:payload.update(takeProfit=take_profit_price(side,price,payload['qty'],cfg.take_profit_dollars,instrument['priceFilter']['tickSize']),tpTriggerBy='MarkPrice',tpOrderType='Market')
                if cfg.trailing_enabled:
                    reference=price if cfg.trailing_distance_basis=='price' else price/cfg.leverage
                    if reference*dec(cfg.trailing_distance_pct)/100<dec(instrument['priceFilter']['tickSize']):raise ValueError('Trailing distance is smaller than one price tick')
                metadata.update(thoth_midpoint_window=int(self.clock())//3600*3600,thoth_entry_sources=checked['entry_sources'],thoth_volume_delta_decision=checked)
                if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'):
                    metadata['thoth_volume_window']=(context or {})['volume_window'];metadata.pop('thoth_midpoint_window',None)
                from .signal_stretch import snapshot
                history=(context or {}).get('source_history') if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit') else (context or {})['midpoint_history']
                metrics=snapshot(history[:-1]+[fresh[-1]],side,checked['entry_sources'])
                if metrics:
                    metrics.update(provisional=True,observed_at=self.clock(),atr_basis='Wilder ATR14 including forming hourly candle')
                    metadata['thoth_signal_stretch']=metrics
            if cfg.strategy=='order_block_sr_1h_early':
                from .early_hour import recheck,window
                check_timing['early_recheck_started_at']=self.clock()
                fresh=self.client.candles(symbol,'60',limit=2)
                checked=recheck(fresh,cfg,side,context or {},self.clock())
                check_timing['early_recheck_completed_at']=self.clock()
                price=dec(checked['price'])
                payload=order_payload(symbol,side,cfg.position_value,price,instrument,identity+':'+symbol+':'+str(stamp))
                payload.update(stopLoss=stop_price(side,price,payload['qty'],cfg.stop_dollars,instrument['priceFilter']['tickSize']),slTriggerBy='MarkPrice',tpslMode='Full')
                if cfg.take_profit_enabled:payload.update(takeProfit=take_profit_price(side,price,payload['qty'],cfg.take_profit_dollars,instrument['priceFilter']['tickSize']),tpTriggerBy='MarkPrice',tpOrderType='Market')
                if cfg.trailing_enabled:
                    reference=price if cfg.trailing_distance_basis=='price' else price/cfg.leverage
                    if reference*dec(cfg.trailing_distance_pct)/100<dec(instrument['priceFilter']['tickSize']):raise ValueError('Trailing distance is smaller than one price tick')
                metrics=checked['metrics'];metrics.update(observed_at=self.clock(),early_rules=checked['rules'])
                metadata['thoth_entry_sources']=checked['passed_sources']
                passed={(check['source'],check['boundary']) for check in checked['source_checks'] if check['passed']}
                metrics['references']=[ref for ref in metrics['references'] if (ref['source'],ref['price']) in passed]
                metadata['thoth_signal_stretch']=metrics
                metadata['thoth_entry_diagnostics'].update(check_timing)
                if not window(self.clock()) or self.clock()-stamp>30 or self._early_hour_used(symbol,cfg):raise ValueError('Early entry expired or hourly slot used during checks')
                bar=int(self.clock())//3600*3600
                metadata['thoth_early_window']=bar
                self.state['early_entry_windows']={s:w for s,w in self.state.get('early_entry_windows',{}).items() if w==bar}
                self.state['early_entry_windows'][symbol]=bar
                self.save()
            if cfg.strategy=='sr_volume_intrabar':
                window=int(self.clock())//14400*14400
                metadata['thoth_sr_volume_window']=window
                self.state.setdefault('sr_volume_entry_windows',{})[symbol]=window
                self.save()  # Reserve durably before any submission; an ambiguous outcome keeps the limit.
            if cfg.strategy=='ob_sr_minute_hourly':
                if int(stamp)//60!=int(self.clock())//60 or self.clock()-stamp>30:
                    raise ValueError('Fresh 1m entry expired during entry checks')
                window=int(self.clock())//3600*3600
                metadata['thoth_minute_hour_window']=window
                reason=self._minute_hour_block_reason(symbol,cfg)
                if reason:raise ValueError(reason)
                self.state['minute_hour_entries']={s:w for s,w in self.state.get('minute_hour_entries',{}).items() if w==window}
                self.state['minute_hour_entries'][symbol]=window
                self.save()  # Reserve before submission; an uncertain result retains the slot.
            if cfg.strategy in TWO_DIRECTION_STRATEGIES:
                from .two_direction_gate import reserve
                gate=self.state['entry_gates'][symbol]
                if not entry_fresh(stamp,context,self.clock()):raise ValueError('Fresh 1m direction expired during checks')
                if (context or {}).get('direction_index')!=len(gate['directions']):raise ValueError('Minute direction already consumed')
                metadata.update(thoth_permission_arm_id=gate['arm_id'],thoth_permission_armed_at=gate['armed_at'],thoth_direction_index=len(gate['directions']))
                reserve(gate,side,payload['orderLinkId'],(context or {})['arm_id']);self.save()
            if cfg.strategy==HOUR_GATE:
                gate=self.state['entry_gates'][symbol]
                metadata['thoth_permission_armed_at']=gate['armed_at']
                metadata['thoth_permission_arm_id']=gate['arm_id']
                self._consume_hour_permission(symbol,gate)
                metadata['thoth_permission_closed_bar']=gate['permission_closed_bar']
                self.save()  # Single-use permission reserved before submission/any crash.
            if cfg.strategy=='order_block_sr_1h_early' and (not window(self.clock()) or self.clock()-stamp>30):
                # Durable reservation itself may wait on disk: check again at the
                # submission boundary, not only before saving the reservation.
                self.state.get('early_entry_windows',{}).pop(symbol,None)
                raise ValueError('Early entry window or fresh signal expired before submission')
            scheduled=self._schedule_entry_reason(cfg)
            if scheduled:
                for key,value in reservations.items():
                    if value is None:self.state.pop(key,None)
                    else:self.state[key]=value
                raise ValueError(scheduled+'; window changed during entry checks')
            guard=(lambda:self._schedule_entry_reason(cfg)) if (cfg.entry_daily_enabled or cfg.entry_weekend_enabled or self.state.get('schedule_exit',{}).get('active')) else None
            if cfg.strategy=='order_block_sr_1h_early':
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    try:
                        latest=self.client.request('GET','/v5/market/tickers',dict(category='linear',symbol=symbol))['list'][0]['lastPrice']
                        final=direction_check(latest,side,(context or {}).get('sma200_baseline'),self.clock())
                        if not window(self.clock()) or self.clock()-stamp>30:return 'Early entry window or fresh signal expired before submission'
                        reason=self._schedule_entry_reason(cfg)
                        if reason:return reason
                        if not final['passed']:return final['reason']
                        self.journal.record_timing(payload['orderLinkId'],sma200_submission=final)
                        return None
                    except Exception:return '1h SMA200 entry blocked: fresh submission price unavailable'
            if cfg.strategy in ('volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit'):
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    if not midpoint_window(self.clock()) or not 0<=self.clock()-(context or {})['midpoint_observed_at']<=30:return 'Volume Delta :30–:31 window or fresh decision expired before submission'
                    return None
            if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit'):
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    if not window_fresh(context or {},cfg,self.clock()):return 'Volume Delta window, source hour or fresh decision expired before submission'
                    return None
            if cfg.strategy in TWO_DIRECTION_STRATEGIES:
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    current_gate=self.state['entry_gates'][symbol]
                    if current_gate['phase']!='armed' or current_gate['arm_id']!=(context or {}).get('arm_id'):return 'Minute permission expired before submission'
                    if not entry_fresh(stamp,context,self.clock()):return 'Minute direction expired before submission'
                    return None
            if cfg.strategy in DELAYED_STRATEGIES:
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    if not ready((context or {}).get('delayed_30m') or {},cfg,self.clock()):return 'Delayed :07 / :37 entry window expired before submission'
                    return None
            if cfg.strategy in ('trend_major_pullback_30m_close','trend_major_breakout_30m_close','major_pullback_30m_intrabar'):
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    if not 0<=self.clock()-stamp<=30:return trend_label+' signal expired before submission'
                    try:
                        latest=self.client.request('GET','/v5/market/tickers',dict(category='linear',symbol=symbol))['list'][0]['lastPrice']
                        checked=pullback_recheck(side,context,self.clock(),latest)
                        if not 0<=self.clock()-stamp<=30:return trend_label+' signal expired before submission'
                        reason=self._schedule_entry_reason(cfg)
                        if reason:return reason
                        self.journal.record_timing(payload['orderLinkId'],**{('major_pullback_intrabar_submission' if cfg.strategy=='major_pullback_30m_intrabar' else 'trend_breakout_submission' if cfg.strategy=='trend_major_breakout_30m_close' else 'trend_pullback_submission'):checked})
                        return None
                    except ValueError as exc:return str(exc)
                    except Exception:return trend_label+' fresh submission price unavailable'
            if cfg.strategy=='crypto_ob_strength_1h_close':
                def guard():
                    reason=self._schedule_entry_reason(cfg)
                    if reason:return reason
                    try:validate_entry(symbol,side,context,self.clock())
                    except ValueError as exc:return str(exc)
                    return None
            record=self.journal.submit(payload,cfg.stop_dollars,metadata,entry_guard=guard) if guard is not None else self.journal.submit(payload,cfg.stop_dollars,metadata)
            if cfg.strategy in TWO_DIRECTION_STRATEGIES and (record['phase']=='rejected' or record.get('result',{}).get('not_submitted')):
                gate=self.state['entry_gates'][symbol]
                if gate['directions'] and gate['directions'][-1]['link']==payload['orderLinkId']:gate['directions'].pop();self.save()
            if record['phase']=='cancelled' and record.get('result',{}).get('not_submitted'):
                for key,value in reservations.items():
                    if value is None:self.state.pop(key,None)
                    else:self.state[key]=value
                self._signal_decision(symbol,'skipped: '+record['result']['reason'],dict(time=stamp,signal=side))
                self.save();return
            if record['phase']=='rejected':
                if cfg.strategy=='order_block_sr_1h_early':self.state.get('early_entry_windows',{}).pop(symbol,None)
                if cfg.strategy==HOUR_GATE:self.state['entry_gates'][symbol]['phase']='armed'
                if cfg.strategy=='ob_sr_minute_hourly':self.state.get('minute_hour_entries',{}).pop(symbol,None)
                if cfg.strategy=='sr_volume_intrabar':self.state.get('sr_volume_entry_windows',{}).pop(symbol,None)
                code=record.get('result',{}).get('exchange_code')
                self.event(symbol+': entry rejected by Bybit'+(' (code '+str(code)+')' if code is not None else '')+'; skipped this coin, other coins continue')
                self.save();return
            self.event(symbol+(': CLOSED 1h per-coin strength Order Block' if cfg.strategy=='crypto_ob_strength_1h_close' else ': 30m Major pullback / CLOSED 15m confirmation; NO SMA' if cfg.strategy=='major_pullback_30m_intrabar' else ': CLOSED 1h trend / CLOSED 30m Major breakout' if cfg.strategy=='trend_major_breakout_30m_close' else ': CLOSED 1h trend / CLOSED 30m Major pullback' if cfg.strategy=='trend_major_pullback_30m_close' else ': Intrabar 4h/Daily S/R breakout or bounce/rejection' if cfg.strategy=='sr_4h_daily_unfiltered' else ': CLOSED 30m price / SMA50 cross' if cfg.strategy=='price_sma50_30m_close' else ': Delayed CLOSED 30m :07 / :37 S/R / RSI' if cfg.strategy==RSI_STRATEGY else ': Delayed CLOSED 30m :07 / :37 Order Block / Major S/R' if cfg.strategy in DELAYED_STRATEGIES else ': CLOSED 30m permission / CLOSED 1m two-direction Order Block / S/R' if cfg.strategy==CLOSED_TWO_DIRECTION else ': 1h window permission / 1m two-direction Order Block / S/R' if cfg.strategy in TWO_DIRECTION_STRATEGIES else ': 1h Volume Delta :28/:58 window decision' if cfg.strategy in ('volume_delta_1h_windows_sma7_14_exit','volume_delta_1h_windows_no_sma_exit') else ': 1h Volume Delta :30 decision' if cfg.strategy in ('volume_delta_1h_midpoint','volume_delta_1h_midpoint_sma7_exit') else ': Early 1h Order Block / S/R :55–:00 signal' if cfg.strategy=='order_block_sr_1h_early' else ': Intrabar 1m Order Block / S/R hourly-limited entry' if cfg.strategy=='ob_sr_minute_hourly' else ': CLOSED 1h permission / intrabar 1m Order Block or S/R' if cfg.strategy==HOUR_GATE else ': 1h CLOSED NORMAL Order Block / S/R signal' if cfg.strategy=='order_block_sr_1h_close' else ': 1h CLOSED INVERTED Order Block / S/R signal' if cfg.strategy=='order_block_sr_1h_inverted_close' else ': '+('1h' if cfg.timeframe=='60' else '4h')+' Order Block / S/R volume-qualified signal' if cfg.strategy in ('order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close','order_block_sr_1h_early') else ': 4h Order Block intrabar signal' if cfg.strategy=='order_block_intrabar' else ': 4h SMA 7/50 intrabar crossover' if cfg.strategy=='sma_7_50_intrabar' else ': S/R volume MA breakout' if cfg.strategy=='sr_volume_intrabar' else ': Two-stage Order Block confirmation' if cfg.strategy==ORDER_BLOCK_STRATEGY else ': Two-stage Volume Delta confirmation' if cfg.strategy in STAGED_STRATEGIES else ': Volume Delta signal' if cfg.strategy=='volume_delta_intrabar' else ': Inverse S/R signal' if cfg.strategy=='sr_inverse_intrabar' else ': Daily S/R + fixed RSI signal' if cfg.strategy=='sr_rsi_daily' else ': RSI intrabar signal' if cfg.strategy=='rsi_hourly_intrabar' else ': SMA 7/50 crossover' if cfg.strategy=='sma_7_50' else ': S/R + SMA 7/50 signal' if cfg.strategy=='sr_sma_7_50' else ': SMA 7/200 crossover' if cfg.strategy=='sma_cross' else ': intrabar S/R breakout' if cfg.strategy in HOURLY_MINUTE_STRATEGIES else ': S/R breakout')+' order submitted; awaiting fill/protection')
        except ValueError as exc:
            self._signal_decision(symbol,'entry skipped: '+str(exc),{'time':stamp,'signal':side})
        except AmbiguousOrder:
            self._lock('safety_lock','Entry operation outcome unavailable; reconcile before enabling entries')
        except BybitError as exc:
            self._signal_decision(symbol,'entry rejected by Bybit'+(' (code '+str(exc.code)+')' if exc.code is not None else '')+'; skipped this coin, other coins continue',{'time':stamp,'signal':side})
        except Exception:self._lock('safety_lock','Entry operation outcome unavailable; reconcile before enabling entries')
        self.save()

    def _drain(self,cfg):
        for symbol,intent in list(self.state['pending_signals'].items()):
            scheduled=self._schedule_entry_reason(cfg)
            if scheduled:
                self._signal_decision(symbol,'skipped: '+scheduled,dict(time=intent['time'],signal=intent['side']))
                del self.state['pending_signals'][symbol];continue
            if symbol_paused(self.state['risk'],symbol,cfg,self.clock()):
                self._signal_decision(symbol,'skipped: per-coin consecutive-loss cooldown',{'time':intent['time'],'signal':intent['side']})
                del self.state['pending_signals'][symbol];continue
            if self.clock()-intent['time']>(30 if cfg.strategy in SHORT_LIVED_SIGNAL_STRATEGIES else 2*interval_seconds(cfg.timeframe)):
                reason='maximum positions reached' if len(self.state['owned'])>=cfg.max_positions else 'waiting for prior orders or entry checks'
                self._signal_decision(symbol,'expired in entry queue: '+reason,{'time':intent['time'],'signal':intent['side']})
                del self.state['pending_signals'][symbol];continue
            if self.state['flatten_reason'] or self.state['status']!='running' or not self.state['entries'] or len(self.state['owned'])>=cfg.max_positions:return
            if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
            del self.state['pending_signals'][symbol]
            self._entry(symbol,intent['side'],intent['time'],intent['identity'],cfg,intent.get('context'))
            return

    def _reversals(self,cfg):
        for symbol,reversal in list(self.state['reversals'].items()):
            scheduled=self._schedule_entry_reason(cfg)
            if scheduled:
                del self.state['reversals'][symbol]
                self._signal_decision(symbol,'replacement entry skipped: '+scheduled,dict(time=reversal['time'],signal=reversal['side']))
                continue
            if symbol_paused(self.state['risk'],symbol,cfg,self.clock()):
                del self.state['reversals'][symbol]
                self.event(symbol+": replacement entry blocked by this coin’s consecutive-loss cooldown")
                continue
            if self.clock()-reversal['time']>(30 if cfg.strategy in SHORT_LIVED_SIGNAL_STRATEGIES else 2*interval_seconds(cfg.timeframe)):
                del self.state['reversals'][symbol]
                self.event(symbol+': reverse signal expired; no replacement position opened')
                continue
            if symbol in self.state['owned']:continue
            if self.state['flatten_reason'] or self.state['status']!='running' or not self.state['entries']:continue
            if any(r['phase'] in ('prepared','unknown','submitted','partial','stop_error','mismatch') for r in self.journal.records()):return
            if len(self.state['owned'])>=cfg.max_positions:continue
            del self.state['reversals'][symbol]
            self._entry(symbol,reversal['side'],reversal['time'],reversal['identity'],cfg,reversal.get('context'))

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
