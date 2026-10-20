"""Private multi-database backups and verified restore for THOTH.

Stop the app first for a consistent application-level point in time. Each
SQLite file is copied through its online backup API and checked independently.
SQLite account/candle/journal/security files and the dedicated security encryption
key are included. Exchange API credential files are never included. Protect these
backups as login secrets; restore security.sqlite and security.key together.
"""
import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime,timezone
from pathlib import Path


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def private_write(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as output:output.write(data)


def backup(source,destination):
    source=Path(source).resolve();destination=Path(destination)
    files=sorted(source.glob('*.sqlite'))
    if not files:raise ValueError('No THOTH SQLite databases found')
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    manifest={'format':1,'created_at':datetime.now(timezone.utc).isoformat(),'databases':[]}
    for path in files:
        if path.is_symlink():raise ValueError('Refusing symlink database')
        target=destination/path.name;private_write(target,b'')
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(target) as copy:
            original.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
        manifest['databases'].append({'file':path.name,'sha256':digest(target),'bytes':target.stat().st_size})
    key=source/'security.key'
    if (source/'security.sqlite').exists() and not key.exists():
        with sqlite3.connect((source/'security.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            security=json.loads(db.execute('SELECT payload FROM owner WHERE id=1').fetchone()[0])
        if security.get('totp_secret'):raise ValueError('Authenticator encryption key missing; backup is incomplete')
    if (source/'security.sqlite').exists() and key.exists():
        if key.is_symlink() or not key.is_file():raise ValueError('Invalid security encryption key')
        private_write(destination/'security.key',key.read_bytes())
        manifest['security_key']={'file':'security.key','sha256':digest(destination/'security.key'),'bytes':(destination/'security.key').stat().st_size}
    # Manifest written last: partial copies without it are never valid backups.
    private_write(destination/'manifest.json',(json.dumps(manifest,indent=2)+'\n').encode())
    return manifest


def verify(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest.get('format')!=1 or not manifest.get('databases'):raise ValueError('Unsupported or empty backup manifest')
    seen=set()
    for item in manifest['databases']:
        name=item['file']
        if Path(name).name!=name or not name.endswith('.sqlite') or name in seen:raise ValueError('Invalid backup filename')
        seen.add(name);path=directory/name
        if path.is_symlink() or not path.is_file() or digest(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:
            raise ValueError('Backup checksum/size verification failed')
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup SQLite verification failed')
    key=manifest.get('security_key')
    if key:
        path=directory/'security.key'
        if key.get('file')!='security.key' or path.is_symlink() or not path.is_file() or digest(path)!=key['sha256'] or path.stat().st_size!=key['bytes']:
            raise ValueError('Security key verification failed')
    return manifest


def restore(source,destination):
    source=Path(source);manifest=verify(source);destination=Path(destination)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for item in manifest['databases']:
        target=destination/item['file'];private_write(target,b'')
        with sqlite3.connect((source/item['file']).resolve().as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(target) as copy:
            original.backup(copy)
    if manifest.get('security_key'):private_write(destination/'security.key',(source/'security.key').read_bytes())
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','verify','restore']);parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path,nargs='?')
    args=parser.parse_args()
    if args.action=='verify':result=verify(args.source)
    else:
        if args.destination is None:parser.error('A new destination directory is required')
        result=backup(args.source,args.destination) if args.action=='backup' else restore(args.source,args.destination)
    print(json.dumps({'status':'verified','database_count':len(result['databases'])}))
