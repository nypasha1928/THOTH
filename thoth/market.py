"""Read-only public Bybit market data. TLS verification remains enabled."""
import json
from urllib.parse import urlencode
from urllib.request import urlopen


def candles(symbol, interval):
    params = urlencode(dict(category='linear', symbol=symbol, interval=interval, limit=1000))
    with urlopen('https://api.bybit.com/v5/market/kline?' + params, timeout=12) as response:
        result = json.load(response)
    if result.get('retCode') != 0:
        raise ValueError(result.get('retMsg', 'Bybit rejected market request'))
    if not result['result']['list']:
        raise ValueError('No candles available for this symbol')
    return [dict(time=int(row[0]) // 1000, open=float(row[1]), high=float(row[2]),
                 low=float(row[3]), close=float(row[4]), volume=float(row[5]))
            for row in reversed(result['result']['list'])]


def sma(rows, length):
    total = 0.0
    output = []
    for i, row in enumerate(rows):
        total += row['close']
        if i >= length:
            total -= rows[i-length]['close']
        if i >= length-1:
            output.append(dict(time=row['time'], value=total/length))
    return output


from functools import lru_cache
from math import isfinite
from time import time


def quote(symbol, environment='live'):
    """Public last-traded price, cached for five seconds; never sends credentials."""
    return dict(_quote(symbol, environment, int(time() // 5)),server_time=time())


@lru_cache(maxsize=256)
def _quote(symbol, environment, bucket):
    if environment not in ('paper','live','testnet'):
        raise ValueError('Unsupported quote environment')
    host='https://api-testnet.bybit.com' if environment=='testnet' else 'https://api.bybit.com'
    params=urlencode(dict(category='linear',symbol=symbol))
    with urlopen(host+'/v5/market/tickers?'+params,timeout=10) as response:
        result=json.load(response)
    rows=result.get('result',{}).get('list',[])
    if result.get('retCode')!=0 or len(rows)!=1 or rows[0].get('symbol')!=symbol:
        raise ValueError('Quote unavailable')
    price=float(rows[0]['lastPrice'])
    if not isfinite(price) or price<=0:raise ValueError('Invalid quote')
    observed=time()
    exchange=float(result.get('time',observed*1000))/1000
    if not isfinite(exchange):exchange=observed
    return dict(symbol=symbol,environment=environment,price=price,observed_at=observed,exchange_clock_offset=exchange-observed)
