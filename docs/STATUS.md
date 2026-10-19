# THOTH implementation status

The app now exposes only live Bybit trading. Paper/testnet routes, dashboard controls, workers, compose overrides and the testnet acceptance utility have been removed. Shared execution/risk tests still use isolated fake exchanges; they do not place orders.

Live state, account binding, journals, candle history and exit obligations retain their existing paths. No historical databases are deleted. Existing server configuration and the unverified-indicator override remain compatible. Restart recovery manages known positions with new entries paused.

Activity distinguishes order submission from owned-fill/stop verification. Verification is emitted once per trade only after the reported exchange stop matches the expected dollar stop. Symbol-level protection failures remain contained; account-wide risk and monitoring locks still apply.

Actual TradingView S/R comparison remains unverified. Local tests and synthetic chart screenshots do not establish strategy profitability or guarantee exchange execution. This workspace does not hold the VPS credentials or deploy changes to the user's running server.
