# Order Block + S/R · 30m Close → 1m Close · Two Directions

A separate profile: `ob_sr_30m_close_two_direction`. The previous hourly window profile
retains its intrabar behavior. Chart timeframe now includes native Bybit **30m** candles.
Changing the chart timeframe changes display only.

## Permission and sources

A NEW **completed 30-minute candle** with an Order Block OR Major S/R signal selects the
coin. There are no :58–:03 or :28–:33 strategy windows. This profile has no wick, SMA,
Volume MA, ATR-distance or strength entry filter.

On BOTH the 30m and 1m source:

- All four unfiltered Order Block events remain enabled.
- Both long-term and short-term MAJOR support and resistance can signal.
- S/R includes breakout/breakdown AND native bounce/rejection.
- NO MINOR S/R signal can authorize permission, entry, reversal or exit.

S/R calculations use only causal confirmed pivots. A forming 30m candle cannot arm a coin.
On startup/restart or after a data gap, establish a baseline rather than replaying historical
signals; the latest old candle is not a new permission. The chart can still show forming
previews, explicitly marked as references; those previews do not trigger orders here.

## Closed minute sequence

After permission, wait for a NEW CLOSED 1m signal. That minute must close strictly AFTER
permission was armed; for an open position, it must also close AFTER THOTH observed its fill.
A closed minute that already existed before permission/fill cannot count just because it
was processed later. Minute direction is independent of the 30m direction.

1. First direction: enter Long or Short.
2. Next opposite CLOSED 1m direction: close, confirm flat, reconcile P&L, then reverse if
   account, schedule, cooldown, capacity and freshness checks allow.
3. Next opposite CLOSED 1m direction: close ONLY; no third entry. Wait for new 30m permission.

Same-direction repeats do nothing. A new CLOSED 30m permission restarts the sequence;
a position already open stays open and counts as direction 1. It does not open another
position just because permission refreshed. Each completed parent candle is consumed once.

Stops, targets, trailing, daily/weekend schedules, Maximum Positions and per-coin loss
cooldowns remain configured independently. A loss on direction 1 can trigger a cooldown
and block direction 2. This strategy does not bypass those protections or enable live entries.

## Data and execution

Hourly/parent and minute workers have separate processing cursors. Actual missing 30m/1m
candles are backfilled in bounded pages; no synthetic prices are used. Recovery bridges
remain until both closed-source states advance, including during chart refreshes.
Closed source signals emit once per completed bar, never while it is forming. Minute
snapshots must include the current minute, so the prior candle is confirmed from a
post-close download. Pending/unfilled entries cannot consume subsequent directions.

Entry intents remain bounded by the existing 30-second lifetime after observation,
including queued reversals; confirming a candle does not create an unlimited stale order.
Sequence reservations and rejected/unknown order handling match the prior profile.
Signals and closes remain sampled; candle close is not a guarantee of immediate fill.

History/alerts distinguish **30-minute** permission sources from **1-minute** signal sources.
The expandable coin-permission panel shows CLOSED 1m entry/reversal/exit stages.
