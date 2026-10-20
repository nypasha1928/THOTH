"""Supported Bybit candle intervals shared by charts, strategies and exits."""
INTERVALS={'1':60,'5':300,'60':3600,'240':14400,'D':86400}


def interval_seconds(interval):
    try:return INTERVALS[interval]
    except KeyError:raise ValueError('Unsupported candle interval') from None
