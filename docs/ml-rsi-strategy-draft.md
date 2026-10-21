# Corrected ML RSI — source review and implementation

The operator selected **corrected ML + static signals** after the array defect was disclosed. This branch adds the chart indicator, its independent display controls and **RSI 1h → Exit 1m**. No running server or exchange account was accessed or activated during development. TradingView parity and actual exchange acceptance remain unverified.

## Original source

`reference/ml_rsi_900_uploaded.pine` preserves the uploaded source byte for byte, including CRLF line endings. SHA-256: `cf415bc7edbd732e04bf2c11148a2a04d06c4ff5dc418625134fd3a43e3078df`.

| Signal | Direction |
| --- | --- |
| RSI crosses above 30 | Long |
| RSI crosses below 70 | Short |
| RSI crosses above upper ML threshold | Long |
| RSI crosses below lower ML threshold | Short |
| RSI crosses above lower ML threshold | Long |
| RSI crosses below upper ML threshold | Short |

Default calculations: close-source RSI 14, EMA smoothing 4, volume greater than its SMA 20 × 1, daily UTC hlc3 VWAP, SMA 7/50 alignment, MACD histogram filter disabled. Static signals can bypass the volume condition independently. The RSI settings and filters are separate from S/R entry filters.

The source's Show All/Static/ML variables control plots, not combined alerts. THOTH therefore separates **chart visibility** from **entry signal selection**. Multiple agreeing signal families produce one entry; conflicting directions produce no entry.

## Disclosed defects and explicit corrections

- Source lines 185 and 216 allocate three empty distance slots before appending the actual distances. Corrected working arrays contain only actual values.
- Line 197 similarly preallocates three empty centroid slots before appending means. Threshold slots 0/2 then refer to empty values. Corrected centers contain the three actual means.
- The source's `last_bar_index` history selection appends without trimming during live operation. The corrected calculation keeps the most recent `max_data + 1` **valid RSI observations**, with bounded checkpoint storage.
- Empty clusters would have unavailable means. The corrected calculation retains the previous center for an empty cluster and keeps centers ordered so lower/upper thresholds retain their meaning.
- Clustering has a bounded iteration count and a numerical convergence tolerance. Unconverged results do not emit ML signals.
- Performance Memory (`perfAlpha`) and threshold range/step (`factors`) do not feed the uploaded signal calculations. They are omitted rather than given invented effects.

These are intentional corrections, not a claim of literal equivalence to the supplied script. A corrected Pine comparison is still required. Flat all-zero RSI seeds remain unavailable rather than inventing a numeric value. All supported MA/source/anchor combinations are configurable but not individually certified against TradingView's imported library.

## Calculation and state

`thoth/rsi.py` implements Wilder RSI, the ten source smoothing choices (SMA, Hull, Ema, Wma, Dema, RMA, LINREG, TEMA, ALMA, T3), explicit OHLC/hlc3/ohlc4 sources, volume/SMA/MACD filters and UTC VWAP anchors. ALMA uses the source's offset 0; T3 uses its factor 0.7.

Sorted prefix sums and nearest-center partition boundaries avoid repeated allocation of a three-distance array for every sample at every iteration. An independent nearest-center implementation tests the corrected result, including tied/empty clusters. A synthetic local check replayed 4,000 hourly bars in approximately 0.7 seconds and recalculated a tentative forming observation in approximately 1 ms. This does not establish the VPS's capacity at a large watchlist size.

Only closed candles advance checkpoint state. Each forming preview uses a temporary copy and cannot alter prior results or commit the unfinished candle. The original offline default-settings prototype remains in `tools/ml_rsi_draft.py` as a reference; trading uses `thoth/rsi.py`.

## Chart

The RSI pane is available independently of selected trading strategy and chart timeframe. Appearance separately controls:

- RSI pane visibility.
- All RSI signal arrows.
- Arrows on the price chart and arrows in the RSI pane.
- Static 30/70 arrows and ML arrows.
- RSI VWAP on the price chart.

These are device-local display settings and generate no trading-settings writes. Per-family colored triangles identify RSI reference calculations; hover labels distinguish them from independently selectable S/R/SMA references; E/X mark recorded trades. Reference calculations do not prove that a live intrabar trade occurred. Partial ML windows are labelled. Chart refresh cadence can differ from the execution worker's cadence.

## Strategy

**RSI 1h → Exit 1m** fixes entries to forming hourly candles and requires the existing one-minute SMA exit. Long closes when SMA 7 crosses below SMA 50; Short closes when it crosses above. Neither waits for candle close. Observation is sampled, nominally about every five seconds, with network/quota/watchlist delays possible.

The source's six signal families are combined according to the saved static/ML entry switches and RSI filters. A durable cursor consumes each family at most once per hourly candle, including when blocked/paused; initial or stale observations prime without replaying an entry. Multiple agreeing events collapse to one directional signal; conflicts suppress entry. Entry/reversal intents expire after 30 seconds.

When ML entry signals are selected, new entries require the full configured valid-RSI window plus contiguous indicator warm-up. Initial bootstrap requests at most four older pages through the shared paced client and persists them. Normal cycles then reuse durable history and fetch only the newest page. Static-only mode avoids the extra ML bootstrap. A newly listed coin without enough history stays in warm-up. Missing RSI data blocks that coin's RSI entries; independent minute exits and account risk management continue.

The order journal, owned positions and closed history retain strategy/entry/exit timeframe metadata. Restart recovery restores management with entries paused and preserves minute exit routing. All existing dollar stops, account risk limits, per-coin cooldowns, opposite-signal settings and optional dollar take profit apply. Changing strategy still requires paused entries, flat managed positions and reconciled orders.

## Validation and remaining external checks

Synthetic tests cover a known Wilder RSI seed, default static signals/filter comparison, independent corrected clustering, all smoothing choices' checkpoint equivalence, bounded history, causal previews, invalid/gapped data, VWAP resets, MACD direction gates, independent S/R settings, duplicate/conflicting/stale events, bootstrap reuse, both actual synthetic hourly entry directions, minute exits after restart and timeframe metadata in history.

Desktop/mobile browser fixtures check pane/arrow visibility without settings writes, nested RSI settings saves, strategy selection, mandatory minute exits, independent chart timeframe, take-profit controls and existing navigation/chart interactions. No test places a real order.

Before treating this as verified live behavior: compare the corrected RSI against a corrected Pine reference using identical inputs/history/timezone, then inspect deployed indicator values and actual Bybit order/protection/exit results. Existing unverified-live authorization remains explicit; no new automatic activation or mandatory testnet gate is introduced.
