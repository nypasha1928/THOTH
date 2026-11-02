"""SQLite transactions that release file handles at scope exit, not at GC time."""
import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,exc_type,exc_value,traceback):
        try:
            return super().__exit__(exc_type,exc_value,traceback)
        finally:
            self.close()


def _resources(path):
    """Best effort numbers only; never put paths or raw exception text in reports."""
    import os
    import shutil
    from pathlib import Path
    result={}
    try:result['open_fds']=len(os.listdir('/proc/self/fd'))
    except OSError:pass
    try:
        import resource
        soft,_=resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft>=0:result['fd_soft_limit']=soft
    except (ImportError,OSError,ValueError):pass
    try:
        value=os.fsdecode(path)
        if value.startswith('file:'):
            from urllib.parse import unquote
            value=unquote(value[5:].split('?',1)[0])
        result['disk_free_bytes']=shutil.disk_usage(Path(value).resolve().parent).free
    except (OSError,ValueError,TypeError):pass
    return result


def connect(*args,**kwargs):
    kwargs['factory']=ClosingConnection
    try:return sqlite3.connect(*args,**kwargs)
    except sqlite3.Error as exc:
        exc.thoth_db_resources=_resources(args[0] if args else kwargs.get('database'))
        raise
