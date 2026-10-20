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

## Optional S/R entry filters

S/R Breakout starts with no entry filters. Both checkboxes default off.
The first upgrade also clears the older automatic SMA filter selection; later
explicitly saved choices persist across restarts. There are two independent checkboxes:

- **SMA price filter (default SMA 200)** uses `sma_enabled` and configurable `sma_length` (200 by default). Long entries need
  close above that SMA; shorts need close below it.
- **SMA 7/50 entry filter** starts off. Long entries need SMA 7 above SMA 50;
  shorts need SMA 7 below SMA 50. This tests alignment, not a new crossover.

Selecting both requires both conditions. With neither selected, the S/R
breakout logic runs without either SMA entry filter. SMA 7/50 requires 50
contiguous closed candles and equality blocks both directions. These options
apply only to S/R Breakout; SMA 7/200 retains its confirmed-crossover entries.
The independently selectable intrabar SMA 7/50 exit still applies to both
entry strategies when enabled. It does not become an entry filter.
