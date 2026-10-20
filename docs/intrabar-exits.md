# SMA 7/50 exits and daily countdown

Settings offers **SMA 7/50 intrabar exits (both strategies)**, enabled by default,
including for existing saved configurations. Pause entries before changing it.
When enabled it applies to existing managed positions as well as new ones.
This is separate from entry logic and the SMA entry filter.

For a long, SMA 7 crossing below SMA 50 closes the position. For a short,
SMA 7 crossing above SMA 50 closes it. The server uses the strategy timeframe,
50 closed candles and the unfinished candle's last traded close. It compares
successive observations inside the same candle, starting from the preceding
closed candle on first observation. A position already on the adverse side
without a new crossing is not automatically closed by this feature.

Each open managed coin is checked approximately every five seconds, subject to
network and account polling latency. This is a sampled server exit, not a native
Bybit conditional SMA order or a guarantee of detecting every tick. Candle gaps,
stale data, and invalid prices cannot generate exits; unavailable observations
block new entries while existing stops and account risk controls continue.
The verified dollar stop remains on Bybit during outages.

An SMA exit creates a durable reduce-only close, with its reason in Activity and
History. It runs during entry pauses and per-coin cooldowns, without creating a
replacement order. A later fresh confirmed entry signal can enter under the
existing risk checks. Entry strategies remain S/R Breakout and SMA 7/200, both
using closed candles. Trailing stops remain independent and off by default.

Daily protection displays a server-clock-adjusted countdown to the next
00:00 UTC, updated every second. Reaching zero does not itself enable entries
or establish a risk baseline; the backend daily rollover and saved manual or
automatic resume setting still control that behavior.

Validation uses simulated exchange responses and browser fixtures. Actual
Bybit SMA exit execution has not been verified by the development environment.
