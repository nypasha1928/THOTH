"""Curated failure metadata only: never retain upstream bodies or credentials."""
from urllib.error import HTTPError
import sqlite3

ENDPOINTS={'/v5/market/kline','/v5/position/list','/v5/position/trading-stop','/v5/account/wallet-balance',
 '/v5/account/transaction-log','/v5/order/create','/v5/order/realtime','/v5/order/history',
 '/v5/order/cancel','/v5/user/query-api','/v5/account/info'}


def failure_details(exc,stage):
    from .bybit_testnet import BybitError
    kind='DatabaseError' if isinstance(exc,sqlite3.Error) else 'BybitError' if isinstance(exc,BybitError) else 'HTTPError' if isinstance(exc,HTTPError) else 'TimeoutError' if isinstance(exc,TimeoutError) else 'ConnectionError' if isinstance(exc,OSError) else 'ValidationError' if isinstance(exc,ValueError) else 'UnexpectedError'
    result={'stage':stage,'error_type':kind}
    if isinstance(exc,BybitError) and isinstance(exc.code,int):result['exchange_code']=exc.code
    if isinstance(exc,HTTPError):result['http_status']=exc.code
    if isinstance(exc,sqlite3.Error):
        # Numeric SQLite codes and fixed labels survive saved pause reports without
        # exposing SQL, database paths, account data or raw exception messages.
        result['exception_class']=type(exc).__name__ if type(exc).__module__=='sqlite3' else 'Error'
        code=getattr(exc,'sqlite_errorcode',None)
        if type(code) is int and 0<=code<=65535:
            result['sqlite_code']=code
            labels={1:'SQL error',2:'SQLite internal error',3:'Permission denied',4:'Operation aborted',
                5:'Database busy',6:'Database locked',7:'Out of memory',8:'Read-only database',
                9:'Operation interrupted',10:'Disk I/O error',11:'Database corrupt',12:'Database not found',
                13:'Disk full',14:'Unable to open database',15:'Lock protocol error',17:'Schema changed',
                18:'Value too large',19:'Constraint failed',20:'Type mismatch',21:'SQLite misuse',
                22:'Unsupported platform',23:'Authorization denied',24:'Invalid parameter',26:'Not a database'}
            result['database_reason']=labels.get(code & 255,'SQLite failure')
    quota=getattr(exc,'rate_quota',{})
    safe_quota={k:v for k,v in quota.items() if k in ('limit','remaining','reset_ms') and type(v) is int and 0<=v<=10**16} if isinstance(quota,dict) else {}
    if safe_quota:result['rate_quota']=safe_quota
    reason=getattr(exc,'exit_data_reason',None)
    if reason in ('forming_candle_or_history_unavailable','candle_gap','invalid_price'):result['data_reason']=reason
    endpoint=getattr(exc,'thoth_endpoint',None)
    if endpoint in ENDPOINTS:result['endpoint']=endpoint
    if stage=='account_monitor':
        # Keep code locations, never traceback source lines, locals or exception text.
        from pathlib import Path
        root=Path(__file__).resolve().parent
        known={KeyError,TypeError,AttributeError,IndexError,RuntimeError,ZeroDivisionError,ValueError,OverflowError}
        if type(exc) in known:result['exception_class']=type(exc).__name__
        sites=[];tb=exc.__traceback__
        while tb:
            code=tb.tb_frame.f_code;path=Path(code.co_filename).resolve()
            if path.parent==root and path.suffix=='.py' and code.co_name.isidentifier():
                sites.append(dict(file=path.name,function=code.co_name,line=tb.tb_lineno))
            tb=tb.tb_next
        if sites:result['code_locations']=sites[-5:]
    return result


def failure_summary(details):
    parts=[str(details[k]) for k in ('stage','error_type','endpoint','exchange_code','http_status','data_reason','sqlite_code','database_reason') if k in details]
    if details.get('stage')=='account_monitor':
        if details.get('exception_class'):parts.append(details['exception_class'])
        sites=details.get('code_locations',[])
        if sites:
            site=sites[-1];parts.append(site['file']+':'+str(site['line'])+' '+site['function'])
    reason=PROTECTION_LABELS.get(details.get('protection_reason'))
    if reason:parts.append(reason)
    elif details.get('protection_check') in PROTECTION_CHECKS:parts.append('check: '+details['protection_check'].replace('_',' '))
    return ' · '.join(parts)


PROTECTION_CHECKS={'entry_price','filled_margin','stop_price','liquidation_boundary','existing_stop','attach_stop','verify_stop'}
PROTECTION_REASONS={
 'Partial fill margin is too small for requested dollar stop':'stop_exceeds_filled_margin',
 'Stop is beyond liquidation':'stop_at_or_beyond_liquidation',
 'Dollar stop implies non-positive price':'nonpositive_stop_price',
 'Dollar stop is smaller than one price tick':'stop_smaller_than_tick',
 'Invalid dollar stop inputs':'invalid_stop_inputs',
 'Expected stop not confirmed':'expected_stop_not_confirmed',
}
PROTECTION_LABELS={
 'stop_exceeds_filled_margin':'Requested stop is at or above filled initial margin',
 'stop_at_or_beyond_liquidation':'Requested stop is at or beyond liquidation',
 'nonpositive_stop_price':'Requested dollar stop implies a nonpositive price',
 'stop_smaller_than_tick':'Requested dollar stop is smaller than one price tick',
 'invalid_stop_inputs':'Invalid stop calculation inputs',
 'expected_stop_not_confirmed':'Expected exchange stop was not confirmed',
}


def protection_details(exc,stage,check,values):
    """Only fixed local reason labels and finite numeric stop inputs; never raw responses."""
    from decimal import Decimal,InvalidOperation
    result=failure_details(exc,stage)
    if check in PROTECTION_CHECKS:result['protection_check']=check
    if isinstance(exc,ValueError) and str(exc) in PROTECTION_REASONS:
        result['protection_reason']=PROTECTION_REASONS[str(exc)]
    numbers={}
    for key in ('entry','filled_qty','leverage','filled_margin','stop_dollars','expected_stop','liquidation_price','tick_size','reported_stop'):
        try:
            value=Decimal(str(values[key]))
            if value.is_finite() and (value==0 or -30<=value.adjusted()<=30) and len(str(value))<=80:
                numbers[key]=str(value)
        except (KeyError,InvalidOperation,ValueError,TypeError):pass
    if numbers:result['protection_values']=numbers
    return result
