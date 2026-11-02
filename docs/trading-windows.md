# Trading windows in Settings — New York time

The schedule is an account entry gate, separate from every strategy's signal
logic. The agreed preset is:

- Daily: block new entries from 03:45 inclusive until 10:45 exclusive, and
  close all THOTH-managed positions at the 03:45 cutoff.
- Weekend: block from Saturday 00:00 inclusive until Monday 10:45 exclusive.
- Scope: Main existing and Main Market Leaders 50. Scope follows the selected
  list, which can include gold-linked assets; it is not per-symbol classification.

With Early :55–:00, the first eligible Monday/daily decision window is 10:55–11:00.
Reopening does not guarantee an order: a fresh qualifying signal, prior LIVE
activation, instrument/account eligibility and all risk controls are still needed.

## Configure

Pause new entries to edit Settings through the existing form. Expand **Trading
windows · New York**, click **Use agreed schedule in draft**, review the times,
scope, both enabled switches and the daily position-exit checkbox, then **Save strategy & trading symbols**. Saving
this form saves the other strategy/risk edits too, so review them before saving.
The preset button edits only the draft. Existing installations default OFF;
merging/deploying does not silently impose or enable a schedule on real funds.

Controls allow different daily and weekly start/end times and list scope. The
weekend can end Monday 00:00 if changed later. TradFi or all-list scope is an
explicit selection, not a conclusion about those markets' trading sessions.
Identical start/end points are rejected when a window is enabled. Overnight and
week-wrapping windows are supported. Overlapping windows combine: entries must
be outside both. A combination with no opening is shown explicitly rather than
inventing a reopening timestamp.

## Execution and automatic reopening

The schedule never switches account `entries` permission off and never changes
risk counters or strategy source parameters. Thus, if entries were enabled and
other controls allow them, entry eligibility returns automatically at the end of
the window. A manual pause, restart recovery, safety lock, cooldown or daily/account
stop is not cleared by the schedule. Schedule edits use the existing pause-before-
settings rule and establish fresh execution cursors; old signals are not replayed.

Indicators and comparison sampling continue during blocked periods. Existing
positions keep their stops, trailing, targets, account management and indicator
exit handling until confirmed flat. With the agreed preset, daily cutoff exit is
enabled: the first management cycle at or after 03:45 submits reduce-only closes
for every owned position and cancels unsettled entry orders. This runs even when
entries are manually paused, before wallet/transfer monitoring. Late fills are
reconciled and closed too. The exit obligation is persisted before exchange writes,
survives restart and remains blocking after 10:45 until flatness and settlement
are confirmed. A missed cutoff closes carried positions upon recovery; fresh
positions opened after reopening are not part of that earlier obligation.
Enabling the daily exit during its blocked window closes existing positions on
the next management cycle. Exchange/network delays can delay fills; existing
idempotent close reconciliation and retry limits remain in effect. THOTH never
automatically closes unowned positions. The weekend boundary itself remains an
entry block; its daily 03:45 exits still apply. A normal opposite signal can close an existing position; the new
reverse entry is prohibited while blocked. Reversals/pending entries blocked by
the window are discarded rather than stored until reopening. Delayed inverse
entries cannot arm while the schedule is blocked. A higher-timeframe permission
may still be tracked under its strategy's existing rules; its actual entry still
requires a fresh confirmation and the schedule gate.

Checks run at queueing, entry preflight, and immediately before Bybit's create
request, after idempotency reconciliation. If reconciliation/disk work crosses a
block boundary, the unsent journal intent is marked locally cancelled with
`not_submitted: true`; no order is sent and entry-cap reservations are restored.
Accepted/uncertain submissions retain normal reconciliation and capacity rules.
Reduce-only closes bypass this new-entry guard. A request sent before a boundary
can be filled or observed after it; accepted orders are not retroactively cancelled
by an entry-only window. When daily cutoff exit is enabled, its cancellation
and late-fill close management applies.

## Status and timezone

The dashboard shows whether windows are OFF, apply to the selected list, OPEN, or
blocking entries, and the next opening in America/New_York. Readiness distinguishes
LIVE entries armed during a scheduled pause from account activation being absent.
The market worker remains active. A scheduled block is not recorded as a server
restart or account-monitoring failure. Signal alerts explain scheduled skips and
local no-submission cancellations.

Timezone conversion uses IANA America/New_York and installed tzdata, not a fixed
UTC offset. Tests cover fall-back repeated hours and spring-forward skipped hours,
exact boundaries, weekend overlap, scope, persistence, manual-pause/restart rules,
opposite-signal closures, trailing during a block, daily forced exits, late-fill
closure, durable restart recovery, missed cutoffs, wallet failure and
non-replay of queued reversals. The bundled SQLite connection-lifetime fix is
also included because it was still absent from main when this branch started.
