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
