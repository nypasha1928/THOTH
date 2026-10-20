# Chart SMA 7, 50 and 200

The chart always draws three display-only moving averages on the selected chart
timeframe, once enough candles are available:

- SMA 7: white by default.
- SMA 50: light blue by default.
- SMA 200: blue by default.

Appearance offers an independent color picker for each line. Colors are stored
only in that browser's existing appearance settings. Previously hidden SMA 7
preferences no longer suppress it. The existing saved SMA line color is retained
for SMA 200; Restore screenshot styles restores the default blue.

The lines calculate from the loaded chart candles, including the current
forming candle when a fresh quote is available. They warm up before the visible
viewport when history is available. Auto price scale includes their visible
values; manual price zoom/pan can still move them outside the viewport.

These fixed display periods do not depend on entry strategy, SMA filter enable
checkboxes, filter length, or the intrabar exit checkbox. Changing chart colors
or timeframe does not change saved strategy settings, trading signals, or risk.
Volume and its MA 20 remain independent display overlays.
