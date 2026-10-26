"""Direct 4h/Daily S/R entries, gated by the forming candle's volume SMA."""
import math
STRATEGY='sr_volume_intrabar'


def volume_pass(rows,period):
    if len(rows)<period:return dict(passed=False,volume=None,average=None,reason='volume MA warmup unavailable')
    values=[float(row['volume']) for row in rows[-period:]]
    if any(not math.isfinite(value) or value<0 for value in values):
        raise ValueError('Invalid candle volume')
    average=sum(values)/period
    return dict(passed=values[-1]>average,volume=values[-1],average=average,
                reason='volume passed MA' if values[-1]>average else 'volume did not exceed MA')
