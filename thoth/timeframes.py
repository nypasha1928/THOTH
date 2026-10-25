"""Supported Bybit candle intervals shared by charts, strategies and exits."""
INTERVALS={'1':60,'5':300,'60':3600,'240':14400,'D':86400}


def interval_seconds(interval):
    try:return INTERVALS[interval]
    except KeyError:raise ValueError('Unsupported candle interval') from None

HOURLY_MINUTE_STRATEGIES=('sr_hourly_intrabar','rsi_hourly_intrabar','sr_rsi_daily','sr_inverse_intrabar')

INTRABAR_STRATEGIES=HOURLY_MINUTE_STRATEGIES+('volume_delta_intrabar',)
