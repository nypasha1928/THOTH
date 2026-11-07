# S/R + RSI · 30m Close · Delayed :07/:37

New selectable profile `sr_rsi_30m_delayed`. The existing Order Block + S/R profile
remains available with unchanged signal rules. This profile never evaluates Order
Block events for entry or exit.

Only completed 30-minute candles qualify. Either Major S/R or one of the six RSI
crossings can provide the direction; both are not required. Major S/R retains
long-term and short-term breakout/breakdown and native bounce/rejection. Minor
levels cannot qualify. Opposing Long/Short sources on the same candle skip entry.

The six RSI rules use the existing RSI engine with **original_six** and **fixed**
threshold mode:

| Event | Direction |
| --- | --- |
| Cross above 30 | Long |
| Cross below 70 | Short |
| Cross above fixed Long threshold | Long |
| Cross below fixed Long threshold | Short |
| Cross above fixed Short threshold | Long |
| Cross below fixed Short threshold | Short |

The existing saved Long/Short threshold values remain editable in RSI Settings
(defaults 56.68 / 42.38). Existing RSI source, length and smoothing settings are
retained. Static and fixed-threshold signals are both enabled. Volume, VWAP,
SMA alignment and MACD histogram filters are forced OFF for this profile, matching
the unfiltered strategy it duplicates. Crossing means the previous closed RSI
was on or across the other side, and the current closed RSI is strictly past the
threshold. Equality alone is not a crossing. Warm-up or absent RSI cannot create
an RSI signal; independently valid S/R can still qualify.

Closed :00 signal -> request during :07; closed :30 signal -> request during :37.
The execution window lasts exactly one minute (ends :08 / :38, exclusive).
The saved direction is executed directly, not requalified on a forming candle.
No 1-minute entry/exit worker is required. Missed windows are discarded. Old
crossings are not replayed on startup or after a continuity gap. Pending signals
are cleared on pause, restart or settings save, as for the existing profile.

Selected opposite Reversal/Close-only behavior, Max Positions, exchange checks,
stop loss, per-coin/combined trailing, breakeven, schedule and loss cooldowns stay
in force. Fills can arrive after the request minute. A close/reversal must settle
before its replacement is considered, and a new loss cooldown can block it.

Alerts preserve the signal candle, previous/current RSI, thresholds, exact RSI
crossings, S/R references and source names. Copy all signal alerts includes those
diagnostics for newly captured signals. Chart RSI arrows are references; actual
trade markers show executions. Order Block overlays, if displayed, have no role
in this profile. No production selection or activation is performed by this change.
