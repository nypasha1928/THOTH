# Crypto Order Block · 1h Close · Per-coin Strength · $1 Stop

A separate selectable strategy (`crypto_ob_strength_1h_close`) and saved list
**Crypto · OB Strength Candidates 10**. Selecting it loads the candidate list into
the settings draft; saving applies the selection. It does not enable orders.
The existing Main/TradFi/Top50 lists remain available. One account strategy runs
at a time. Subsets of these ten are supported; other symbols are rejected.

| Symbol | Minimum raw block width |
|---|---:|
| LDOUSDT | 0.005 |
| UNIUSDT | 0.1 |
| WIFUSDT | 0.0019 |
| LINKUSDT | 0.107 |
| JASMYUSDT | 0.000052 |
| GRTUSDT | 0.00033 |
| JUPUSDT | 0.0045 |
| GALAUSDT | 0.000021 |
| ETHUSDT | 7.45 |
| SEIUSDT | 0.00081 |

These are fixed historical research candidates, not proven optimal thresholds.
Strength is the original formed zone's high minus low in raw quoted price units,
not ATR, volume, or a percentage. No automatic optimization or rebalancing.

## Signals and execution

- Evaluate only completed 1h candles with the existing Salem OB formation logic.
- Both bullish and bearish formations must meet that symbol's minimum width.
  The threshold applies when admitting a new zone, preserving native break and
  overlap ordering. Understrength zones are not admitted. Subsequent overlap can
  narrow an admitted zone without retroactively changing a formation signal.
- Bullish formation enters Long; bearish formation enters Short. Breaks do not
  signal entries, exits or reversals. No S/R, RSI, volume, SMA or ATR entry filter.
- First execution observation baselines instead of replaying an old signal.
  Persisted candle cursors prevent duplicate entries after a restart.
- Same-direction signals while holding do nothing. An opposite qualifying closed
  formation closes the current holding, waits for closure and P&L reconciliation,
  then reverses if entries, capacity, schedule, account health and risk allow it.
  Permission to close does not guarantee permission to open the replacement.
- Queued entry/reversal is rechecked against its symbol, side, threshold,
  boundaries and latest completed hour, including immediately before submission.
- A stop close does not automatically reverse or reenter. A fresh formation is
  needed for the next entry.

## Protection policy

Selecting this strategy sets and locks the **$1 gross stop per new position**.
It uses the normal journaled Bybit MarkPrice stop with post-fill protection
attachment/verification and existing protective-failure close handling. The
independent account monitor also enforces the position's dollar loss limit.
Fees, slippage, gaps and exchange availability can increase actual loss.

Price trailing, per-coin P&L trailing, combined open-P&L trailing, per-coin
breakeven, take profit and SMA/OB auxiliary exits are off in this profile.
Position size, leverage and Maximum Positions remain the operator's settings;
there is no hardcoded ten-position limit. Account daily/period protection,
loss cooldowns and scheduled entry/cutoff controls remain active.
Existing positions retain their originally recorded dollar stop; choosing this
profile does not replace their protection with $1.

The research used $50 notional, all hours, no account cooldowns or portfolio
constraints; it does not predict results at another size or reproduce live
execution, funding or liquidation. Saved schedules can restrict live entries.

## Where to see it

In **Settings → Entry logic**, choose the new strategy. The strategy description
and expandable per-coin table show the policy and all ten widths. The dedicated
saved list is selected into the draft. Save settings, then perform normal
readiness/activation checks separately.

On a candidate's **1h chart**, enable the Order Block overlay. It replays the
same strength-admission logic using closed candles, hides subthreshold current
boxes and break arrows, and carries confirmed boxes across the forming hour
without generating a forming signal. Other chart timeframes and unsupported
symbols do not show this profile's OB boxes/signals. Small widths retain useful
precision. Historical formation references are not actual fills; E/X markers
identify actual trades. Loaded history length can change historical zone state.

Signal diagnostics and expandable history reason details retain the actual
block boundaries, width, threshold, symbol and source candle. History copy/export
includes this evidence. Existing strategy labels and historical records remain.

## Validation

Tests cover closed-only processing, per-symbol thresholds, no break triggers,
checkpoint resume, API normalization, filtered chart overlays, baseline and
repeat-scan deduplication, stop submission/verification, close-reconcile-reverse,
standalone dollar-stop closure without automatic reentry, and retained evidence.
