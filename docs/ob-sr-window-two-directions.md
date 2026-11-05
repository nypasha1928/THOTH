# Order Block + S/R: hourly window → two minute directions

Select **Order Block + S/R · 1h :28/:58 → 1m · Two Directions**.
This is a separate profile (`ob_sr_windows_two_direction`); earlier strategies keep their logic.

## Hourly permission

Evaluate the forming 1h candle during :58–:03 and :28–:33, without waiting for close.
The end times are exclusive. :58 across :00 or midnight remains one window.
The first qualifying sampled Order Block OR Major S/R signal selects the coin; it does not
place an hourly order or restrict the minute direction. A continuous fresh observation is
required, including a baseline after restart or missing data. Each coin gets at most one
permission per window. Missing or conflicting source directions do not arm the coin.

Both timeframes use all four unfiltered Order Block events and Major S/R breakout AND
native Bounce/Rejection from the uploaded indicator. Minor S/R stays display-only.
Bounce means low touches support, close is above support AND the previous candle's close.
Rejection means high touches resistance, close is below resistance AND the previous close.
Breakout and bounce checks use separate causal source trackers. There is no Volume MA,
SMA200, SMA7/50, opposing-wick, ATR-distance or strength entry filter in this profile.

## Minute sequence

After permission, wait for a NEW forming 1m Order Block OR S/R signal:

1. First direction: enter Long or Short, independently of the hourly direction.
2. Next opposite direction: close the position, confirm flat and reconcile realized P&L,
   then submit the opposite entry if fresh signal, account, schedule and capacity checks pass.
3. Next opposite direction: close ONLY. No third entry; stop minute entries until a new
   hourly window permission.

Same-direction repeats do not advance the sequence. The minute sequence can continue
outside the hourly windows. Unknown/unfilled entry intents cannot consume the next direction.
Minute signals must be fresh, from the current minute, and later than permission/observed fill.
Data gaps and restarts baseline minute sources rather than replaying an old event.

A new hourly permission in the next window REPLACES the previous permission and restarts
the sequence. An open position remains open and counts as direction 1; next opposite reverses,
following opposite exits. No additional position is opened by this restart. Old queued minute
intents are discarded; the new permission gets a fresh minute-source baseline.

Stops, take profit, trailing, daily/weekend cutoffs and account protections remain configured
independently. They may close a position earlier. Such a close does not reset the direction
budget: only the next opposite direction can use the second slot; after it, the next opposite
locks the sequence. Loss cooldowns block new entries and require fresh hourly permission
following cooldown. Minute exits remain managed during an entry pause. Maximum Positions
still limits concurrent positions; reversal first closes/reconciles so it needs no extra slot.

Direction slots are durably reserved before submission. A definite rejected/local-not-submitted
order releases its reservation; an ambiguous outcome retains it pending reconciliation.
A server restart does not reset the two-direction budget or automatically enable live entries.

## Display and verification

The expandable **Two-stage coin permissions** panel shows waiting, direction 1, or direction 2
with its next action. Alerts/history identify hourly and minute sources separately.
Chart references show breakout plus bounce/rejection on 1h and 1m; they do not replay the live
sequence. E/X markers show recorded trades. Market sampling may miss signals, and a market
fill may arrive after a window ends. This implementation does not deploy or activate trading.

Chart S/R markers use **BO▲/BO▼** for breakout, **B▲** for bounce, and **R▼** for rejection.
Hover to see the signal type and direction; forming candles are identified as previews.
When both breakout and bounce qualify on one candle, both marker types are shown.
Use **Chart appearance → S/R → Show S/R breakout, bounce/rejection arrows** to show/hide them.
This display control does not change strategy settings or orders.

## Minute-history recovery and independent workers

An armed coin may still have unavailable minute data. The minute worker now recovers real
missing candles with at most two extra 1,000-candle pages per check; failed backfills retry
after 60 seconds. Partial progress is persisted, without fabricating prices or replaying old
entry signals. The recovery bridge is retained until both OB and S/R checkpoints advance,
then the normal 5,000-minute retention limit resumes. Chart refreshes respect that bridge.
Trades, risk limits and the two-direction permission budget are not erased by recovery.

Hourly permission updates and minute results use separate processing cursors, so a newer
hourly update cannot discard a still-fresh minute result. Minute freshness and the requirement
for a signal after the current permission still apply.

`/api/runner` → `confirmation.symbols` includes per-coin `history_recovery` progress and
`last_detected_signal` (time, direction and sources). Detection does not mean an order was
accepted: account, schedule, cooldown, capacity and exchange checks remain independent.

## Queued reversal across a minute boundary

A direction-2 reversal already queued while direction 1 is open may submit after the
minute changes, provided closing/reconciliation and all entry checks finish within the
existing 30-second signal lifetime. Merely crossing :59 → :00 no longer cancels that
queued replacement. Ordinary first entries still require the current signal minute.
A new permission, expired signal, loss cooldown, schedule or account protection can still
block direction 2. In particular, a loss on direction 1 counts toward the existing per-coin
consecutive-loss limit; a newly triggered cooldown is not bypassed to complete the sequence.
