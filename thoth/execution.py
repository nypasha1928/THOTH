"""Durable TESTNET submission journal; deliberately not wired to app controls.

One process per journal. Persist intent before sending; any interrupted or
ambiguous submission is reconciled rather than resubmitted. Account-level risk,
permission/margin preflight and an activation flow must wrap this component
before it can be connected to an actual testnet account.
"""
import json
import sqlite3
import threading
from pathlib import Path

from .bybit_testnet import AmbiguousOrder, BybitError, dec, stop_price
from .diagnostics import protection_details


class ExecutionJournal:
    def __init__(self, path, client, environment='testnet'):
        expected={'testnet':'https://api-testnet.bybit.com','live':'https://api.bybit.com'}.get(environment)
        if expected is None or client.BASE != expected:
            raise ValueError('Exchange endpoint does not match the explicitly selected environment')
        self.client=client
        self.path=str(path)
        self.lock=threading.RLock()
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS execution (link TEXT PRIMARY KEY, payload TEXT NOT NULL, phase TEXT NOT NULL, stop_dollars TEXT, instrument TEXT, result TEXT NOT NULL)')

    def db(self):
        return sqlite3.connect(self.path,timeout=15)

    def records(self):
        with self.db() as db:
            links=[r[0] for r in db.execute('SELECT link FROM execution ORDER BY rowid')]
        return [self.get(link) for link in links]

    def get(self, link):
        with self.db() as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM execution WHERE link=?',(link,)).fetchone()
        if row is None:raise ValueError('Unknown execution identifier')
        record=dict(row)
        for key in ('payload','result','instrument'):
            record[key]=json.loads(record[key]) if record[key] is not None else None
        return record

    def update(self,link,phase,result=None):
        # Results are curated local metadata, never a raw upstream account response.
        with self.db() as db:
            previous=db.execute('SELECT result FROM execution WHERE link=?',(link,)).fetchone()
            result=dict(result or {})
            if previous and 'protection_error' in json.loads(previous[0]):result.setdefault('protection_error',json.loads(previous[0])['protection_error'])
            db.execute('UPDATE execution SET phase=?, result=? WHERE link=?',
                       (phase,json.dumps(result or {}),link))
        return self.get(link)

    def submit(self,payload,stop_dollars=None,instrument=None):
        with self.lock:
            link=payload.get('orderLinkId')
            if not link or not payload.get('symbol'):raise ValueError('Missing execution identity')
            if not self.client.armed:raise BybitError('Testnet execution is not armed')
            if not payload.get('reduceOnly') and (stop_dollars is None or dec(stop_dollars)<=0 or not instrument):
                raise ValueError('Entry requires a protective dollar stop and instrument metadata')
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                existing=db.execute('SELECT payload FROM execution WHERE link=?',(link,)).fetchone()
                if existing:
                    if json.loads(existing[0])!=payload:raise ValueError('Identifier reused for a different order')
                else:
                    unresolved=db.execute("SELECT 1 FROM execution WHERE phase IN ('prepared','unknown','submitted','partial','stop_error','mismatch') LIMIT 1").fetchone()
                    if unresolved and not payload.get('reduceOnly'):raise ValueError('Resolve outstanding execution before another submission')
                    db.execute('INSERT INTO execution VALUES(?,?,?,?,?,?)',(link,json.dumps(payload),'prepared',
                        str(stop_dollars) if stop_dollars is not None else None,json.dumps(instrument), '{}'))
            if existing:
                return self.poll(link)  # Never submit an existing intent again.
            try:
                response=self.client.submit(payload)
            except AmbiguousOrder:
                return self.update(link,'unknown',{'reason':'Submission outcome unknown; reconcile before continuing'})
            except BybitError as exc:
                return self.update(link,'rejected',{'reason':'Exchange rejected submission','exchange_code':exc.code})
            except Exception:
                return self.update(link,'unknown',{'reason':'Submission interrupted; reconcile before continuing'})
            if response['state']=='reconciled':
                return self.poll(link)
            return self.update(link,'submitted',{'reason':'Accepted is not filled'})

    def poll(self,link):
        with self.lock:
            record=self.get(link)
            if record['phase'] in ('protected','closed','rejected','cancelled'):
                return record
            payload=record['payload'];symbol=payload['symbol']
            try:
                order=self.client.reconcile(symbol,link)
            except Exception:
                return self.update(link,'unknown',{'reason':'Reconciliation unavailable'})
            if not order:
                # A missing response is not proof that no order was submitted.
                return self.update(link,'unknown',{'reason':'Order not found; investigate history before any resubmission'})
            status=order.get('orderStatus')
            try:
                qty=dec(order.get('cumExecQty','0'))
                if not qty.is_finite() or qty<0:raise ValueError()
            except Exception:
                return self.update(link,'unknown',{'reason':'Invalid exchange fill metadata'})
            terminal=status in ('Filled','Cancelled','PartiallyFilledCanceled','Rejected','Deactivated')
            if qty==0:
                return self.update(link,'cancelled' if terminal else 'submitted',{'exchange_status':status})
            if not terminal:
                # Cancel any remainder, then reconcile the final fill before sizing its stop.
                try:
                    self.client.request('POST','/v5/order/cancel',dict(category='linear',symbol=symbol,orderLinkId=link),private=True)
                except Exception:
                    return self.update(link,'unknown',{'reason':'Partial-fill cancellation not confirmed'})
                return self.update(link,'partial',{'reason':'Remainder cancel requested; final fill reconciliation required'})
            try:
                positions=self.client.request('GET','/v5/position/list',dict(category='linear',symbol=symbol),private=True)['list']
                active=[p for p in positions if dec(p.get('size','0'))!=0]
            except Exception:
                return self.update(link,'unknown',{'reason':'Cannot verify exchange position'})
            if payload.get('reduceOnly'):
                # A reduce-only order fill does not prove the whole position is flat.
                if active:return self.update(link,'mismatch',{'reason':'Close filled but exchange position is still open'})
                return self.update(link,'closed',{'exchange_status':status,'filled_qty':str(qty)})
            if len(active)!=1 or active[0].get('side')!=payload['side'] or active[0].get('positionIdx')!=0 or dec(active[0]['size'])!=qty:
                return self.update(link,'mismatch',{'reason':'Position differs from owned one-way fill; manual reconciliation required'})
            stage='validate_protection';check='entry_price'
            values=dict(filled_qty=qty,stop_dollars=record['stop_dollars'])
            try:
                entry=dec(active[0]['avgPrice'])
                values['entry']=entry
                if not entry.is_finite() or entry<=0:raise ValueError()
                check='filled_margin'
                leverage=dec(active[0].get('leverage') or '0')
                values['leverage']=leverage
                if leverage>0:values['filled_margin']=entry*qty/leverage
                if leverage>0 and dec(record['stop_dollars'])>=entry*qty/leverage:
                    raise ValueError('Partial fill margin is too small for requested dollar stop')
                check='stop_price'
                values['tick_size']=record['instrument']['priceFilter']['tickSize']
                expected_stop=dec(stop_price('Long' if payload['side']=='Buy' else 'Short',entry,qty,record['stop_dollars'],record['instrument']['priceFilter']['tickSize']))
                values['expected_stop']=expected_stop
                check='liquidation_boundary'
                liquidation=dec(active[0].get('liqPrice') or '0')
                values['liquidation_price']=liquidation
                if liquidation>0 and ((payload['side']=='Buy' and expected_stop<=liquidation) or (payload['side']=='Sell' and expected_stop>=liquidation)):
                    raise ValueError('Stop is beyond liquidation')
                # Initial entries already attach a stop. Verify it before
                # attempting an update; unchanged stops are not a failure.
                check='existing_stop'
                values['reported_stop']=active[0].get('stopLoss','0')
                if dec(active[0].get('stopLoss','0'))==expected_stop:
                    return self.update(link,'protected',{'exchange_status':status,'filled_qty':str(qty),'protection_source':'existing_exchange_stop'})
                stage='attach_protection';check='attach_stop'
                try:
                    self.client.attach_stop(symbol,'Long' if payload['side']=='Buy' else 'Short',
                        entry,qty,record['stop_dollars'],record['instrument'])
                except BybitError as exc:
                    if exc.code!=34040:raise
                    # Not-modified acknowledgement is accepted only after the
                    # owned position's stop is verified by a separate read.
                stage='verify_protection';check='verify_stop'
                # Re-read after acknowledgement: verify the exchange actually reports a stop.
                confirmed=self.client.request('GET','/v5/position/list',dict(category='linear',symbol=symbol),private=True)['list']
                matching=[p for p in confirmed if p.get('positionIdx')==0 and p.get('side')==payload['side'] and dec(p.get('size','0'))==qty]
                expected=dec(stop_price('Long' if payload['side']=='Buy' else 'Short',entry,qty,record['stop_dollars'],record['instrument']['priceFilter']['tickSize']))
                if len(matching)==1:values['reported_stop']=matching[0].get('stopLoss','0')
                if len(matching)!=1 or dec(matching[0].get('stopLoss','0'))!=expected:
                    raise ValueError('Expected stop not confirmed')
            except Exception as exc:
                return self.update(link,'stop_error',{'reason':'Protective stop unconfirmed; block new submissions and investigate immediately','protection_error':protection_details(exc,stage,check,values)})
            return self.update(link,'protected',{'exchange_status':status,'filled_qty':str(qty)})
