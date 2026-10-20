"""Curated failure metadata only: never retain upstream bodies or credentials."""
from urllib.error import HTTPError

ENDPOINTS={'/v5/market/kline','/v5/position/list','/v5/position/trading-stop','/v5/account/wallet-balance',
 '/v5/account/transaction-log','/v5/order/create','/v5/order/realtime','/v5/order/history',
 '/v5/order/cancel','/v5/user/query-api','/v5/account/info'}


def failure_details(exc,stage):
    from .bybit_testnet import BybitError
    kind='BybitError' if isinstance(exc,BybitError) else 'HTTPError' if isinstance(exc,HTTPError) else 'TimeoutError' if isinstance(exc,TimeoutError) else 'ConnectionError' if isinstance(exc,OSError) else 'ValidationError' if isinstance(exc,ValueError) else 'UnexpectedError'
    result={'stage':stage,'error_type':kind}
    if isinstance(exc,BybitError) and isinstance(exc.code,int):result['exchange_code']=exc.code
    if isinstance(exc,HTTPError):result['http_status']=exc.code
    reason=getattr(exc,'exit_data_reason',None)
    if reason in ('forming_candle_or_history_unavailable','candle_gap','invalid_price'):result['data_reason']=reason
    endpoint=getattr(exc,'thoth_endpoint',None)
    if endpoint in ENDPOINTS:result['endpoint']=endpoint
    return result


def failure_summary(details):
    return ' · '.join(str(details[k]) for k in ('stage','error_type','endpoint','exchange_code','http_status','data_reason') if k in details)
