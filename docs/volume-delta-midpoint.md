# Volume Delta · 1h · :30 Decision

This is a separate profile (`volume_delta_1h_midpoint`); the original Volume Delta
intrabar strategy is unchanged. Select it in Settings, save while entries are
paused, then use normal readiness and manual entry activation. Saving a profile
does not enable real orders. Existing scheduled windows and account protections
still apply. The fixed strategy timeframe is 1h; chart timeframe remains display
only.

## Decision

For each saved coin, make one sampled decision during :30:00 inclusive through
:31:00 exclusive of each hour. Use the current forming hourly candle, not a
closed hour. For example, the 10:30 decision uses the 10:00–11:00 candle and the
available minute-volume observations since 10:00. An already-present qualifying
signal counts; no new crossover during the decision minute is required.

- Current qualifying bullish signal: Long.
- Current qualifying bearish signal: Short.
- No qualifying signal or unavailable required data: skip the coin this hour.

Consume and persist the decision before execution; later samples, restart,
rejection, manual pauses or an early close do not produce a second scanner
decision in that hour. New hourly decisions may occur during the next :30 minute.
Startup still establishes the execution cursor before trading, so start observation
before the decision window. Missed windows are never replayed. Data downloaded
before :30 is not treated as a :30 observation, and observations older than 30
seconds cannot enter. This is sampled polling: a long scan, connection outage or
large watchlist can miss some coins' one-minute window. There is no guarantee of
an order or fill at exactly :30:00.

## Entry rules and filters

Reuse the existing uploaded Volume Delta family and its saved options:

- SMA7/50 alignment defaults ON: SMA7 > SMA50 for Long, SMA7 < SMA50 for Short.
  Equal averages or insufficient alignment history blocks qualification when ON.
  The averages use hourly candles including the forming hour. The switch remains
  configurable and independent of the generic SMA200 settings.
- Bullish/Bearish enable switches default ON; candle types default Strong.
- Strong bullish: hourly close > open and normalized delta > 0.
- Strong bearish: hourly close < open and normalized delta < 0.
- Weak uses the opposite delta sign while preserving hourly candle direction.
  All removes the delta-sign requirement, but retains candle direction and any
  enabled SMA alignment filter.
- Zero hourly volume and missing/invalid required minute data block qualification.

This is a **1-minute candle-volume proxy**, not aggressor buy/sell trade delta.
Up-minute volume adds to bullish volume and down-minute volume adds to bearish
volume. Flat-minute volume contributes to neither; the difference is normalized
by forming hourly volume. No minimum delta magnitude is added.

No SMA200, opposing-wick, ATR-distance, RSI, VWAP or Volume MA20 entry filter is
added. Wick/ATR candle measurements are recorded for comparison only, without
fabricating unavailable values. The dashboard shows saved filter settings.

## Execution and exits

Refresh the current/previous hourly candles and the current hour's minute inputs
before submitting. Reject changed prior-hour data, a faded signal, a changed
configuration, or an original direction that no longer qualifies. Reconciliation
and durable journal writes must still finish inside :30–:31 and the 30-second
freshness limit. A final pre-POST guard checks these deadlines and the schedule.
Locally unsent intents are marked not-submitted. Accepted or ambiguous submissions
retain normal reconciliation and the once-per-hour execution cap.

Selected Reversal/Close-only behaviour applies to opposite signals at the same
hourly decision. Reverse closes and reconciles first; a replacement can open only
if it still qualifies within the same decision minute. There is no separate
continuous opposing-signal exit. Protective stops, per-coin/combined trailing,
targets, risk management and scheduled exits continue throughout the hour.

Charts show closed-candle Volume Delta references where complete minute data is
available; those references do not reconstruct historical :30 decisions. Actual
trade markers and recorded signal alerts are the execution record. No-signal
:30 decisions and data-related skips are recorded in signal diagnostics.

Normal background scans download hourly data. Extra minute inputs are downloaded
for this profile only during the decision minute; the scan uses a bounded
32-minute refresh, and execution performs its own fresh two-hour/32-minute check.
