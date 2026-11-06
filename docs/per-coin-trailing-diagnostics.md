# Per-coin trailing diagnostics

New records appear in History, under an expandable **Per-coin trailing diagnostics**
section in the Reason cell. **Copy all trades** includes the complete retained record
as a final column. Existing trades without evidence show `Not recorded` in exports.
No old peaks or activation events are reconstructed from candles.

For each managed position after this update, the supervisor preserves:

- Actual enabled state, activation and fixed-dollar drop at evaluation time.
- Sample count, first/last evaluation times, largest interval between evaluations,
  last unrealized P&L and mark, and highest observed P&L (including before activation).
- Latest trailing state: activation, active peak, threshold and trigger time.
- Activation/trigger events, settings resets with previous state, monitoring errors,
  and close attempts with reason, identity, submission outcome or sanitized error.
- Settlement time and final exit reason in the retained trade history.

The record lives with the position in SQLite and is copied to closed history before
ownership is removed, surviving ordinary restarts and Activity message rollover.
Settings resets preserve the diagnostic record while resetting the normal trailing
state as before. The last 40 diagnostic events per position are retained, with an
explicit dropped-events count; sample summaries are cumulative. This bounds storage
and avoids recording an unbounded stream of every price update.

These are server-observed unrealized P&L samples, not continuous tick history.
A peak between samples is still unknowable. The largest evaluation gap includes API,
other position processing, outages and restarts; it does not identify the cause by
itself. Submission acknowledgement is not a fill; final history records settlement.
A missing report is not evidence that activation did not happen. The generic
`Exchange stop/close` reason remains generic; diagnostics do not invent an exchange
closure classification.

This update changes diagnostics and display only. Activation/drop calculations,
exit ordering, stops, entry logic and risk settings are unchanged. No production
credentials or production SQLite records are available in the development workspace;
this update cannot recover already discarded diagnostics from the user's server.
