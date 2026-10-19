# THOTH implementation status

The app now exposes only live Bybit trading. Paper/testnet routes, dashboard controls, workers, compose overrides and the testnet acceptance utility have been removed. Shared execution/risk tests still use isolated fake exchanges; they do not place orders.

Live state, account binding, journals, candle history and exit obligations retain their existing paths. No historical databases are deleted. Existing server configuration and the unverified-indicator override remain compatible. Restart recovery manages known positions with new entries paused.

Activity distinguishes order submission from owned-fill/stop verification. Verification is emitted once per trade only after the reported exchange stop matches the expected dollar stop. Symbol-level protection failures remain contained; account-wide risk and monitoring locks still apply.

Actual TradingView S/R comparison remains unverified. Local tests and synthetic chart screenshots do not establish strategy profitability or guarantee exchange execution. This workspace does not hold the VPS credentials or deploy changes to the user's running server.

## Strategy and navigation update

Entry logic is selectable between S/R breakout and confirmed SMA 7/200 crossover. Opposite signals can reverse or close only; account checks and risk limits still govern replacement entries. Strategy changes require closed/reconciled managed exposure.

The PWA now has separate Chart, Live positions, History and Settings pages. Closed trades persist in a deduplicated history ledger with symbol filtering/pagination; retained legacy records are imported without inventing missing trade details. Per-coin performance analysis is planned separately.

## Trailing, mobile and analysis

Configurable trailing activation and execute distance are frozen per new trade. The selected default bases are initial-margin ROI for activation and best-price percentage for distance; trailing defaults off. THOTH samples mark prices and tightens verified fixed Bybit stops, retaining durable peak/update intent through restart. The last confirmed stop survives server outages but does not keep trailing.

Mobile gestures add one-finger time/price movement and two-finger candle zoom. The Analysis page uses the retained closed-trade ledger for coin and strategy statistics. Biometric login, password reset/change and Google Authenticator remain deferred as requested.

## Priority risk update

Daily protection now uses a fixed starting-equity dollar allowance below a persisted observed peak. Transfers are neutralized and the UI shows remaining allowance. Per-coin consecutive-loss control is the default, with independent persisted cooldowns; unrelated coins can continue entering/reversing. Whole-account mode is optional. Legacy shared pauses are not assigned to every coin, and unavailable historical peaks are not fabricated.

Live activation errors now name ENABLE LIVE ORDERS. Manual daily-stop review preserves baseline/peak and requires flat, reconciled exposure plus equity inside the saved allowance. Countdown displays use the server clock; Activity retains the latest 30 on screen. Profit/loss row colors and detailed reversal block reasons are included.
