# RSI visual comparison changes

The user confirmed the exact uploaded Pine default Inputs. Calculation/filter inputs were therefore preserved. This update addresses display defects and comparison timing; it does not claim that THOTH's corrected ML calculation is numerically identical to the original TradingView script.

## Findings disclosed before changes

- A fixed purple RSI line omitted the source's bullish/bearish/neutral coloring.
- Direction-only marker deduplication discarded distinctions between six signal families.
- Pane markers followed the RSI trace instead of the source's explicit threshold offsets.
- Individual marker controls, source backgrounds, cluster circles and pane sizing were missing.
- Price-only panning/scaling paused candle/RSI refresh while the separate quote remained live.
- The older-history request omitted the RSI include flag.
- Chart calculation inherited entry-family switches, preventing independent display of eligible families.

The screenshots also show differing window endpoints: THOTH ends at 21:43 UTC, while TradingView is near 17:49 America/New_York (21:49 UTC on October 21). This is a comparison-timing clue, not proof of a calculation defect. The updated chart identifies stale/historical candles and distinguishes their timestamp from the live quote.

## Marker defaults

| Family | Price color | Pane color | Default pane anchor |
| --- | --- | --- | --- |
| Bullish RSI 30 | Green | Green | 27 |
| Bearish RSI 70 | Red | Red | 73 |
| Bullish ML Long | Green | Cyan | Upper ML − 3 |
| Bearish ML Short | Yellow | Yellow | Lower ML + 3 |
| Bullish ML Short Up | Blue | Blue | Lower ML − 3 |
| Bearish ML Long Down | Orange | Orange | Upper ML + 3 |

Each family has independent chart/pane visibility, colors, shapes and positions. Default price placement is below the candle for bullish events and above for bearish events. Same-candle families stack vertically while retaining the same candle x coordinate. Pane threshold anchors remain separate from the RSI value. Background defaults follow the four source backgrounds; two additional families default to no background but can be configured.

RSI line colors use the current displayed bar's actual upper/lower thresholds: green strictly above upper, red strictly below lower, neutral otherwise. Threshold lines, cluster circles, 30/70 line color, pane height and line/arrow size are configurable. Cluster IDs/centers are returned as display metadata; numerical thresholds and trading events are unchanged.

## S/R, candles and hover

The S/R arrow switch and S/R signal-candle tint switch are independent. Tinting defaults off; it colors only candles with a confirmed S/R reference signal and uses its own bullish/bearish palette. S/R series are available independently of execution strategy, and SMA 7/200 reference markers retain their own switch. The top S/R/SMA arrows checkbox is their shared display master, not an RSI switch.

The vertical crosshair spans price, volume and RSI at one candle time. Hovering either pane's arrow resolves its stored candle timestamp, highlights that arrow and shows a tooltip and RSI/threshold readout. Candle labels now use America/New_York (EST/EDT), matching history/account display; underlying candle timestamps remain UTC. Hover does not submit an order or update settings.

## Refresh behavior

At the live edge, manual price scaling is retained while fresh candles/RSI arrive. A horizontal historical view or extended-history snapshot stays paused intentionally. A visible notice identifies that view and warns when candles lag behind a live quote. Latest candle performs an immediate fresh load. Older-history requests include RSI data. Disabling auto refresh also disables quote-triggered candle-boundary reloads; manual Refresh/Latest remain available.

## Validation and limits

Synthetic tests verify all six marker colors/threshold anchors, aligned pane/price x coordinates, full-height hover crosshair and tooltip, trend color segments, thin blue 30/70 levels, independent S/R arrows/tints, per-family color persistence, no style writes to trading settings, retained manual price scaling during refresh, and existing desktop/mobile chart/ruler/touch behavior. API tests verify chart families are independent of entry switches and cluster metadata matches actual centers.

These are visual/state/regression checks. No real account or live strategy was activated. Numerical comparison still requires matching closed candles from both applications with the same inputs and sufficient history. The separately documented corrected ML history/empty-cluster rules remain explicit semantic corrections to the uploaded Pine, not changes introduced to make screenshots look alike.
