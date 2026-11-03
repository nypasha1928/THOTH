# Order Block + S/R · 1h · Early :55–:00

This separate strategy evaluates the current forming Bybit hourly candle during
its final five minutes. Select it explicitly in Settings; saving does not enable
orders. The existing Bar Close profile remains available.

The first qualifying decision for each coin each hour can open Long or Short.
All four native Order Block event types and long-/short-term Major S/R breakouts
are eligible independently. Minor S/R, Volume MA and OB thickness filters
are disabled. A mandatory 1h SMA200 direction filter applies to new entries,
including reversal replacement orders. Bounce/Rejection has not been added to this profile.

Defaults, adjustable under **Early hourly qualification · :55–:00**:

- Opposing wick: at most 30% of the forming candle's full range.
- Distance beyond the source boundary in the signal direction: 0.05–0.50 ATR14.
- ATR uses Wilder ATR14 including the forming hourly candle.

At least one source must pass both wick and distance checks. Opposing raw source
directions skip the decision. These thresholds are experimental and do not
predict whether a trade will win. An OB formation edge is not a breakout level.

The execution path refreshes the last two hourly candles and revalidates against
the original indicator checkpoint after normal account, instrument and leverage
checks. Changed prior-hour data, changed settings, a faded signal, an expired
30-second queue or the end of the window blocks the new order. A durable per-coin
hourly reservation prevents a second entry after an early close or process
restart; uncertain submission outcomes retain it. A definitive rejection releases
the execution reservation, but the source worker does not replay that hourly
qualifying decision. Maximum Positions and existing account protections apply.

Qualifying opposite signals use the existing selected Reversal/Close-only
process. Reversal first closes and reconciles the held position; its replacement
must still pass the fresh checks and fit inside :55–:00. Stops, targets, trailing
and account management continue outside that window. There is no separate
indicator exit added. With one qualifying decision per coin per hour, another
signal for that coin is not emitted until the next hourly window.

Live checking is sampled, not tick-by-tick. A market order submitted before :00
may fill or be observed afterward. History labels these measurements as forming
candle snapshots, not final hourly closes. Execution records keep the sources
that passed the final recheck. The existing Analysis comparison remains an
independent observation-only feature for both the Bar Close and Early profiles.
It uses the Early profile’s saved limits and labels the profile on each record;
missing past intrabar observations are not reconstructed.

Validation uses synthetic candles and a simulated exchange; it does not establish
profitability or TradingView parity and never places real orders.

## Required SMA200 entry direction

For the Early profile only, use the last **200 consecutive completed Bybit hourly
closes**. Exclude the forming hour, fix the length at 200, and use strict decimal
comparisons without an equality tolerance:

- Current price above the average: Long entries only.
- Current price below the average: Short entries only.
- Exact equality, fewer than 200 completed hours, duplicate/gapped/invalid closes,
  stale hourly baseline, or unavailable/invalid fresh price: no new entry.

The scanner calculates the average from its existing downloaded history. The
compact entry context retains that hourly baseline. The normal final two-candle
refresh validates the prior closed candle and rechecks the forming price; a fresh
last-trade quote rechecks direction after idempotency reconciliation immediately
before any order POST. A local refusal is marked not-submitted and releases the
hourly reservation. No extra 200-candle download is added for each entry. A market
fill can still differ from the checked price; crossing the average after an order
has already been submitted does not retroactively invalidate that order.

This is an entry permission, not a signal inversion or new exit. Qualified
opposite source signals can still close held positions independently of this
filter. A reversal's replacement must pass the filter and all existing checks;
no replacement fallback is allowed. Stops, trailing, scheduled exits, daily risk
and the :55–:00 window remain independent. Other strategy profiles are unchanged.

The rule is part of the Early profile and takes effect after deployment; no new
checkbox must be enabled. Settings explains it under Early hourly qualification.
New History/Signal alert measurement details retain the SMA value, checked price
and direction decision. Submission diagnostics also retain the final quote check.
Earlier trades have no reconstructed SMA measurement. The comparison worker
applies the same filter to Early observations; Bar Close comparisons are unchanged.
The strategy fingerprint is versioned to separate the new rule from earlier data.
