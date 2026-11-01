"""Opt-in Web Push for account entry pauses; delivery never runs on a trading thread."""

from .database import connect
import base64
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import webpush, WebPushException
from py_vapid import Vapid
import requests


def encode(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')


def subscription(value):
    if not isinstance(value,dict):raise ValueError('Invalid push subscription')
    endpoint=value.get('endpoint','')
    if not isinstance(endpoint,str) or len(endpoint)>2048:raise ValueError('Invalid push endpoint')
    url=urlsplit(endpoint);host=url.hostname or ''
    allowed=(host in ('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com')
        or host.endswith('.push.apple.com') or host.endswith('.notify.windows.com'))
    if url.scheme!='https' or not allowed or url.port not in (None,443) or url.username or url.password or url.fragment:
        raise ValueError('Unsupported browser push provider')
    keys=value.get('keys',{})
    if not isinstance(keys,dict):raise ValueError('Invalid push keys')
    decoded={}
    for name,length in (('p256dh',65),('auth',16)):
        text=keys.get(name,'')
        if not isinstance(text,str) or len(text)>128:raise ValueError('Invalid push key')
        try:raw=base64.b64decode(text+'='*(-len(text)%4),altchars=b'-_',validate=True)
        except Exception:raise ValueError('Invalid push key') from None
        if len(raw)!=length:raise ValueError('Invalid push key')
        decoded[name]=encode(raw)
    try:ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),base64.urlsafe_b64decode(decoded['p256dh']+'='*(-len(decoded['p256dh'])%4)))
    except ValueError:raise ValueError('Invalid push public key') from None
    return dict(endpoint=endpoint,keys=decoded)


class NoRedirectSession(requests.Session):
    def post(self,*args,**kwargs):
        kwargs['allow_redirects']=False
        return super().post(*args,**kwargs)


class PushNotifications:
    def __init__(self,path,supervisor_path,subject,clock=time.time,sender=None):
        self.path=str(path);self.supervisor_path=str(supervisor_path);self.subject=subject;self.clock=clock
        self.sender=sender or self._send;self.stop_event=threading.Event();self.thread=None
        self.lock=threading.RLock()
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created REAL NOT NULL,last_status TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY,device TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,next_attempt REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,UNIQUE(device,event))')
            if not db.execute("SELECT 1 FROM metadata WHERE key='private_key'").fetchone():
                key=ec.generate_private_key(ec.SECP256R1())
                pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
                db.execute('INSERT INTO metadata VALUES(?,?)',('private_key',pem))
            pem=db.execute("SELECT value FROM metadata WHERE key='private_key'").fetchone()[0]
        self.private_key=pem
        key=serialization.load_pem_private_key(pem.encode(),password=None)
        self.public_key=encode(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint))

    def db(self):return connect(self.path,timeout=5)

    def register(self,value):
        value=subscription(value);identity=hashlib.sha256(value['endpoint'].encode()).hexdigest()
        with self.db() as db:
            old=db.execute('SELECT id FROM devices WHERE id=?',(identity,)).fetchone()
            if not old and db.execute('SELECT COUNT(*) FROM devices').fetchone()[0]>=20:raise ValueError('Maximum 20 notification devices; remove an unused device first')
            db.execute("INSERT INTO devices VALUES(?,?,?,'registered') ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (identity,json.dumps(value),self.clock()))
        return identity

    def remove(self,value):
        value=subscription(value);identity=hashlib.sha256(value['endpoint'].encode()).hexdigest()
        with self.db() as db:
            db.execute('DELETE FROM devices WHERE id=?',(identity,))
            db.execute('DELETE FROM deliveries WHERE device=?',(identity,))

    def status(self):
        with self.db() as db:
            devices=[dict(id=row[0],status=row[1]) for row in db.execute('SELECT id,last_status FROM devices')]
        with connect('file:'+self.supervisor_path+'?mode=ro',uri=True,timeout=2) as source:
            pauses=[dict(id=row[0],time=row[1],**json.loads(row[2])) for row in source.execute('SELECT id,time,payload FROM entry_pauses ORDER BY id DESC LIMIT 50')]
        return dict(public_key=self.public_key,devices=devices,recent_pauses=pauses)

    def test(self,value):
        value=subscription(value);identity=hashlib.sha256(value['endpoint'].encode()).hexdigest()
        with self.db() as db:
            if not db.execute('SELECT 1 FROM devices WHERE id=?',(identity,)).fetchone():raise ValueError('Enable notifications on this device first')
            recent=db.execute("SELECT created FROM deliveries WHERE device=? AND event LIKE 'test:%' ORDER BY created DESC LIMIT 1",(identity,)).fetchone()
            if recent and self.clock()-recent[0]<30:raise ValueError('Wait 30 seconds before another test')
            now=self.clock()
            db.execute("INSERT INTO deliveries(device,event,payload,created,next_attempt,status) VALUES(?,?,?,?,?,'pending')",
                (identity,'test:'+str(now),json.dumps(dict(title='THOTH test alert',body='Phone notifications are connected. This test does not change trading.')),now,now))

    def capture(self):
        with self.db() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='pause_cursor'").fetchone();cursor=int(row[0]) if row else 0
            with connect('file:'+self.supervisor_path+'?mode=ro',uri=True,timeout=2) as source:
                events=source.execute('SELECT id,time,payload FROM entry_pauses WHERE id>? ORDER BY id LIMIT 100',(cursor,)).fetchall()
            for identity,created,payload in events:
                # A newly subscribed device must not receive old pauses.
                for device, in db.execute('SELECT id FROM devices WHERE created<=?',(created,)).fetchall():
                    db.execute("INSERT OR IGNORE INTO deliveries(device,event,payload,created,next_attempt,status) VALUES(?,?,?,?,?,'pending')",
                        (device,'pause:'+str(identity),payload,created,self.clock()))
                cursor=identity
            db.execute("INSERT OR REPLACE INTO metadata VALUES('pause_cursor',?)",(str(cursor),))
            db.execute('DELETE FROM deliveries WHERE created<?',(self.clock()-86400,))

    def _send(self,value,payload):
        with NoRedirectSession() as session:
            session.trust_env=False
            return webpush(subscription_info=value,data=json.dumps(payload),vapid_private_key=Vapid.from_pem(self.private_key.encode()),
                vapid_claims={'sub':self.subject},ttl=3600,timeout=8,requests_session=session)

    def deliver(self):
        with self.db() as db:
            rows=db.execute("SELECT d.id,d.device,d.payload,d.attempts,v.payload FROM deliveries d JOIN devices v ON v.id=d.device WHERE d.status='pending' AND d.next_attempt<=? ORDER BY d.id LIMIT 10",(self.clock(),)).fetchall()
        for identity,device,payload,attempts,value in rows:
            if self.stop_event.is_set():break
            state='accepted';retry=False;expired=False
            try:
                response=self.sender(json.loads(value),json.loads(payload))
                if response is not None and not 200<=response.status_code<300:
                    expired=response.status_code in (404,410);retry=not expired;state='expired' if expired else 'delivery_failed'
            except WebPushException as exc:
                expired=exc.response is not None and exc.response.status_code in (404,410)
                state='expired' if expired else 'delivery_failed';retry=not expired
            except Exception:
                state='delivery_failed';retry=True
            with self.db() as db:
                if expired:
                    db.execute('DELETE FROM devices WHERE id=?',(device,))
                    db.execute('DELETE FROM deliveries WHERE device=?',(device,))
                else:
                    db.execute('UPDATE devices SET last_status=? WHERE id=?',(state,device))
                    db.execute('UPDATE deliveries SET status=?,attempts=?,next_attempt=? WHERE id=?',
                        ('pending' if retry else 'accepted',attempts+1,self.clock()+min(300,30*2**min(attempts,4)),identity))

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear()
        def run():
            while not self.stop_event.is_set():
                try:self.capture();self.deliver()
                except Exception:pass  # Notification failures must not stop risk management.
                self.stop_event.wait(2)
        self.thread=threading.Thread(target=run,name='thoth-phone-alerts',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=10)
