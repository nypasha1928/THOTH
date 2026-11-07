# Signal Alerts versus routine candle checks

Signal Alerts shows source events and their execution/eligibility outcomes.
An empty periodic candle check is not an entry signal and must not appear as one.

Definite no-event S/R + RSI checks and empty Volume Delta decision checks are now
saved to a separate bounded `scan_checks` table (5,000 rows). They do not consume
signal-decision retention or generate Activity signal messages. Consequently they
do not trigger the browser's signal-update sound. Actual signals, including
conflicting source events and qualified signals blocked by entry checks, retain
their sources/reasons in the existing lifecycle feed.

The reader also excludes identifiable historical no-event checks from Signal
Alerts, its count, pagination, watched updates and Copy all signal alerts. Filtering
happens before pagination. Legacy raw records are not deleted or rewritten.
Unknown historical source details are not fabricated or silently classified as
empty checks. Recorded direction/source events always prevent no-event exclusion.

Analysis has a collapsed **Candle checks · Analysis only** panel with refresh,
pagination, expandable evidence and **Copy all checks**. It loads only on demand
or while open. Reports include retained legacy no-event checks and new scan checks;
they are not a complete scan history. No new market requests are introduced.

This changes reporting/storage only. Signal predicates, :07/:37 scheduling,
order checks, protections and saved schedule/window settings are unchanged.
