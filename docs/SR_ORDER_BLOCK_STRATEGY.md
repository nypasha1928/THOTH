# S/R 4h/D → Order Block 5m

This is a separate selectable strategy. The existing S/R + RSI → Volume Delta strategy remains available.

## Entry sequence

1. Monitor both 4-hour and Daily S/R breakouts intrabar, using confirmed Major/Minor levels and fresh observations. **No RSI** can arm this profile. S/R entry filters are off.
2. The first S/R signal arms that coin without placing an order or choosing a direction. Later higher signals do not reset the permission or loss count.
3. Wait for a **new 5-minute Order Block signal**, sampled while its candle forms. The Order Block event chooses Long or Short independently of the original S/R direction.

The user's final SMA 200 rules supersede the earlier answer requesting inverted comparisons for every event:

| 5-minute event | Position | Current 5-minute close vs 5-minute SMA 200 |
| --- | --- | --- |
| Bullish Order Block formed | Long | Strictly below |
| Bearish Order Block formed | Short | Strictly above |
| Breakout Above Supply | Long | Strictly above |
| Breakdown Below Demand | Short | Strictly below |

At equality, none qualifies. SMA period 200 and confirmation timeframe 5m are fixed in this profile. RSI, VWAP, SMA 7/50, volume and strength filters are not used. The four signal families are individually selectable and all enabled by default. If opposing eligible families occur in one sampled candle, keep both event types in the reference data and skip the ambiguous entry, consistent with existing combined strategies.

Supply breaks use `high > supply high`; demand breaks use `low < demand low`, as in the uploaded Pine. They do not require the closing price to cross the zone. The current close determines the SMA comparison. Both permission and confirmation work intrabar; polling is not tick-by-tick.

## Original script and explicit overrides

The upload is preserved byte for byte at `reference/order_block_salem_uploaded.pine`, SHA256 `e2fcbf0221f92fc175ead52f569c86d063c4f43d7588048c031c2e0934af03d5`. The port in `thoth/order_block.py` preserves the price-action patterns, ATR14, DAC/pivot offsets, zone bounds, head-only strict wick breaks, overlap/bounce/replacement order and full active-zone queues.

The uploaded defaults have several optional filters enabled and SMA200 disabled. The user explicitly chose only SMA200, so the trading profile disables the others and enables the inverted SMA200 comparison for formation. The uploaded supply/demand break alerts originally bypass SMA200. After clarification the user explicitly required supply breaks above SMA200 and demand breaks below SMA200; those requested filters are applied in the strategy without modifying the uploaded reference. TradingView comparisons must account for those deliberate settings/alert differences.

## State, management and restart

Higher permission and 5m confirmation reuse the independent workers. Waiting coins do not fetch confirmation data. Newly armed coins bootstrap up to1,000 actual 5m candles; at least200 price samples are needed for SMA200. Later reads use60 recent candles plus gap catchup. Closed pattern/ATR/zone state is checkpointed. Each forming preview copies the previous closed state, matching Pine rollback; a temporary zone/break is never permanently committed by a preview.

Only fresh, previously unconsumed events may enter. Events already visible on restart or after a data gap are consumed as a baseline, not replayed. This is intentionally stricter than displaying historical reference events. Closed-candle revisions in the checkpoint overlap trigger replay of retained history. Initial data cannot reconstruct zones formed before the available history window; chart references also start at the loaded window. Numerical/feed-timing parity with TradingView is not established by these tests.

No SMA exit is used. Existing dollar stops, optional TP, combined/per-coin trailing, opposite-signal close/reversal choice and account controls remain. Default three consecutive settled losses lock only that coin for60minutes, then require a fresh 4h/Daily **S/R** signal before another new Order Block event. Wins reset its consecutive count; flat results do not. Existing risk cooldowns are retained when switching profiles. Other healthy coins continue.

Pause entries and close/reconcile existing positions before switching strategy. Saving does not enable live orders. After deployment, inspect both worker health sections and actual arming/order/fill/stop Activity messages.

## Chart and validation

On a 5m chart, Appearance → **Order Block · 5m** controls demand/supply boxes and their two colors. Boxes extend right, with translucent fills, borders and visible Strength labels; there are no Order Block arrows. Other chart timeframes show a notice to select5m. Display controls do not change trading permission. History, Analysis and Signal alerts identify this strategy and record the higher S/R source plus the precise Order Block event.

Validation covers source hash, exact hand-calculated zone bounds/offsets, mirrored bullish/bearish replay, checkpoint restoration, strict wick/head-only breaks, bounce/replacement mutation, event-specific SMA comparisons, rollback, baseline/restart dedupe, S/R-only permission without RSI calls, a real simulated 5m formation → protected order, per-coin lock/isolation, read-only API references, and desktop/mobile settings/chart controls. No real exchange trades or deployment were performed by the coding agent. This does not establish profitability.

The box styling follows the user's TradingView screenshot: blue demand, orange supply,20% fill,2px border, rightward extension and gray Strength labels rounded to two decimals. Broken zones are removed; no additional Order Block arrows or colored broken-zone overlays are drawn. Existing S/R/RSI/SMA/Volume Delta arrows are separate indicators with their own display controls. The screenshot's1m selection is a visual reference; this strategy's confirmation remains5m.
