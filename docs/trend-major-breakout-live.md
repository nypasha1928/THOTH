# Trend + Major Breakout · 1h / 30m Close

New selectable profile: `trend_major_breakout_30m_close`. The existing
`trend_major_pullback_30m_close` profile remains bounce/rejection only.
The two have different settings identities, entry sources and History evidence.
They are alternative LIVE selections; adding this option does not switch the
selected strategy, deploy the update or activate orders.

## Entry rules

- Build completed UTC hourly candles from consecutive actual closed 30m pairs.
  Require 200 consecutive completed hours. Last hourly close above its SMA200
  permits Long; below permits Short. Equality or insufficient data skips.
- Use the latest newly CLOSED 30m candle. A resistance cross requires the previous
  30m close <= a Major resistance already known before the deciding candle, and
  the deciding close strictly > that same resistance. Support breakdown mirrors
  this: previous close >= prior support, deciding close strictly < support.
- Both long-term and short-term Major families participate. Minor levels and
  native touch/bounce/rejection events never trigger this profile. Same-side
  closes do not repeat a cross. A new level formed by the deciding candle cannot
  manufacture a breakout of a previously unknown reference.
- Breakout direction must match the hourly trend. Entry uses the first timely
  post-close decision, within 120 seconds, with a price snapshot no older than
  30 seconds. No :07/:37 delay or intrabar entry. Restart, recovery and missed
  scans baseline without replaying old signals. A candle has one decision.
- Buffered entry must be on the signal side of its own broken level and no more
  than **0.25 Wilder ATR14** beyond it, using a **2 bps adverse price buffer**.
  ATR uses completed 30m bars, including the deciding candle. Each agreeing source
  is checked against its own level; choose the nearest eligible reference as
  the exact entry source and retain all source distances in the evidence.
- Recheck that frozen confirmed setup against the current ticker during entry
  checks and immediately before submission. Expired/overextended entries skip.
  The buffer does not guarantee the eventual market fill.

The profile shares freshness, persistence and submission-guard infrastructure
with the pullback profile. Each uses its own signal qualifier and evidence key;
new breakout orders cannot consume pullback entry evidence.

## Sizing, exits and records

Keep the saved position notional, leverage, dollar stop, take profit, per-coin and
combined trailing, breakeven, maximum positions, account protections, cooldowns
and schedules. Selected Reversal/Close-only applies to opposite qualifying closed
breakout signals. No separate SMA or 1m exit is added. No research structural-stop
sizing or new dollar risk is applied.

Settings and the active banner show the separate Breakout name. Analysis has its
own strategy filter. History → Reason → **Trend + Major Breakout entry evidence**
and Copy all trades include trend, source, boundary, ATR distance and quote check.
The existing pullback evidence remains separate. Ordinary no-event checks go to
Analysis diagnostics rather than Signal alerts.

The 30m chart shows Major breakout references for this profile. Use the 1h chart
with SMA200 for the trend; chart averages follow the displayed timeframe. Raw
reference arrows do not prove trend/distance qualification or an executed order.

The existing Trend + Major Pullback observational panel remains bounce/rejection
research and does not collect under the new breakout selection. Its saved rules
and outcomes are not relabelled as breakout results. This change adds a separate
LIVE strategy, not a new breakout simulation. Technical checks do not establish
profitability.
