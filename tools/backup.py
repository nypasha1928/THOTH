"""Consistent SQLite backup for local administrators; never an HTTP download.

Usage: python tools/backup.py /path/to/paper.sqlite /private/backups/run-name
Includes optional companion candle history. Refuses overwriting existing output.
Keep backups private: they contain trading history and configuration.
"""
import argparse
import os
import sqlite3
from pathlib import Path


def backup(source, destination):
    source=Path(source).resolve()
    destination=Path(destination)
    if not source.is_file():raise ValueError('Source database does not exist')
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    try:
        for path in (source,source.with_suffix('.candles.sqlite')):
            if not path.exists():continue
            target=destination/path.name
            # Exclusive creation and private mode before writing any data.
            descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(descriptor)
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as original, sqlite3.connect(target) as copy:
                original.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Backup integrity verification failed')
        return destination
    except Exception:
        # Keep partial output for diagnosis; do not claim it is a completed backup.
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    backup(args.source,args.destination)
    print('SQLite backup completed and integrity checked; protect the destination directory.')
