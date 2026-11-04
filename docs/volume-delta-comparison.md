# Volume Delta :25 versus :30 — observation only

Keep **Volume Delta · 1h · :30 Decision** selected for live trading. Open
**Analysis → Volume Delta · :25 versus :30** to review observations. The panel
is collapsed by default; collection defaults ON independently of whether it is
open. Its checkbox changes only the separate research database. **Copy all
comparisons** exports all retained comparisons, not only the visible page.

The observer never receives an order client and has no execution methods. It
writes only `volume-delta-comparison.sqlite`. It cannot change trading settings,
submit an order, enable entries, or manage positions. Execution still occurs
only through the original :30 strategy and its account checks.

## Samples and pairing

At :25–:26, the market adapter obtains one extra bounded 32-minute candle sample
per selected coin/hour/filter configuration, alongside its existing hourly
download. The research worker uses the same Volume Delta function and saved
SMA7/50, Strong/Weak/All and direction switches. The first :25 snapshot is retained,
including no-signal and invalid-data outcomes; it does not search for a later
passing signal. No new live entry signal is emitted at :25.

At :30–:31, a nonblocking queue copies the **actual first source decision** made
by the live scanner. That is not proof of a submitted or filled order: position
limits, account locks, cooldowns, schedule, preflight and fresh submission checks
are not simulated. No second :30 market scan or new order path is introduced.

Match by coin, hourly candle and strategy-settings fingerprint. Different filter
configurations remain separate unverified records. Report same direction, changed
direction, :25 only, :30 only, neither qualified, or missing/invalid observations.
A :25 observation processed more than 30 seconds late is marked unverified.
Positive price advantage means :25 offered a better snapshot price for the same
direction; the percentage denominator is the :25 price. Opposite directions are
not compared as a price advantage.

Normal hourly downloads can supply the common completed-hour closing price.
Directional percentage moves from each qualifying snapshot to that **same hourly
close** are price measurements, not hypothetical P&L. They do not replay stop
losses, trailing, reversals, basket membership, position sizing, risk controls,
fees, funding, liquidation or slippage. Full matched exit simulations are not
provided by this signal-comparison panel.

## Availability and safety

Collection requires the :30 profile and a connected market worker. Manual entry
pauses and risk locks do not themselves disable market observation. The observer
is sampled, not tick-by-tick: slow downloads, outages, restarts, large watchlists
or a full queue can miss windows. Missing snapshots and closes stay unverified;
there is no historical backfill or invented :25 result. At most 20,000 coin/hour/
configuration records are retained. The report shows dropped snapshots and worker
errors. Disabling collection retains existing records and stops extra :25 reads.

Extra :25 market reads are needed because the original live strategy downloads
its minute inputs only at :30. Research processing runs in a separate bounded
worker; callback failures cannot prevent the live :30 decision. Public market
requests share the existing exchange pacing, so this is additional bounded read
load, not a promise of zero latency impact under exchange congestion.

Tests cover first-snapshot/no-signal retention, direction changes, price-advantage
signs, common-close measurements, invalid/stale data, filter mismatch, persistence,
disabling/overflow, read-only API/export, observer failures and unchanged :30
execution. Browser checks cover the collapsed panel, enabled setting, comparison
rows, export, error recovery and independence from live settings writes.
