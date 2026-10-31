"""Supported Bybit candle intervals shared by charts, strategies and exits."""
INTERVALS={'1':60,'5':300,'60':3600,'240':14400,'D':86400}


def interval_seconds(interval):
    try:return INTERVALS[interval]
    except KeyError:raise ValueError('Unsupported candle interval') from None

HOURLY_MINUTE_STRATEGIES=('sr_hourly_intrabar','rsi_hourly_intrabar','sr_rsi_daily','sr_inverse_intrabar')

INTRABAR_STRATEGIES=HOURLY_MINUTE_STRATEGIES+('order_block_intrabar','order_block_sr_intrabar','order_block_sr_1h','sma_7_50_intrabar','sr_volume_intrabar','volume_delta_intrabar','sr_rsi_volume_gate','sr_order_block_gate','order_block_sr_1h_early','ob_sr_hour_gate','ob_sr_minute_hourly')

# Closed-bar detection uses a short execution queue too, timed from observation.
SHORT_LIVED_SIGNAL_STRATEGIES=INTRABAR_STRATEGIES+('order_block_sr_1h_inverted_close','order_block_sr_1h_close')
