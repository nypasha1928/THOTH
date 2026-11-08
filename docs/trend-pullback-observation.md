# Trend + Major Pullback · Observation only

Analysis has an expandable research panel. Collection is ON by default. Leave the
present LIVE strategy selected: the observer runs alongside the two 30m delayed
profiles, the closed 30m/two-direction profile, or Price × SMA50 · 30m Bar Close. This is not a selectable LIVE
strategy and it has no order client. No LIVE entry, protection or risk settings
are changed. It can collect while LIVE entries are paused if market management
remains connected. The panel reports unsupported/disconnected collection state.

The main source runner queues a tiny snapshot after it has saved the usual 30m
history. A separate worker reads that candle database in read-only mode and writes
only its own `trend-pullback-observation.sqlite`. No extra market requests are made.
The queue is bounded; late/dropped observations and worker errors are reported.
Source-runner exceptions from observation submission are caught, leaving LIVE
signal processing unchanged. Collection is independent of whether the panel is open.

## Prospective entry rules

- Pair existing consecutive CLOSED 30m bars into real completed 1h bars. Require
  the latest 200 consecutive completed hours. No partial hour enters SMA200.
- Last closed 1h price above its SMA200 permits Long; below permits Short. Equality,
  insufficient history or data gaps cannot qualify.
- A CLOSED 30m candle must show native Major support bounce (Long) or resistance
  rejection (Short), aligned with hourly direction. Both long/short-term Majors
  are available. References must already exist before that candle; no minors,
  breakout-only entries, Order Block triggers or RSI triggers.
- First timely post-close observation (within 120 seconds) supplies the sampled
  entry price; do not wait for :07/:37. Including adverse modeled slippage, entry
  must be on the signal side of the reference and at most 0.25 ATR14 beyond it.
- ATR14 uses Wilder smoothing on contiguous closed 30m bars, including the signal
  candle. The stop is beyond the setup low/high and reference plus 0.10 ATR.
- Quantity targets $0.40 initial structural-stop risk. Skip if the modeled
  notional would exceed $1,000. No stacking: one virtual position per coin.
- The saved daily/weekend entry schedule applies. Daily force-flat cutoff applies
  when a continuous price sample observes it. Other account eligibility and real
  portfolio Max Positions are NOT simulated.

Research assumptions are editable in the panel, independent of LIVE Settings.
RSI14 (unsmoothed close source) is recorded as context only, never an entry filter.
Different saved rule sets have different identities; do not treat their combined
summary as a controlled comparison. Each record retains assumptions, model hash,
source strategy, watchlist scope, signal candle, prior Major levels and hourly
trend evidence. Scope describes the Main/TradFi watchlist, not verified asset class.

## Virtual position and result rules

Defaults: breakeven arms at 1R and protects fixed 0.25R; trailing arms at 1.5R and
allows a 0.5R drop from the highest sampled gross profit. R is initial modeled
structural-stop risk, not margin or price percentage. Structural stop, trailing,
breakeven or scheduled daily cutoff closes the virtual position on a sampled
price. No live order, exchange stop or account-wide close is produced.

Entry and exit each assume 2 basis points adverse slippage and 5.5 basis points
fees. Simulated closed results show gross, estimated fees, net dollars and net R.
Funding, spread beyond the slippage assumption, lot-size/minimum orders, margin,
liquidation, portfolio capacity and actual account eligibility are not modeled.
This is a sampled-price simulation, not a historical replay or a real fill.

Per-position evidence includes maximum favorable/adverse sampled R, adverse R
before the first gross-positive sample, and largest sample gap. Intracandle moves
between samples can be missed. The observer never uses a candle high/low as an
assumed fill or guarantees execution at a stop/floor. Gaps can produce losses
beyond 1R when still within the continuity limit.

A gap over 120 seconds, restart, or research settings change interrupts an open
simulation: it receives NO invented exit or net result. Old setups are baselined
rather than replayed. Interrupted records stay visible and are excluded from
closed-result totals. Closed results remain durable. At most 20,000 non-open
records are retained; previously pruned records cannot be exported.

**Copy all observations** exports all retained evidence, settings, results and
limitations. Evaluate enough prospective observations, separate watchlist scopes
and market conditions, and inspect estimated costs before considering any LIVE
implementation. This research does not establish profitable or optimal settings.
