# Volume Delta · 1h · :30 · Closed 1m SMA7 Exit

This is a **new strategy**, `volume_delta_1h_midpoint_sma7_exit`. The existing
**Volume Delta · 1h · :30 Decision** retains its original exits and has no new
minute-SMA rule. Select the new profile explicitly to use this feature. Saving
never enables live entries; existing account activation and schedule rules apply.

## Entries remain hourly

Use exactly the same Volume Delta signal calculation, saved Strong/Weak/All and
SMA7/50 alignment settings, once-per-coin/hour decision, :30–:31 entry minute,
freshness checks, risk controls and exchange preflight as the original :30 profile.
The strategy timeframe is fixed to 1h. No minute candle opens or reverses a trade.
The new profile is independently named in History and Analysis. Switching does
not reset per-hour execution reservations or replay prior decisions.

## Exit only: completed 1m close versus SMA7

Once a new-profile position is filled and observed:

- Long: close when a completed 1m candle's close is **below** its SMA7.
- Short: close when that completed candle's close is **above** its SMA7.
- Exact equality: no SMA7 exit.

The average includes the deciding completed candle and the preceding six
completed minute closes, using strict decimal comparisons. This is a price-level
check, not an SMA7/50 crossover and not a requirement for a new crossing. The first
eligible minute can therefore exit a position that is already on the adverse
side of SMA7. A deciding candle must finish **strictly after the fill was first
observed**. An earlier closed candle cannot immediately close a newly filled
position. If a fill is first observed after a minute boundary, wait for the next
post-fill completed candle rather than use order-creation time as a substitute.

Each eligible check obtains a fresh, bounded eight-candle minute response. Never
reuse a snapshot downloaded while the deciding candle was still forming. Require
seven consecutive completed closes, the latest expected closed minute, valid
positive prices and nonfuture/nonduplicate minute timestamps. The receipt must
be after the deciding candle's close and no more than ten seconds old. During the
existing short publication grace, wait for a missing newly completed candle;
do not fabricate it or use the forming candle. Gaps and stale data do not produce
an invented exit.

The monitor checks on its normal cadence (no more frequently than five seconds
per position). This is sampled server management, not a guaranteed fill at the
minute closing price. Missing connectivity or data can delay it; after recovery
it uses the latest eligible close, not a fictional past execution.

## Close-only and independent protections

A minute-SMA exit persists a close obligation, clears pending replacement intents
for that coin, and submits a reduce-only close. Existing idempotent reconciliation
handles retries and restarts. **It never creates a reverse order or a minute
entry.** Subsequent entries still require a fresh hourly :30 decision and all
original controls. The selected hourly Reversal/Close-only action remains part
of the copied hourly strategy; its replacements still obey the original window.

Protective exchange stops, take profit, per-coin/combined/equity trailing, account
loss limits, cooldowns and the scheduled daily cutoff remain active. The minute
exit runs even when entries are paused and before wallet/transfer monitoring.
An exit-data failure is reported and blocks only that coin's new entries until
its data recovers; other positions, trailing and account risk continue to be
managed. No missing-data fallback fabricates an SMA7 signal.

Existing trailing settings and their enabled/disabled state are retained rather
than replaced or automatically enabled. Review them before saving the new
profile. Settings and the visible filter banner identify the additional
**CLOSED 1m price vs SMA7 · close only** exit; History records its specific exit
reason. The generic SMA7/50 exit toggle stays OFF because this is a different,
required exit rule belonging only to the new profile.

The :25 comparison also supports the new profile, independently paired by its
strategy/settings fingerprint. It still observes entry signals and price moves,
not a replay of this minute exit or hypothetical profit.
