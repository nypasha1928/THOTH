"""SSH-owner-only dashboard reset. Never import the app or alter trading data.
Run interactively inside the app container as its normal UID:
    docker exec -it --user 10001 thoth-app-1 python -m thoth.security_recovery
"""
import getpass
import os
from pathlib import Path
from .security import SecurityStore,password_hash


def main():
    os.umask(0o077)
    first=getpass.getpass('New dashboard password (16–256 characters): ')
    second=getpass.getpass('Repeat new dashboard password: ')
    if first!=second:raise SystemExit('Passwords do not match; nothing changed')
    hashed=password_hash(first)
    directory=Path(os.environ.get('THOTH_DB',str(Path(__file__).resolve().parent.parent/'data'/'live.sqlite'))).parent
    store=SecurityStore(directory)
    with store.edit() as (state,db):
        state.update(password=hashed,revision=state['revision']+1,totp_secret=None,totp_last_step=-1,recovery=[],passkeys=[])
        db.execute('DELETE FROM challenges')
    print('Dashboard access reset. Authenticator, passkeys and recovery codes removed; older sessions invalidated. Sign in with the new password, then save fresh recovery codes. Trading state was not opened or changed.')


if __name__=='__main__':main()
