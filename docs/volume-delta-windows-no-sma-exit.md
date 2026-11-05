# Volume Delta two windows — No SMA Exit

Select **Volume Delta · 1h · :28/:58 · No SMA Exit** in Settings.
This is a separate saved profile (`volume_delta_1h_windows_no_sma_exit`), preserving the SMA7/30 version.

Entry logic matches the two-window profile: first qualifying forming 1h Volume Delta signal
per coin in :58–:03 or :28–:33, with at most one consumed decision per window. :58 across
:00 is one window. Original Strong/Weak/All, direction toggles and optional SMA7/50 entry
alignment remain. Fresh requalification, schedule, cooldown and Maximum Positions checks apply.

There is no price/SMA7, SMA7/30 or generic SMA7/50 exit and no minute exit data download.
The SMA7/50 entry filter, if selected, still controls entries only.
“No SMA Exit” does not disable the configured stop loss, take profit, trailing, account risk
or scheduled cutoff. A selected opposite window signal retains Reversal/Close-only behavior;
full capacity skips it and leaves the existing position managed by the other protections.

No live deployment, entry activation or risk-setting change is performed by this update.

Existing positions retain their owning profile's exit rules. Selecting this new profile
changes future entries; it does not remove SMA exits from positions opened by the SMA7/30 profile.
