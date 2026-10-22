# THOTH project handover

Last updated: 2026-10-22 (America/New_York).

## Start here in a new chat

Read this file, inspect git status and origin/main, then read the linked implementation notes. This document records decisions, not permission to enable trading or disclose credentials. The user wants sustained implementation, reviewable pull requests, and simple deployment guidance. Explain defects before fixing them. Do not claim a branch-comparison URL is an already-created PR.

## Deployment and account

- Repository: nypasha1928/THOTH. PWA: https://app.thothtrade.com. DigitalOcean Frankfurt Ubuntu VPS, repo /opt/thoth; user deploys manually over SSH.
- SSH: root@165.227.169.71. Never request or print private keys, passwords or Bybit secrets. Secrets are files under /etc/thoth, not the repository.
- Deployment uses compose.yaml + compose.live.yaml + compose.live-unverified.yaml with THOTH_PUBLIC_HOST, THOTH_PASSWORD_PATH, THOTH_BYBIT_LIVE_KEY_PATH and THOTH_BYBIT_LIVE_SECRET_PATH exports. Preserve volumes, databases, position ownership, journals and risk state.
- User pauses entries before restart. Existing exchange stops remain, server exits briefly stop. Restart requires explicit entry activation. User authorized live trading without mandatory TradingView/testnet gates; do not reintroduce those gates.
- Bybit isolated, one-way, dedicated USDT account. Typical $50 notional / 20x = about $2.50 initial margin; dollar stop configurable. Up to 11 positions discussed for comparison.
- The user will not add capital until reviewing performance together. Do not promise profit or suggest simulated results prove an actual alternative fill.

## Existing trading behavior

- S/R Breakout, SMA 7/200, S/R 1h -> Exit 1m and RSI 1h -> Exit 1m are selectable. SMA entry crossover confirmed on close; hourly intrabar modes sampled, no hourly close wait.
- Long SMA exit: SMA7 below SMA50. Short: SMA7 above SMA50. Intrabar, normally sampled about 5 seconds; mandatory minute exits in hourly modes.
- Reversal means close current position, confirm settled/flat, then open opposite subject to risk. Close-only remains selectable.
- S/R entry filters SMA200 and SMA7/50 separate, optional; no filter unless selected. Major/minor levels selectable.
- Daily stop: starting equity times saved percentage is a fixed dollar allowance below transfer-adjusted daily peak. Reset at 00:00 UTC, not New York midnight. Closes managed winners and losers. Per-coin consecutive-loss cooldowns; account controls remain separate.
- Coin SMA-data faults isolate that coin; account read rate-limit handling retries verified recovery. Never bypass daily, ownership or order-ambiguity checks.
- Per-position dollar stops and optional dollar TP exist. Per-position trailing is a separate older option.

## Combined fixed P&L trailing (merged by user)

See thoth/portfolio_trailing.py, thoth/testnet.py, tests/test_portfolio_trailing.py and README.

- Disabled by default. Editable USDT activation and fixed USDT drop. Examples: activate at $1; peak $1.50 -> close all at $1.40, peak $1 -> close at $0.90 with $0.10 drop.
- Combined current unrealized P&L includes all managed winners and losers. It is not 200% per trade. Closing/adding trades changes the metric; realized profit is not carried into it.
- Close ALL, but do NOT disable entry permission because of this trailing trigger. New orders wait only until flatten/cancel/settlement completes, then new qualifying signals may trade. Other risk controls/manual pause/restart still apply. Do not change this to a manual trailing-stop reset.
- Fixed peak persists across restart. Trigger creates durable flatten obligation before exchange writes. Late fills also flattened. Changing combined parameters resets only an untriggered cycle; other settings must preserve peak.
- History displays the persisted `Open P&L trailing stop` reason as **Combined Trailing Stop** (added with dynamic observation work; old stored reasons remain unchanged). Simulated dynamic exits never enter actual History.
- P&L card shows waiting/active, peak, threshold. Server-monitored, not an exchange-native portfolio stop. Fees, slippage, delays and outages affect realized result.
- Last completed validation for fixed feature: 250 unit/integration tests and desktop/mobile browser smoke passed. Branch codex/portfolio-pnl-trailing was merged; deployment instructions sent, actual VPS state not independently accessible.

## RSI status: deferred by user

User requested original Pine behavior after corrected port differed from TradingView. Original reference/ml_rsi_900_uploaded.pine is preserved byte-for-byte (SHA256 cf415bc7edbd732e04bf2c11148a2a04d06c4ff5dc418625134fd3a43e3078df).

Settings offers Uploaded Pine array behavior versus Corrected ML. Uploaded arrays allocate 3 empty slots then append means, leaving threshold indices 0/2 unavailable; its mode preserves that outcome, so only eligible filtered static 30/70 signals can appear. It is not a verified reproduction of screenshot ML thresholds. Corrected remains migration default until explicitly selected. See docs/rsi-source-audit.md, docs/ml-rsi-strategy-draft.md and docs/rsi-visual-comparison.md. Do not silently repair original or claim numerical TradingView parity. RSI work is currently paused/deferred.

## UI/security already present

Separate Chart/Live positions/History/Analysis/Settings/Security pages. History 50/page, winners green/losses red, NY opening date+time and closing time. Chart has searchable symbols independent of traded watchlist, pan/pinch/zoom/ruler, price/time scales, live-price yellow line/countdown, volume MA20, SMA7/50/200 colors, S/R styles and separate compact indicator dropdowns. RSI pane/price markers share timestamp hover guide. Chart visibility never changes trading filters.

Password changes, recovery codes, Google Authenticator TOTP and passkeys exist; user tested Android passkey and password+TOTP on Windows. Never recover plaintext passwords or reveal saved secrets.

## Active task: dynamic combined trailing observation

User authorized implementation on 2026-10-22. Branch codex/dynamic-trailing-observation, based origin/main 688d02d when begun.

- Add dynamic observation ONLY alongside current fixed combined trailing. Fixed remains sole real trailing executor, settings/peak/positions untouched by observation. No dynamic live execution selector in this phase.
- Observe whatever THOTH-managed coins, quantities, sides and position count are currently open; **11 was only an example, never hardcode it**. A changing basket starts a fresh comparison and marks the earlier one incomplete; frozen post-close copies must exclude new trades. Estimate dollar trailing distance from recent aggregate P&L fluctuations, with editable min/max bounds and multiplier. Ratchet dynamic threshold upward only.
- Separate comparison screen: fixed actual close time/confirmed realized outcome versus dynamic simulated trigger/results. If dynamic waits beyond real closure, follow a frozen virtual copy using public market prices. No new real trades.
- Clearly label simulated continuation, assumed fees, missing funding/data, incomplete observations, basket changes, and unavailable realized results. Never say the operator "should have waited" or imply simulated fills were executable.
- Observation must be bounded and asynchronous. No extra authenticated exchange requests, no observation I/O/network blocking live risk management, no writes to live execution/risk/position state. Preserve fixed peak on update/restart.
- Step 1 complete: handover created.
- Step 2 implemented: separate read-only trailing_observation.py worker/database, nonblocking live snapshot hook, dynamic ratchet/volatility model, public batch mark-price virtual continuation, confirmed actual-history lookup, observation-only API and separate Trailing comparison page. No dynamic executor exists.
- Step 3: 260 Python tests passed (including changing coin baskets with 2, 11 and 5 positions) after calculation/data-quality refinements. Browser checks passed for the new separate comparison page, 11-position display, observation settings save without live-setting writes, bookmark reload and existing desktop/mobile flows. A fetch-wrapper signature mismatch was fixed; pre-existing quote-redraw hover fixture timing was made atomic. Final desktop/mobile browser run also passed the Combined Trailing Stop History label. Indicator regeneration is unchanged; shell syntax and source deployment packaging passed. Ready for review on codex/dynamic-trailing-observation. Not yet merged or deployed; provide the pull/new link, then normal server update guidance after the user merges. Verify fixed saved settings/peak and observer health on the actual server; local checks do not establish VPS deployment or real profitability.
- Implementation limits: aggregate-P&L volatility window 60 (5-second normalized), eight-return warm-up uses maximum distance; public virtual marks sampled 15 seconds, continuation expires at 24 hours; 20 unfinished baskets and 100 recent completed/expired records retained. Only sampled gross quote difference is comparable; assumed 5.5 bps fees editable, funding/slippage/liquidations/other exits not simulated. Actual net realized data is separate. Observation starts on first observed fixed activation, not retrospectively. Snapshot gaps/basket changes/late starts suppress advantage claims. Dynamic execution remains unavailable.

## Local verification and next-chat workflow

- /tmp/thoth-security-clean/bin/python -m unittest discover -s tests
- /tmp/thoth-security-venv/bin/python tools/browser_smoke.py (Playwright, /usr/bin/chromium). Browser uses synthetic fixtures only and overwrites tracked PNGs; restore generated screenshots unless intentionally delivering new artifacts.
- node --check on changed JS, git diff --check. Read applicable AGENTS if introduced.
- After checks, commit/push branch and provide GitHub pull/new URL if no tool can create actual PR. User merges, then manually deploys.
- Keep this handover current after each completed step. Record what is actually verified; do not infer deployment from merge.

## Latest UI step: prominent combined trailing status

Branch codex/prominent-trailing-status, based main 7051f35. User requested a clearer colored status beneath Open LIVE P&L. Added separate green Active, blue Waiting, amber Closing and neutral Off panels, with large Peak and Close-all threshold figures. Text labels supplement colors; no flashing. This is display-only: no changes to trailing math, fixed peaks, settings, order execution or observation. Validation: JS syntax/diff checks and full desktop/mobile browser smoke passed, including active-panel green border and large peak/threshold readouts. Ready for PR review on this branch; not yet merged/deployed. This UI step did not change backend logic; previous backend validation remains 260 tests.

The user has supplied a five-position cycle: fixed gross trigger $0.99, actual realized about $0.5980, dynamic sampled gross $0.96. Fees confirmed $0.13727342 opening + $0.13679398 closing. Do not claim a guaranteed minimum or 200% actual margin return. Their chosen $0.50 sizing maps to $50 notional /20x=$2.50 margin; five such positions about $12.50 margin, $0.60 realized about4.8%. Their target mapping is chosen amount x2, not total portfolio margin x2. Preserve distinction between actual net reported P&L and simulated gross/assumed-fee estimates.
