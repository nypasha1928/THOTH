# ML RSI draft for tomorrow

Status: offline source review and default-settings calculation prototype only. This branch does **not** add an app indicator, a selectable RSI strategy, a live signal feed, or order execution. Do not deploy it as a completed trading feature. Existing strategy modules and deployment configuration are untouched.

## Requested behavior

- Independently selectable chart indicator with an RSI pane, its 30/70 and adaptive threshold lines, plus separate visibility toggles for RSI signal markers in the pane and on the price chart. Hiding signals must never disable trading or change its calculation. Keep S/R arrows independently selectable.
- New strategy name proposed: **RSI 1h → Exit 1m**. Existing strategies remain available.
- Entries use the uploaded indicator's combined bullish/bearish alerts on forming hourly candles, without waiting for the hourly close. Static 30/70 and ML signal families must be separately selectable for trading, independently of display toggles. Default intent is combined alerts, matching the source's Combined Bullish/Bearish conditions.
- Long exits when the **1-minute** SMA 7 crosses below SMA 50; Short exits when it crosses above. No minute-close wait. Keep fixed-dollar Bybit stops and account/coin risk controls, original position ownership and restart recovery.
- Persist strategy and entry/exit timeframes in journal, owned positions and history. RSI entries must not inherit S/R entry filters: use the RSI script's own filter settings.
- Polling is sampled, not every exchange tick. Prevent duplicate orders when multiple signal families agree. No retrospective entries after a restart, stale market observation, pause, or missed crossing. Conflicting bullish/bearish events produce no entry.

## Source preserved exactly

`reference/ml_rsi_900_uploaded.pine` is the uploaded source, including its original CRLF line endings. SHA-256: `cf415bc7edbd732e04bf2c11148a2a04d06c4ff5dc418625134fd3a43e3078df`.

Its six signal families are:

| Source alert | Entry |
| --- | --- |
| RSI crosses above 30 | Long |
| RSI crosses below 70 | Short |
| RSI crosses above upper ML threshold | Long |
| RSI crosses below lower ML threshold | Short |
| RSI crosses above lower ML threshold | Long |
| RSI crosses below upper ML threshold | Short |

Defaults: close-source RSI 14, EMA smoothing 4, volume greater than its SMA 20 × 1.0, daily hlc3 VWAP filter, SMA 7/50 alignment filter, MACD histogram filter disabled. The source allows bypassing volume for static signals while keeping it for ML. These are RSI-specific settings; do not silently reuse the existing S/R filter flags.

The source's Show All/Static/ML Signals variables gate plots, **not** combined alerts. This distinction must remain explicit in THOTH's display and execution settings.

## Source defects and comparison decisions

1. Lines 185 and 216 allocate `array.new_float(3)` for distances, then append distances. That creates six slots rather than an empty list with three distances; the cluster index no longer represents the intended three centers.
2. Line 197 allocates `new_centroids = array.new_float(3)` and appends three means. After assignment, threshold slots 0 and 2 refer to the initially empty values rather than the appended means. A literal port cannot honestly be presented as a working adaptive-threshold indicator.
3. `maxData` selects history using `last_bar_index - bar_index`, then appends to a persistent array without trimming on subsequent live bars. That is not a consistent rolling window: historical recomputation can differ from an indefinitely running session. The proposed corrected draft uses a causal last `maxData + 1` valid RSI observations, explicitly a semantic correction.
4. Empty clusters require a defined policy. The prototype retains that cluster's previous center; this differs from an empty-array mean producing an unavailable value. It must be reviewed alongside a corrected Pine reference.
5. Performance Memory (`perfAlpha`) and the threshold range/step (`factors`) do not feed the signal calculations. Do not invent effects for these settings.
6. Flat zero-gain/zero-loss RSI seeding, all ten MA choices, alternate calculation sources, VWAP anchors/timezone and missing-value behavior require independent comparison. The prototype leaves all-zero seeds unavailable instead of inventing a numeric reading.

Tomorrow's key decision: compare against a corrected Pine reference with empty distance/centroid arrays, or initially enable only the valid static 30/70 signal families. Do not silently describe corrected ML calculations as equivalent to the uploaded script. No TradingView parity claim has been established.

## Work completed on this branch

`tools/ml_rsi_draft.py` is an offline calculation prototype. It has no API client, credentials, database, dispatch loop, app imports or order submission. It handles the supplied **default calculation choices** (close, EMA smoothing, daily UTC hlc3 VWAP), configurable lengths and filters, all six signal-family definitions, and an explicitly opt-in corrected ML calculation (`experimental_ml=True`). Experimental ML is off by default. Alternative MA/source/VWAP-anchor choices are not implemented and are not exposed as if supported.

Tests cover a known Wilder RSI seed, default EMA smoothing, all six crossing directions, strict crossing boundaries, filter independence, volume bypass, anchored volume-weighted VWAP reset, explicit experimental ML, empty clusters, causal prefix results, tentative forming-candle recalculation, invalid candles and gaps. These synthetic checks do not validate TradingView parity or profitability.

## Next implementation steps

1. Review the ML defects and establish a corrected comparison reference or a clearly static-only initial scope. Choose meaningful TradingView samples and matching inputs, symbol, timezone and hourly data.
2. Finish the supported settings schema and numerical comparisons, including the selected MA/source/anchor options. Unsupported choices must be rejected, never silently approximated.
3. Benchmark corrected clustering at the intended history/watchlist size on a small server before enabling live polling. The source's maximum-iteration/data settings can be expensive; do not lower them silently. Reuse confirmed RSI state and bound concurrent work while preserving the defined calculation.
4. Add the chart pane, threshold lines and independent signal visibility controls. Label historical candle-close reference markers separately from recorded live intrabar triggers. Record actual sampled live signal events if the chart is to show them as strategy evidence.
5. Add a durable RSI hourly feed. Prime without orders on startup/gaps, validate warm-up and contiguous data, recalculate the forming candle without committing it to confirmed RSI state, consume each family/direction at most once per hourly candle, and deduplicate combined entries. Account for the source's 3,001-point history rather than silently using the default 1,000-candle fetch.
6. Generalize the current hourly-entry/minute-exit strategy routing so both S/R and RSI use correct freshness limits and one-minute exit readiness/management after restart. Preserve existing strategies' behavior. Add both-direction end-to-end and UI tests before offering a selectable strategy.
7. Prepare a reviewable PR, merge/deploy only when the user resumes tomorrow, then inspect the live chart/Activity with trading still explicitly controlled by the operator.
