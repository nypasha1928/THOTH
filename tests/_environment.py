"""Keep unit/API tests away from deployed databases and exchange credentials.

Loaded before thoth.app by every API/auth test module. Changes affect only this
separate test process, not a running server. No existing secret values are read,
printed or copied. The temporary application directory is removed on exit.
"""
import os
import tempfile

_directory=tempfile.TemporaryDirectory(prefix='thoth-unit-app-')
os.environ['THOTH_DB']=os.path.join(_directory.name,'paper.sqlite')
os.environ['THOTH_ENABLE_TESTNET']='0'
os.environ['THOTH_ENABLE_LIVE']='0'
os.environ['THOTH_PASSWORD']=''
os.environ['THOTH_PUBLIC_HOST']=''
os.environ.pop('THOTH_PASSWORD_FILE',None)
for name in tuple(os.environ):
    if name.startswith('THOTH_BYBIT_'):os.environ.pop(name,None)
