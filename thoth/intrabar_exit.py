"""Causal SMA 7/50 exits using the current (unfinished) exchange candle."""
import math
from .timeframes import interval_seconds


CANDLE_PUBLICATION_GRACE=10  # Seconds; never fabricate the new candle.


class ExitDataError(ValueError):
    def __init__(self,reason):
        super().__init__(reason)
        self.exit_data_reason=reason


def observe_exit(tracker,rows,timeframe,side,now):
    duration=interval_seconds(timeframe)
    start=int(now)//duration*duration
    rows=sorted(rows,key=lambda r:r['time'])
    if len(rows)<51:raise ExitDataError('forming_candle_or_history_unavailable')
    publishing=(rows[-1]['time']==start-duration and 0<=now-start<=CANDLE_PUBLICATION_GRACE)
    if rows[-1]['time']!=start and not publishing:
        raise ExitDataError('forming_candle_or_history_unavailable')
    rows=rows[-51:]
    if any(b['time']-a['time']!=duration for a,b in zip(rows,rows[1:])):
        raise ExitDataError('candle_gap')
    values=[float(r['close']) for r in rows]
    if any(not math.isfinite(v) or v<=0 for v in values):raise ExitDataError('invalid_price')
    if publishing:
        # REST can return the just-closed candle while the new bar publishes.
        # Retry on the normal cadence; keep the last real SMA observation.
        tracker.update(checked_at=now,waiting_for_candle=start)
        return None
    tracker.pop('waiting_for_candle',None)
    previous=sum(values[-8:-1])/7-sum(values[:-1])/50
    current=sum(values[-7:])/7-sum(values[-50:])/50
    if tracker.get('timeframe')==timeframe and tracker.get('time') in (start,start-duration) and tracker.get('difference') is not None:
        previous=tracker['difference']
    tracker.update(time=start,timeframe=timeframe,difference=current,checked_at=now)
    if side=='Buy' and previous>=0 and current<0:return 'SMA 7 crossed below SMA 50 (intrabar exit)'
    if side=='Sell' and previous<=0 and current>0:return 'SMA 7 crossed above SMA 50 (intrabar exit)'
    return None
