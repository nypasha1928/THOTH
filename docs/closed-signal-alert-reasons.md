# Closed 30m alert source and status corrections

The S/R + RSI delayed profile retained a diagnostic even when no source fired.
The generic execution logger then called that `skipped: closed 30m source`, and
the chart fallback showed `source not recorded`. No crossing had actually been
lost in that case: the diagnostic explicitly has no RSI/S/R candidates.

For a captured or blocked real signal, the delayed handler already recorded its
specific source and decision. A second generic log at the same observation time
could overwrite that decision in the alert lifecycle projection, obscuring saved
entry or weekend-block reasons. The fix suppresses this duplicate log after an
explicit capture decision and retains diagnostics on blocked/expired decisions.
Future expiry and delayed-risk-block alerts carry the saved source and diagnostics.
No indicator predicates, entry eligibility, timing, orders or settings are changed.

The read-only historical projection preserves a prior specific decision when its
only subsequent update is this known generic duplicate. An old no-signal row is
labelled `No qualifying S/R or RSI signal` only when its recorded closed-candle
diagnostics explicitly establish no events/conflicts and it has no entry sources.
Unknown historical sources remain unknown. No boundary, crossing or eligibility
is invented, and stored history is not rewritten. Execution-linked phases still
have priority over descriptive statuses as before.

This change does not enable schedule windows or alter saved risk controls.
Historical block reasons describe the state at the time, not current settings.
