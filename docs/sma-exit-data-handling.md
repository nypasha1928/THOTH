# SMA exit candle-boundary handling and coin isolation

Previously, the exit monitor required the latest REST candle to equal the
server's current candle start on every request. A response containing the
just-closed candle immediately after a boundary raised a validation error,
set a global safety lock, and delayed the next attempt by 30 seconds. Even a
successful retry left new entries globally paused until manual activation.

The corrected behavior:

- For up to ten seconds after a boundary, a valid contiguous 51-candle response
  ending exactly one candle behind is treated as publication in progress.
  No new candle or price is invented. The last real SMA difference is retained,
  and normal polling continues without locking entries or applying the 30-second
  failure backoff.
- A crossing between the last sample of one candle and the first sample of the
  next is retained. Receiving the new bar does not discard the previous observed
  difference and silently miss that cross.
- Missing history, older data, candle gaps, invalid prices and publication delays
  beyond that grace block only the affected coin's NEW entries. They do not
  close positions or pause unrelated coins. Existing verified Bybit stops,
  dollar stops, and independently available opposite-strategy exits stay managed.
  A missing SMA observation cannot generate an invented SMA exit.
- Verified fresh observations clear that coin's data block. If it has become
  flat, re-entry checks fresh SMA data before any new order is prepared. A fresh
  signal is still required; consumed signals are not replayed. Other account or
  coin risk controls still apply. Manual entry pauses are never auto-resumed.
- Actual account-wide loss limits, unknown account/order state, and failed
  account monitoring retain their global behavior. This change does not bypass
  those controls.

The dashboard lists affected coins separately instead of presenting a local
SMA data fault as a global safety lock. Repeated identical errors do not flood
Activity. All new entry orders still pass the existing ownership/risk checks.
The grace applies to 1m, 5m, 1h, 4h and Daily boundaries.

Deployment itself still pauses new entries. Existing global locks from the old
version are not silently cleared: check account status and explicitly enable
entries once after deployment. Tests reproduce boundary responses, extended
failures, protected positions, independent entries/reversals, verified re-entry,
and preservation of manual pauses. They do not establish a real exchange SLA.
