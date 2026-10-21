# THOTH

Self-hosted mobile trading desk for Bybit USDT perpetual support/resistance breakouts. The app runs **live trading only**. Orders use real funds; account checks and explicit activation are required. The S/R strategy comparison remains unverified and passing software tests do not establish profitability.

## Development

Python 3.12+, one application process per data directory:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m uvicorn thoth.app:app --host 127.0.0.1 --port 8080 --workers 1
```

Without private credentials and the live server flag, the dashboard can show public market data but cannot activate trading. Configure authentication before public hosting. See [deployment](deploy/README.md). Local tests use simulated exchange transports; these are development fixtures, not selectable app modes or real exchange verification.

## Pages and strategy selection

Use the PWA navigation for **Chart**, **Live positions**, **History**, **Analysis** and **Settings**. Each is a separate bookmarkable page (`#chart`, `#positions`, `#history`, `#analysis`, `#settings`) with browser Back support. Opening pages or charts never activates orders. Trading-symbol selection and risk configuration live on Settings.

**Entry logic** offers the original S/R breakout strategy or **SMA 7 crosses SMA 200**. The SMA strategy enters Long when the confirmed fast average moves from at/below the slow average to above it; the reverse crossing enters Short. It requires 200 bars for averages and a subsequent bar to detect a crossover. It uses no S/R or separate SMA filter, does not repeatedly enter merely because SMA 7 stays above/below, and excludes forming candles. Average windows restart after a data gap; the live feed still rejects missing market history. Existing saved strategy selection is preserved; changing strategy requires no open managed exposure or unresolved orders.

**Opposite signal action** is separate from the entry strategy: **Reverse** closes the old same-symbol position, waits for exchange flat/realized-result confirmation, and submits the opposite entry while the signal is fresh and entry/risk limits allow it. **Close only** retains the exit without a replacement order. These are one-way position reversals, not simultaneous hedge positions. An account/risk lock may permit an exit while blocking the new entry. Blocked-reversal activity now identifies the entry-disabled flag, account status/reason, and any symbol restriction instead of a generic account/risk message. This does not alter activation or risk rules.

History records confirmed closed trades in an append-only SQLite ledger, keyed by the owned entry identity. Retained older closures are imported once on upgrade; trades already pruned from older versions cannot be reconstructed. New records include side, entry, quantity, strategy, realized P&L, reason and times; missing legacy fields are shown as unknown. Pre-strategy imported records are identified as S/R Breakout because S/R was the sole strategy in that schema; explicit strategy tags are preserved. Losing history rows have a light red background and red P&L; winning rows use light green and green P&L, while breakeven rows stay neutral. Analysis tables use the same profit/loss colors. Filter by complete symbol and page through records. The Analysis page summarizes this ledger by coin and strategy.

## Strategy and risk

In S/R mode, resistance breakout enters long; support breakdown enters short. Major/minor levels use long/short pivot periods (defaults 21/5) with an optional SMA 200 filter. Only confirmed candles generate entry signals. Opposite signals request same-symbol reduce-only exits; reversing into a new position waits for flat status, realized-P&L reconciliation and all entry gates. First observation warms up rather than replaying an old signal.

Default size is $50 notional at 20×, approximately $2.50 initial margin, with a $1 per-position stop. Other defaults: three concurrent positions, 3% daily account loss, three consecutive losses per coin and a 60-minute per-coin pause. All strategy/risk values remain editable. Exchange quantity steps round down; unsupported orders are skipped. Isolated margin, one-way mode and a dedicated USDT-only UTA 2.0 account are required by the selected configuration. The bot checks account margin mode rather than silently changing it.

Daily protection closes every managed position, including winners. Its allowance is starting equity × the configured percentage, measured down from the highest tracked transfer-adjusted equity. The optional overall stop counts losing P&L only, against the chosen margin basis, and requires manual restart. Fees, funding, slippage and outages can cause losses beyond the configured dollar threshold. Unused account funds do not automatically support isolated positions; review Bybit auto-margin replenishment separately.

A stop failure closes and blocks the affected symbol. Healthy symbols retain management; new submissions wait while execution outcomes remain unresolved. Account-wide monitoring failures block new entries, while fresh opposite-signal exits still attempt reduce-only closes after ownership checks. Explicit activation can clear a settled symbol block. Daily risk and account ownership checks continue to apply.

## Fill and stop confirmation

Activity first records “live breakout order submitted; awaiting fill/protection.” Once the fill is owned and the current exchange position reports the expected stop, a separate event records **“position filled; Bybit stop loss verified.”** It is emitted once per trade and persists across restarts. It confirms a readback at that time, not a guarantee of future execution. Missing/incorrect stops never generate the verification event. Historical activity messages are retained.

## Daily protection and cooldowns

The daily dollar allowance is calculated from starting equity, not the growing peak: **$100 start × 5% = $5 allowance; a $150 peak therefore sets the stop at $145**. New highs lift that threshold while the allowance stays $5 unless the operator explicitly edits the percentage. Open-position P&L is included through verified USDT equity. Deposits/withdrawals are neutralized rather than treated as trading profit/loss. The tracked peak and daily baseline persist through restart and reset only at the next established UTC day.

The dashboard shows remaining allowance in dollars and as a percentage of starting equity, allowance used below peak, the tracked adjusted peak, and the current actual account-equity threshold. This is sampled account protection: an intra-sample peak may be missed and execution can overshoot. Earlier intraday highs were not retained by old versions and cannot be reconstructed on upgrade; tracking begins from verified available samples, with any old daily-stop latch preserved.

**Per coin** is the consecutive-loss default; **Whole account** remains an explicit alternative. Each coin’s realized losing closes count only toward its own pause. A profitable close resets that coin’s streak; breakeven leaves it unchanged. Pauses retain their deadlines across restarts and do not extend on every poll. Stops and opposite-signal exits continue; only the paused coin’s new/replacement entries are withheld. Other coins can reverse normally. Fresh signals after expiry may enter; signals observed during the pause are not replayed. The account-wide daily stop remains separate.

The previous shared streak cannot reliably identify per-coin history, so migration starts separate coin counters for newly reconciled results. It does not convert the old shared pause into pauses on every coin. The historical aggregate counters remain available only if Whole account is selected. Deployment still leaves new entries paused for manual activation.

Live countdowns show remaining time for every paused coin, or the whole-account countdown when that mode is selected. They use the server clock and do not independently enable orders. Activity shows the latest **30** messages in a scrollable list, newest first.

For live activation, type **ENABLE LIVE ORDERS**. The app now validates the phrase locally and the backend names the selected environment correctly. Saving settings does not require that phrase. A saved limit change never automatically clears a daily stop. During manual activation, a latched daily stop is reviewed only after account authentication, flat positions, settled ownership/orders, and a fresh equity sample within the saved allowance. If exhausted, it stays stopped. The day’s starting equity and peak are preserved; operator review is logged. No credentials are displayed.

## Configurable trailing stops

Trailing is disabled by default. Settings supplies **Trailing activation (%)**, **Trailing stop execute (%)**, and editable percentage bases. The selected defaults are activation from profit / original margin, and trailing distance from the best favorable mark price. With $2.50 margin, 10% activation means $0.25 unrealized mark-price profit before fees. At 20× this is roughly a 0.5% favorable entry-price move. A 1% execute distance follows the best observed price by 1%, independently of leverage. Alternate price-based activation and initial-margin-based distance are selectable.

Once activated, long trades track higher marks and short trades lower marks. The resulting price is rounded toward protection to the instrument tick and combined with the original dollar stop, so it never loosens protection. A narrow distance below one tick is rejected at entry. Activation does not guarantee a profitable exit. Order sizing, risk limits and signal reversals remain in force.

**THOTH samples marks on its risk-monitor loop and places each tightened fixed stop on Bybit**, with mandatory readback. This is not Bybit’s native continuously moving trailing order. Peaks between samples or while offline are not reconstructed. During a server/network outage the last verified fixed stop remains on Bybit, but does not continue trailing; actual losses can exceed thresholds because of gaps, fees or outages. A crossed sampled stop requests a reduce-only exit. An unconfirmed update closes/blocks the affected symbol; account-wide monitoring failures still block new entries.

Trailing settings are frozen into each new entry’s durable intent and remain attached to that trade after settings edits/restarts. Existing trades from before this feature stay unmodified. Favorable peaks, pending stop updates and confirmed stops persist; a timeout is reconciled against the exchange before any retry, without reverting a tightened stop to the old dollar stop. Restart never restores permission to open new entries.

The chart shows a cyan activation line and a purple active trailing exchange-stop line when in view. **Fit all levels** includes these levels in scaling. Live Positions shows waiting/active trailing state. Chart interaction never changes stop settings or places orders.

## Mobile gestures and analysis

On the chart, one finger pans horizontally and vertically; two fingers pinch to zoom time around their midpoint. The right price axis still supports price scaling, and Reset/Latest restores the view. Native page scrolling is available outside the chart. Gesture cancellation and redraws release touch state; auto candle refresh pauses during a gesture. Navigation fits the mobile viewport.

Analysis filters by coin and strategy and uses every retained ledger record, not just the visible history page or the latest 200 closures. It reports closed trades, wins/losses/breakeven, realized P&L, win rate, average trade P&L and profit factor, including a per-coin S/R-versus-SMA comparison. Win rate is wins divided by all closed trades, including breakeven; profit factor is gross winning P&L divided by gross losing P&L and is unavailable without losses. Empty samples and unavailable legacy fields are not fabricated. Open P&L, other account trades and previously pruned history are excluded. Small historical samples do not predict future performance.

## Connection and activation

Private API key/secret files stay on the server. **Check connection / refresh balance** is read-only and cannot enable trading. Balance snapshots expire after five minutes. Use an IP-bound key with contract order/position permissions and withdrawals disabled. The app checks account identity, dedicated-account conditions, margin mode, outstanding orders and risk before activation.

Live requires server opt-in, dashboard authentication, private live credentials and the phrase `ENABLE LIVE ORDERS`. By default an indicator reference comparison is required; the existing `compose.live-unverified.yaml` override explicitly allows an unverified strategy. There is no testnet receipt or simulation prerequisite. Neither compose file automatically enables entries.

## Persistence and upgrades

Existing **live.sqlite**, execution journals and candle/checkpoint databases remain in the same volume and paths. `THOTH_DB` is retained as a compatibility locator: its parent selects the data directory; no simulation database is opened. Old simulation/testnet files are left untouched, but their workers and API routes are no longer present. Do not delete or recreate the data volume during an upgrade.

After restart, recorded exit obligations recover and new entries stay paused until manual activation. Exactly one application process is required. Durable order IDs prevent blind resubmission after ambiguous outcomes. Account monitoring runs roughly every two seconds between complete cycles; public market requests are paced. Exchange-side stops remain essential during outages.

Daily baselines use UTC. A missed midnight baseline requires a flat-account review; daily starting equity and peak cannot be manually reset. A latched daily stop can be explicitly reviewed while flat/reconciled if current equity is inside the saved allowance, including after an operator increases the percentage; increasing it increases risk. No restart erases prior drawdown or automatically enables entries. Private backups can be created with `tools/backup_data.py` and restored only after exchange reconciliation. HTTPS, private file mounts, login sessions, origin/host checks and a network-only PWA service worker are included. Offline orders are never queued.

## Indicator reference and software checks

The supplied Pine source and its MPL-2.0 license are retained. Generate a comparison export with `python tools/reference_export.py /tmp/thoth-export.pine`, then use `tools/compare_indicator.py` with matching symbol/timeframe/inputs. Reference receipts bind indicator source and strategy; no parity claim is inferred from unit tests.

CI runs unit/API tests, JavaScript syntax, indicator generation and release packaging. `python tools/browser_smoke.py` exercises desktop/mobile controls with synthetic fixtures and no exchange orders. Some shared execution modules retain historical internal names; live code reuses those tested components without creating a testnet service. `thoth/engine.py` remains an offline risk-test helper and is not imported or started by the app.

Build a secret/database-free release using `python tools/package_release.py dist/thoth-release.tar.gz`. Workspace checks do not deploy changes to a running VPS or place real trades.

### Chart and trading watchlist

The saved trading-symbol summary is separate from the chart symbol. Search, select matching results, clear all, remove selected chips, or use **Use chart symbol only**, then save. Edits do not affect trading until saved. The save action includes strategy/risk edits; symbol changes are locked while exchange entries are enabled. Activation displays the saved trading list and rejects unsaved edits.

The responsive chart supports hover/tap OHLC inspection, a crosshair, candle zoom, history navigation, latest-candle return, and independent S/R, SMA and breakout display toggles. These toggles do not change strategy settings. Candle-based scaling keeps price action visible; **Fit all levels** includes distant overlays/stops. Where available, positions show the exchange-reported stop instead of an estimate.

Optional browser validation: install Playwright in your development environment and provide Chromium at `/usr/bin/chromium`, then run `python tools/browser_smoke.py`. It uses isolated temporary state, simulated API routes and no credentials/orders. The screenshots `docs/desktop-watchlist-fixture.png` and `docs/mobile-watchlist-fixture.png` are synthetic browser fixtures, not real trading results.

### Interactive chart appearance

Use **Search symbol** beside the chart dropdown, or click a symbol name in the trading list, to view a chart without changing allowed trading symbols. Checkboxes still control trading selection and require Save. Drag candles horizontally with the mouse to pan; **Latest candle** returns to the live edge. **Maximize chart** expands to the browser viewport and **Restore chart** returns to the dashboard.

The **Appearance** side panel changes bullish/bearish candle and signal colors, signal size, SMA color, and each long/short major/minor S/R family (support/resistance colors, solid/dotted/dashed styles, width and visibility). Display preferences persist on that browser/device, with a reset to the supplied Pine styles; they never change the trading configuration.

S/R drawing now uses horizontal rays from the pivot anchors, showing the latest confirmed levels available at the rightmost viewed candle, with room to their right. This matches the original Pine drawing style rather than connecting historical levels with vertical stair steps. Rays are drawn retrospectively back to pivots only for display; this does not backdate signals or certify matching TradingView values. Long-term major defaults to solid width 4, long-term minor dotted width 2, short-term major dashed width 3 and short-term minor dashed width 1. The colors come from the original supplied Pine.

Updated synthetic previews: [desktop chart](docs/desktop-interactive-chart-fixture.png), [mobile chart](docs/mobile-interactive-chart-fixture.png). They demonstrate browser rendering and controls, not actual account results.

### Mouse chart navigation and saving one symbol

Saving a strategy with exactly one allowed trading symbol now opens that symbol's chart after the save succeeds. With multiple symbols, chart selection remains independent. Mouse wheel over the chart zooms around the pointer; holding the left mouse button and dragging moves both time and price. One-finger touch dragging moves time and price; two-finger pinch zooms candle count. Scroll outside the chart to move the page. **Latest candle** clears manual price/time panning; **Reset view** also restores the default candle zoom. Automatic candle refresh pauses during manual time/price exploration and resumes at the live edge. These controls do not change strategy settings or enable orders.

### Price scale, extrema, exact candle times and current quote

Drag vertically or scroll over the **right price axis** to compress/expand the price range independently from time zoom. Double-click that axis or use **Auto price scale** to restore its automatic range. **High / Low** tags show extremes of the currently visible candles, rather than all exchange history; off-scale tags use directional arrows. Every displayed candle has a time tick: one minute on 1m data and one day on Daily data. Printed tick labels adapt to the available width to avoid overlap; hover/tap any candle for its exact UTC minute/date in the crosshair label.

The **Now** price tag and current-price readout use Bybit's public last-traded-price endpoint, refreshed every five seconds with a five-second bounded server cache. They are independent of account credentials, timeframe, trading activation and signal calculations. The quote is separate from OHLC candles (refreshed every fifteen seconds at the live edge), is not a liquidation/mark price, and is hidden when unavailable or stale. Historical chart exploration can show a present-time quote; it is labelled **Now** to distinguish it from the candle under inspection. Candles may include a forming bar; entries continue using confirmed signals.

### Volume, SMA 7 and chart appearance

**Appearance** now includes adjustable **Right spacing (bars)** (default 24), **Chart background**, and **Show current price line and label**. The current-price line/tag are yellow, with dark tag text. Hiding them affects the chart only; the timestamped quote readout remains available. Display preferences are stored on this browser/device.

The chart adds a separate volume pane with bullish/bearish bars and a simple 20-bar volume MA, plus a white 7-bar SMA of closing prices on the price pane. Both default on and can be hidden in Appearance. They are display-only indicators, independent of the SMA 200 strategy filter and never used for orders. Averages use available candle history before the visible window and only appear after enough valid bars; missing volume is not fabricated. The volume pane uses its own scale and the same time coordinates as price. Forming-bar values can change as candle data refreshes.

### Current-price alignment

Each fresh public quote now updates the displayed forming candle's close and observed high/low, including its display SMA 7. This does not mutate fetched candle records, volume, closed candles or strategy analysis. Quotes for another symbol/environment, stale quotes, and quotes outside that candle's time window are ignored for candle synchronization. At a candle boundary the live view requests fresh candle data rather than inventing a new bar.

The yellow price label stays at the exact price-line height when on scale. High/Low labels move to avoid it; they cannot shift the current-price label. Manual off-scale prices retain directional tags. Refresh cadence remains five seconds for quotes and fifteen seconds for candles, subject to network availability; this is not a tick-by-tick WebSocket feed.

### Candle countdown

A second-by-second countdown appears under the yellow current-price tag and above the chart: `MM:SS` for 1m and `HH:MM:SS` for Daily, counting to UTC candle boundaries. It uses the available Bybit clock offset plus a fresh server-time sample even when the five-second price quote is cached. The device clock is the fallback before synchronization. The timer tracks the current market candle, including while viewing history; it does not predict a signal or submit orders. Hiding the current-price tag also hides its attached timer; the separate countdown readout remains visible. Quote refresh cadence remains five seconds.

### Extended chart history and measurement ruler

**Load 1,000 older candles** expands the chart from the initial 1,000 to at most 5,000 candles (about 3 days 11 hours on 1m), subject to exchange listing/data availability. **Oldest loaded** jumps to the left edge; drag or use Earlier/Later to explore. The loaded UTC date range and candle count are shown. Pagination is bounded, cached briefly, deduplicated, and rejects non-advancing pages or gaps rather than fabricating bars. Failed older-history requests preserve the current chart.

Extended S/R signals are a separate historical replay of fetched data. They never update execution checkpoints or send orders. As additional warm-up history becomes available, early inferred levels/signals can change; they are not a backtest or a substitute for execution logs and TradingView comparison. More data increases chart-load time.

Activate **Measure move**, then click two chart points or drag between them. The ruler reports signed USDT price change, percentage from the start price, candle intervals and elapsed time; endpoints stay attached to their price/time coordinates while panning. **Clear measure** removes it. This is a chart price measurement, excluding leverage, fees, funding, liquidation and realized trade performance. It does not select trading symbols or submit orders.

Extended-history snapshots refresh only on request to limit server work. **Latest candle** returns to the normal 1,000-candle live view and resumes automatic candle refresh. Quote refresh remains separate. The public historical-page cache is also reduced.

### Measure move button

The **Measure move** button is now in the chart's top toolbar. Activate it, then hold/drag/release or click a start and end point. Measurement input automatically stops at the second point; its percentage, signed price move, candle intervals and elapsed time stay visible on the chart. Ordinary dragging returns to panning. Click **Measure move** again for a new measurement or **Clear measure** to remove it. While measuring, Android touch drags stay in the chart instead of scrolling the page. The ruler uses SVG screen coordinates so responsive chart scaling does not shift the selected points. This remains a display-only price measurement, not an order or leveraged P&L calculation.

### Temporary Bybit account rate limits

Bybit code `10006` is a rate-limit rejection. Authenticated clients share pacing, with position-list reads spaced at least 0.5 seconds apart. GET requests retry explicit rate-limit rejections up to three total attempts, using a bounded shared backoff informed by numeric exchange reset headers. Each attempt signs a fresh timestamp. Order writes and ambiguous network failures are not automatically retried by this mechanism.

If account reads remain rate-limited, new entries temporarily wait while existing exchange stops remain in place. Previously enabled entries resume only after a complete successful monitoring pass, including position ownership, protection, wallet, transfers, and risk checks. Pause, settings changes, restart, manual close-all, and genuine safety/risk locks cancel this automatic resume intent. Previously latched safety locks still require manual activation after review. This does not treat a write rejection or an unknown order outcome as a recoverable read interruption. Diagnostics retain only allowlisted numeric quota fields, never request authentication headers. Coin-specific SMA data interruptions remain scoped to the affected coin.

Validation uses synthetic exchange responses and browser fixtures; it does not establish actual Bybit quota capacity or live recovery on the deployed server.

### Hourly S/R entries with minute exits

Select **S/R 1h → Exit 1m** in Settings. This separate strategy fixes the entry timeframe at one hour and requires the existing intrabar SMA 7/50 exit on one-minute data: Long closes on a downward cross, Short on an upward cross. Both Major and Minor are enabled by default and remain selectable. Optional S/R entry filters still apply only when selected. Existing S/R Breakout and SMA 7/200 strategies retain their candle-close entries and selected-timeframe exits. Changing strategy requires paused entries, flat managed positions and reconciled orders.

The entry worker replays **closed hourly candles** to maintain confirmed pivot levels. It compares successive current hourly candle closing-price observations against unchanged confirmed levels, without waiting for the hourly close. It samples on the worker's roughly five-second cadence; network delays and watchlist size can lengthen this. It cannot guarantee execution on every tick or capture a crossing that reverses between samples. Historical candle highs/lows are not used to replay missed entries. The first observation, changed levels, or observations more than 30 seconds apart prime the detector without entering. Crossings are consumed even when filtered or paused; at most one trigger per level family per hourly candle is retained, including across a restart. Simultaneous qualifying levels yield one directional signal, and conflicting directions yield none. Intrabar entry/reversal intents expire after 30 seconds.

The order journal, owned position and trade history retain strategy and entry/exit timeframe metadata. Restart recovery restores position management with entries paused, and new-strategy positions continue using minute exits regardless of the displayed chart. Fixed-dollar exchange stops and all account/coin risk controls still apply. The configured opposite-signal action also remains applicable to opposite hourly entry signals. SMA exits close only; a subsequent entry needs a fresh entry signal. Trailing remains optional and is not enabled by selecting this strategy.

Historical OHLC cannot reconstruct the sampled live intrabar crossings. This strategy's chart therefore shows confirmed S/R levels, candle-close S/R reference arrows on the displayed timeframe, and recorded E/X trades. The reference arrows are explicitly labelled and do not claim to reproduce live intrabar entries. Chart timeframe remains independent, and these display calculations cannot submit orders. Synthetic tests cover hourly fetch/dispatch, Major/Minor selection, repeat suppression, stale/missing observations, durable detector state, both minute-exit directions after recovery, timeframe metadata in closed history, and the independent minute chart. Browser fixtures check strategy selection, mandatory minute exits, activation text and switching back to existing strategies. Real exchange behavior and TradingView parity remain unverified.


Live Positions and History show recorded opening dates/times to the second in America/New_York (EDT/EST). UTC timestamps with stored millisecond precision are available on hover. Live snapshots store milliseconds; historical opening fields store seconds, and the display explicitly converts each format. These are recorded entry-order creation times, not separately verified execution-fill timestamps. Missing older opening times display —. History abbreviates either SMA 7/50 intrabar exit direction to **SMA 7/50 (EXIT)**, preserving the complete stored reason on hover; other reasons are unchanged. This is a display change and adds no exchange requests or trading-rule changes.

### Optional dollar take profit

Settings provides **Enable take profit for new positions** and a gross USDT target per position. It defaults off. The initial market order includes an exchange-hosted full-position MarkPrice-triggered market TP alongside its existing stop loss. After a fill, THOTH recalculates the price using the confirmed entry and filled quantity, rounds away from entry to the supported price tick, and verifies the reported TP on a fresh position read. Prices implying a non-positive short target or less than one tick are rejected before entry when detectable. Fees, funding, tick rounding and execution slippage mean realized net P&L will differ from the target.

TP settings are captured per order and owned position: changing saved defaults affects new positions, not existing ones. Restart recovery retains the original TP. SMA exits, opposite-signal handling and account stops can close a position earlier. A TP update sends no stopLoss field and preserves the existing fixed/trailing stop. If the TP is missing or an update fails, only its verification is marked unavailable; THOTH retries after fresh reads with a ten-second minimum delay, retains existing risk management, and does not pause unrelated coins. Sampled unrealized gross P&L at/above the target also requests a reduce-only close as a fallback. A POST acknowledgement is not labelled verified. Exchange closes retain the generic exchange-stop/close history reason unless THOTH itself requested a specific close; the bot does not invent a take-profit fill classification.

Validation uses synthetic long/short fills, slippage, readback, retained stop/TP configuration, coin-local update failures, restart and reduce-only fallback, plus browser settings-save checks. Actual Bybit TP placement and fills remain to be verified after deployment.


### Corrected ML RSI and RSI 1h → Exit 1m

The uploaded Pine reference is preserved in `reference/ml_rsi_900_uploaded.pine`. Its ML arrays allocate empty slots before appending values; the corrected implementation is explicitly labelled **Corrected ML RSI**. The operator selected corrected ML plus static RSI signals after this defect was disclosed. Corrections use valid rolling RSI history, ordered centers, retained centers for empty clusters and a convergence check. The source's unused Performance Memory and threshold-range controls are not given invented effects. See [the source review](docs/ml-rsi-strategy-draft.md).

Chart Appearance independently controls the RSI pane, all RSI arrows, arrows on the price chart/in the RSI pane, static/ML arrow groups, and the RSI VWAP line. Hiding any of these never changes trading settings. `R▲`/`R▼` mark RSI reference calculations; ordinary triangles retain the existing chart strategy reference, and E/X identify recorded trades. Partial-history ML windows are labelled. The chart remains independent of the entry timeframe and refreshes at the existing chart cadence; it cannot show every execution-worker sample.

Settings includes **Corrected ML RSI settings**, separate from S/R filters. Default calculations follow RSI 14/EMA 4, volume MA 20 × 1, daily UTC hlc3 VWAP, SMA 7/50 alignment, and disabled MACD histogram filtering. The source's ten smoothing choices, explicit OHLC/hlc3/ohlc4 sources, lengths, UTC VWAP anchors and filter switches are configurable. Static 30-up/70-down and both ML threshold crossings are independently selectable for entries. Crossing either ML threshold upward is bullish; crossing either downward is bearish, subject to the saved RSI filters. Both chart visibility and unused S/R settings are excluded from RSI signal decisions.

The selectable **RSI 1h → Exit 1m** strategy samples the forming hourly candle without waiting for close. Its mandatory one-minute exit closes a Long on SMA 7 crossing below SMA 50, or a Short on crossing above. These are sampled server-managed exits, not every-tick guarantees; exchange dollar stops remain primary protection. The configured opposite-signal action, account risk limits, per-coin cooldowns and optional take profit still apply. Paused entries and restarts do not replay consumed signals or grant entry permission. Strategy, entry timeframe and exit timeframe remain in the durable order journal, positions and history after recovery.

Only closed candles advance RSI state; a forming observation uses a temporary copy. A durable cursor consumes each signal family at most once per hourly candle, collapses agreeing families to one entry and suppresses conflicting directions. Initial/stale observations prime without entries. Signals and queued reversals expire after 30 seconds. New RSI entries require complete contiguous warm-up, including the full configured ML window when ML is selected. The initial feed backfills at most four older pages through the shared paced client, persists history, then normally fetches only the newest page. Static-only mode avoids extra ML bootstrap pages. Unavailable RSI data blocks that coin's RSI entries while other coins and independent minute-exit/account management continue. Large watchlists and quota/network delays can extend the nominal five-second observation cadence.

Numerical/state tests cover the known RSI seed, default static/filter comparison, an independent corrected clustering reference, all smoothing choices' checkpoint equivalence, bounded history, causal preview, volume/VWAP/MACD gates, duplicate/conflicting/stale signals, bootstrap caching, and actual synthetic hourly RSI entries in both directions followed by minute exits after restart. Desktop/mobile browser fixtures cover pane/arrow visibility without settings writes, RSI settings saves, strategy selection and independent chart timeframe. A local synthetic 4,000-bar replay took approximately 0.7 seconds and a tentative observation approximately 1 ms; this is not a VPS capacity guarantee. TradingView parity and real exchange RSI/TP behavior remain unverified; no test activated real trading.
