"""Private single-owner dashboard security, independent of exchange state."""
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import struct
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode
from cryptography.fernet import Fernet


def password_hash(password):
    if not isinstance(password,str) or not 16<=len(password)<=256:
        raise ValueError('Use a password or passphrase of 16–256 characters')
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1)
    return salt.hex()+':'+digest.hex()


def password_matches(password,encoded,bootstrap):
    if not isinstance(password,str) or len(password)>256:return False
    if not encoded:
        return bool(bootstrap) and hmac.compare_digest(hashlib.sha256(password.encode()).digest(),hashlib.sha256(bootstrap.encode()).digest())
    try:
        salt,expected=encoded.split(':')
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1)
        return hmac.compare_digest(actual.hex(),expected)
    except (ValueError,TypeError):return False


def totp(secret,step):
    key=base64.b32decode(secret)
    digest=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest()
    offset=digest[-1]&15
    return str((int.from_bytes(digest[offset:offset+4],'big')&0x7fffffff)%1000000).zfill(6)


def code_hash(code):
    value=str(code).strip().upper().replace('-','').replace(' ','')
    return hashlib.sha256(value.encode()).hexdigest()


def new_codes(state):
    codes=[]
    for _ in range(10):
        raw=secrets.token_hex(16).upper()
        codes.append('THOTH-'+'-'.join(raw[i:i+8] for i in range(0,32,8)))
    state['recovery']=[code_hash(c) for c in codes]
    return codes


class SecurityStore:
    def __init__(self,directory,clock=time.time):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.directory/'security.sqlite';self.clock=clock
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
            initial=dict(password=None,revision=0,totp_secret=None,totp_last_step=-1,recovery=[],passkeys=[],user_id=secrets.token_hex(32))
            db.execute('INSERT OR IGNORE INTO owner VALUES(1,?)',(json.dumps(initial),))
            db.execute('CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,purpose TEXT,payload TEXT,expires REAL)')
        self.path.chmod(0o600)

    def db(self):return sqlite3.connect(self.path,timeout=15)

    @contextmanager
    def edit(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT payload FROM owner WHERE id=1').fetchone()[0])
            yield state,db
            db.execute('UPDATE owner SET payload=? WHERE id=1',(json.dumps(state),))

    def read(self):
        with self.db() as db:return json.loads(db.execute('SELECT payload FROM owner WHERE id=1').fetchone()[0])

    def cipher(self):
        path=self.directory/'security.key'
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:pass
        else:
            with os.fdopen(fd,'wb') as stream:stream.write(Fernet.generate_key());stream.flush();os.fsync(stream.fileno())
        return Fernet(path.read_bytes())

    def authenticate(self,state,password,code,bootstrap):
        if not password_matches(password,state['password'],bootstrap):raise ValueError('Authentication failed')
        if state['totp_secret']:self.second_factor(state,code)

    def initialize(self,state,bootstrap):
        if not state['password']:state['password']=password_hash(bootstrap)

    def second_factor(self,state,code):
        code=str(code).strip()
        if len(code)==6 and code.isascii() and code.isdigit():
            secret=self.cipher().decrypt(state['totp_secret'].encode()).decode()
            step=int(self.clock()//30)
            for candidate in (step-1,step,step+1):
                if candidate>=0 and candidate>state['totp_last_step'] and hmac.compare_digest(totp(secret,candidate),code):
                    state['totp_last_step']=candidate;return
        digest=code_hash(code)
        for saved in state['recovery']:
            if hmac.compare_digest(saved,digest):state['recovery'].remove(saved);return
        raise ValueError('Authentication failed; use a fresh authenticator code or unused recovery code')

    def challenge(self,db,purpose,payload,seconds=300):
        db.execute('DELETE FROM challenges WHERE expires<?',(self.clock(),))
        identifier=secrets.token_urlsafe(32)
        db.execute('INSERT INTO challenges VALUES(?,?,?,?)',(identifier,purpose,json.dumps(payload),self.clock()+seconds))
        return identifier

    def consume(self,db,identifier,purpose):
        row=db.execute('SELECT purpose,payload,expires FROM challenges WHERE id=?',(identifier,)).fetchone()
        if not row or row[0]!=purpose or row[2]<self.clock():raise ValueError('Request expired; start again')
        db.execute('DELETE FROM challenges WHERE id=?',(identifier,))
        return json.loads(row[1])

    def status(self):
        state=self.read()
        return dict(password_configured=bool(state['password']),totp_enabled=bool(state['totp_secret']),recovery_codes_remaining=len(state['recovery']),passkeys=[dict(id=p['id'],name=p['name'],created=p['created']) for p in state['passkeys']])

    def qr(self,secret,host):
        import qrcode
        import qrcode.image.svg
        uri='otpauth://totp/'+quote('THOTH:'+host,safe='')+'?'+urlencode(dict(secret=secret,issuer='THOTH',algorithm='SHA1',digits=6,period=30))
        qr=qrcode.make(uri,image_factory=qrcode.image.svg.SvgPathImage,box_size=8,border=4)
        stream=io.BytesIO();qr.save(stream)
        return 'data:image/svg+xml;base64,'+base64.b64encode(stream.getvalue()).decode()
