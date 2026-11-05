# Volume Delta: two entry windows and closed-minute SMA7/14 exit

New separate profile: `volume_delta_1h_windows_sma7_14_exit`.
Choose **Volume Delta · 1h · :28/:58 · Closed 1m SMA7/14 Exit** in Settings.
The existing :30 profiles keep their original behavior and history labels.

Uses the existing forming 1h Volume Delta calculation, accumulated minute candle-volume proxy,
and saved direction, Strong/Weak/All and optional SMA7/50 entry alignment settings.
No new wick, ATR or SMA200 entry filter. This does not enable the old SMA exit.

Each coin's first qualifying sampled decision in :58–:03 or :28–:33 is consumed for that
window. End times are exclusive. The :58 window does not reset at :00 or midnight.
No signal keeps waiting until the window ends. A source decision remains consumed after
close or restart; a pending pre-hour signal expires when its source hour changes.
Final fresh signal qualification is required before submission. Fills can arrive later.

Existing positions are not closed merely because another window starts. Same-direction
signals do nothing. With Reversal selected, an opposite window signal closes, reconciles,
then may reverse subject to all existing checks and Maximum Positions. A full account
skips that opposite decision and leaves the existing position managed by its exits.
No position beyond Maximum Positions is added, including queued/reconciling entries.

The mandatory minute exit uses two consecutive calculations from 15 completed 1m closes:
Long exits on SMA7 crossing from at/above to below SMA14; Short exits from at/below to above.
Equality holds. A relationship already adverse without a fresh cross does not exit.
The deciding minute must close after observed fill; forming candles are excluded.
This minute rule only closes; it never opens or reverses. Missing/stale data blocks that
coin's new entries and retries monitoring. Stops, configured trailing and risk exits remain.

The fixed :25 versus :30 observation panel remains for the fixed :30 profiles;
it does not label this first-qualifying-window strategy as a fixed :30 observation.
No live account has been activated by adding this profile.
