# History refresh recovery

History reads refresh every 15 seconds while the History page is visible. The
status shows the last successful update time in New York. Background reads keep
the existing rows, filter, page and expanded diagnostics when data has not changed.

History requests have a 12-second deadline and explicitly bypass browser caches.
Foreground navigation/refresh replaces the previous read. A periodic refresh can
also replace a read older than 12 seconds instead of treating the loading flag as
proof that useful work is still progressing. Focus, visibility restoration and
network-online events immediately replace an outstanding read and retry.
Request sequence checks prevent a late response from overwriting newer data.
Other API calls retain their existing 45-second deadline and mutation behaviour.

This addresses recovery from stalled reads and suspended/mobile browser sessions.
It does not establish the precise cause of any particular production incident;
server failures, authentication expiry and unavailable connectivity can still
prevent updates. Failed reads show an error and retain the last rendered trades
while automatic retries continue. Authentication expiry still requires sign-in.

`tools/browser_smoke.py` covers a stalled read, replacement after elapsed time,
late obsolete responses, recovery on an online event and a newly settled trade
appearing through the actual automatic interval without reload. It uses synthetic
account fixtures and never activates trading.
