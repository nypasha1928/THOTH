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


@lru_cache(maxsize=24)
def _history_page(symbol,interval,environment,before,bucket):
    host='https://api-testnet.bybit.com' if environment=='testnet' else 'https://api.bybit.com'
    params=dict(category='linear',symbol=symbol,interval=interval,limit=1000)
    if before is not None:params['end']=before*1000-1
    with urlopen(host+'/v5/market/kline?'+urlencode(params),timeout=12) as response:
        body=json.load(response)
    if body.get('retCode')!=0:raise ValueError('Historical market data unavailable')
    return tuple(dict(time=int(r[0])//1000,open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),volume=float(r[5])) for r in sorted(body['result']['list'],key=lambda r:int(r[0])))


def chart_history(symbol,interval,bars,environment):
    """Bounded public chart replay only; never writes execution checkpoints."""
    from time import sleep
    if not 1000<=bars<=10000 or interval not in ('1','D') or environment not in ('paper','live','testnet'):
        raise ValueError('Unsupported historical chart request')
    collected={};before=None
    for page in range(10):
        rows=_history_page(symbol,interval,environment,before,int(time()//30))
        if not rows:break
        if before is not None and any(r['time']>=before for r in rows):raise ValueError('Historical pagination did not advance')
        collected.update({r['time']:dict(r) for r in rows})
        if len(collected)>=bars or len(rows)<1000:break
        before=rows[0]['time']
        sleep(.125)
    result=sorted(collected.values(),key=lambda r:r['time'])[-bars:]
    if not result:raise ValueError('No historical candles available')
    duration=60 if interval=='1' else 86400
    if any(b['time']-a['time']!=duration for a,b in zip(result,result[1:])):
        raise ValueError('Historical candles contain a gap; replay unavailable')
    return result
