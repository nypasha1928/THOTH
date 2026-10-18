# THOTH

Self-hosted mobile trading desk for Bybit USDT perpetual support/resistance breakouts. Includes paper simulation, opt-in testnet execution, and separately gated live execution. **No exchange account is connected and live trading is disabled in the prepared environment.** This is a release candidate awaiting external acceptance, not a claim of profitability or production safety.

## Start the app

Python 3.12+, one process per data directory:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m uvicorn thoth.app:app --host 127.0.0.1 --port 8080 --workers 1
```

Open it in a local browser. For a phone, deploy behind HTTPS using [the deployment guide](deploy/README.md). A public server/domain has not been purchased or provisioned. Local mode is passwordless unless a password is configured; never expose that mode publicly.

The **Funds view** selects Paper, Testnet or Live. Settings, balances, positions, histories and databases are separate. Merely selecting a view does not enable orders. Start/Pause/Close-all acts on the selected mode.

## Strategy

- Resistance breakout → long; support breakdown → short.
- Major and minor levels from long/short pivot periods, default 21 and 5.
- SMA filter, default length 200: long close above it; short close below it.
- Only confirmed candles enter. Pivot levels become available after their confirmation delay, never backdated into tradable signals.
- Daily and 1-minute timeframes.
- No bounce/rejection entries, volume filters, standalone SMA crossover entries or SMA exits.
- Opposite valid signal closes the same symbol, waits for flat position and realized-P&L reconciliation, then reverses while the signal is still fresh.

The Python major/minor state machine is derived from the supplied TFlab Pine source under MPL-2.0. The reference, license, generator and comparison tools are included. **Actual TradingView parity remains unverified.** Causal tests and restart/pruning equivalence are not a substitute for reference data.

## Editable sizing and risk

Defaults are $50 estimated notional at 20× (about $2.50 initial margin), $1 per-position stop, three concurrent positions, 3% daily equity loss, and three consecutive losing closes causing a 60-minute entry pause. Bybit quantity steps round down; unsupported/minimum-size orders are skipped, never silently enlarged. Actual market fills/fees can change the final notional and loss.

- Daily limit: close all THOTH positions, including winners, and cancel pending THOTH entries. Transfer-in/out cash flows are neutralized so deposits do not hide trading losses. A current-day daily stop cannot be manually bypassed.
- Resume next UTC day is editable. Paper defaults to automatic; exchange modes default to manual re-enable. Server restart always pauses exchange entries while recovering exit obligations.
- Individual dollar stops freeze at entry. They are submitted with the order, adjusted to actual fills, verified on the exchange, and monitored. Missing/failed protection triggers a reduce-only close attempt and locks entries.
- Optional overall position-loss stop defaults off, with 20% selected. Winning gains do not offset losses. Choose denominator: losing positions' initial margin, or all open positions' initial margin. It closes all managed positions and requires a flat, settled account before manual restart.
- Max positions, leverage, size, loss percentages, loss streak, pause duration, timeframe, pivots, level families and SMA settings are editable. Pause exchange entries before edits. Existing positions retain original size/stop.

Isolated margin is the selected default, as requested. The bot verifies the account setting and does not change account-wide margin mode automatically. Only dedicated, USDT-only UTA 2.0 accounts in one-way position mode are supported. Existing unrelated positions/orders block activation.

## Chart and mobile interface

Candlesticks, causal major/minor overlays, SMA, breakout arrows, position entry/stop lines and recorded exits; zoom/history navigation and tap-to-inspect OHLC. Automatic refresh runs every 15 seconds at the latest bars and pauses while browsing older history. Testnet uses testnet prices; paper and live use Bybit public production prices. Unavailable data shows an error, never fake prices.

PWA manifest/icons and a network-only service worker support installation where the phone/browser permits it. Offline controls are unavailable and orders are never queued. Phone-specific installation and push notifications have not been validated/implemented respectively. Browser screenshots labeled `fixture` use synthetic visual-test data, not real trades.

Uploaded watchlist: 234 entries, 224 Bybit perpetual candidates. Other exchange/type entries are retained but disabled. Every selected exchange order still requires current instrument/quantity/leverage checks; the imported list is not a verified current exchange listing.

## Account connection and activation

Credentials are provisioned as private server files, never through the dashboard, chat, Git or image builds. The read-only connection check exposes status only, not keys/account IDs/upstream response bodies.

Testnet requires private testnet key/secret files, dashboard authentication, a server-side flag, a dedicated-account acknowledgement, matching isolated margin, contract order/position permissions, and withdrawals disabled. The operator must type `ENABLE TESTNET ORDERS` in the app. See `compose.testnet.yaml` and `compose.execution.yaml`. No credentials are configured here, so actual testnet acceptance is blocked.

Live uses separate files and `compose.live.yaml`. It additionally requires:

1. A matching TradingView reference receipt with at least 500 bars, both signal directions and all level types.
2. A real-exchange testnet trade that was protected, closed and reconciled for this code/strategy, with no unresolved orders. Simulator records cannot generate the receipt.
3. A live API key bound to the server egress IP, contract permissions and withdrawals disabled.
4. Explicit server enabling and the authenticated phrase `ENABLE LIVE ORDERS`.

There is no automatic testnet-to-live switch. Live execution has not been tested against an actual account. After code changes/restarts, exit management can recover for the bound account with entries paused; re-enabling live entries requires current validation receipts again.

## Validation receipts

Generate a TradingView export copy without changing the original:

```bash
python tools/reference_export.py /tmp/thoth-export.pine
```

Use matching symbol/timeframe/inputs, export chart data including indicator columns, then compare:

```bash
python tools/compare_indicator.py exported.csv --settings matching-settings.json --report data/validation/reference.json
```

All exported bars are compared; mismatches return nonzero. Receipts bind the exact indicator code and strategy. Empty or insufficient coverage does not establish readiness. No actual reference receipt has been generated here.

After actual testnet testing, pause entries and fully close/reconcile the account:

```bash
python tools/accept_testnet.py data/testnet.sqlite data/testnet-acceptance.json
```

This refuses simulator-only results and does not activate live mode. `/api/readiness` reports current validation/configuration state; it is not a safety certification.

## Persistence, monitoring and recovery

`data/` contains separate paper/testnet/live state, execution journals and candle databases. `THOTH_DB` changes the paper database location; exchange databases live beside it. Exactly one application process is required. Intent is committed before sending an order; timeouts/duplicate identities are reconciled instead of blindly resubmitted. Accepted, filled, protected and flat are separate states. Partial fills cancel the remainder before final protection/ownership checks. Unknown orders block new entries. Close-all remains active until pending cancellations, late fills, close orders and realized results settle. A stop that closes before the first poll is reconciled rather than treated as a new unowned trade.

Exchange monitoring runs roughly every two seconds between complete cycles, with network latency affecting timing. The candle feed allows four in-flight requests with up to eight request starts/sec. Unavailable unheld symbols are skipped independently; missing held-symbol feed data blocks new entries. Exchange-side stops remain important during server/network failure. Daily/aggregate loss limits cannot execute without exchange connectivity and may overshoot.

Incremental indicator checkpoints survive restarts and retain bounded pivot/window state. Tests compare checkpointed/pruned replay against 6,000 bars of unpruned replay. Up to 5,001 raw bars and 1,001 analysis bars per configuration are retained; gaps beyond the API window fail closed instead of fabricating continuity. Charts and feed share history/checkpoints. Source/history start differences versus TradingView still require comparison.

Exchange daily baselines begin on first activation and sample the new UTC day within a 30-second boundary window. If a midnight sample is missed, entries lock until an operator establishes a new day's baseline while flat. Initial activation mid-day does not reconstruct losses earlier that day. This is sampled risk monitoring, not tick-level protection.

Paper starts with $250 simulated cash; fees/funding, liquidation and executable bid/ask are not modeled. Never use paper results alone as profitability or liquidation evidence.

## Security and backups

Private file mounts, HTTPS proxy, HttpOnly/SameSite cookies, logout revocation, restart session invalidation, login throttling, host/origin checks and a restrictive script policy are included. Backups contain trading data: keep them private. Stop the app for an application-consistent point in time, then use `tools/backup_data.py`; restore into a new directory and verify before switching volumes. See deployment guide.

## Release

GitHub Actions checks tests, JavaScript, source generation and bundle creation. CI has not yet been observed on GitHub. Build a secret/database-free bundle with:

```bash
python tools/package_release.py dist/thoth-release.tar.gz
```

The archive contains a file manifest and an adjacent SHA-256 checksum. Public hosting, actual exchange acceptance, and real-phone installation remain external checks. No real account or credentials have been supplied in this workspace. Bybit production access here returned a country restriction; no VPN or alternate-host bypass is configured.

### Optional unverified live activation

Live normally requires matching TradingView and actual testnet receipts. An explicit server-side override now permits skipping both, while keeping account checks and real-funds confirmation. See [deployment instructions](deploy/README.md#explicit-unverified-live-option). This does not mean indicator behavior or real exchange execution has been verified.

### Read-only balance display

In LIVE or testnet view, **Check connection / refresh balance** reads USDT wallet balance, equity and account unrealized P&L without enabling orders. The display is a timestamped snapshot, valid for five minutes, not the combined value of all account coins. Non-USDT assets or borrowing are reported separately as a trading restriction; they do not hide the balance. Wallet read failures preserve successful API verification but show balance unavailable. Changing credentials or restarting invalidates the cached reading.

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

Saving a strategy with exactly one allowed trading symbol now opens that symbol's chart after the save succeeds. With multiple symbols, chart selection remains independent. Mouse wheel over the chart zooms around the pointer; holding the left mouse button and dragging moves both time and price. Touch dragging remains horizontal so vertical page scrolling still works on mobile. **Latest candle** clears manual price/time panning; **Reset view** also restores the default candle zoom. Automatic candle refresh pauses during manual time/price exploration and resumes at the live edge. These controls do not change strategy settings or enable orders.

### Price scale, extrema, exact candle times and current quote

Drag vertically or scroll over the **right price axis** to compress/expand the price range independently from time zoom. Double-click that axis or use **Auto price scale** to restore its automatic range. **High / Low** tags show extremes of the currently visible candles, rather than all exchange history; off-scale tags use directional arrows. Every displayed candle has a time tick: one minute on 1m data and one day on Daily data. Printed tick labels adapt to the available width to avoid overlap; hover/tap any candle for its exact UTC minute/date in the crosshair label.

The **Now** price tag and current-price readout use Bybit's public last-traded-price endpoint, refreshed every five seconds with a five-second bounded server cache. They are independent of account credentials, timeframe, trading activation and signal calculations. The quote is separate from OHLC candles (refreshed every fifteen seconds at the live edge), is not a liquidation/mark price, and is hidden when unavailable or stale. Historical chart exploration can show a present-time quote; it is labelled **Now** to distinguish it from the candle under inspection. Candles may include a forming bar; entries continue using confirmed signals.

### Volume, SMA 7 and chart appearance

**Appearance** now includes adjustable **Right spacing (bars)** (default 24), **Chart background**, and **Show current price line and label**. The current-price line/tag are yellow, with dark tag text. Hiding them affects the chart only; the timestamped quote readout remains available. Display preferences are stored on this browser/device.

The chart adds a separate volume pane with bullish/bearish bars and a simple 20-bar volume MA, plus a white 7-bar SMA of closing prices on the price pane. Both default on and can be hidden in Appearance. They are display-only indicators, independent of the SMA 200 strategy filter and never used for orders. Averages use available candle history before the visible window and only appear after enough valid bars; missing volume is not fabricated. The volume pane uses its own scale and the same time coordinates as price. Forming-bar values can change as candle data refreshes.
