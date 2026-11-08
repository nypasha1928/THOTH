# Price × SMA50 · 30m Bar Close

Separate profile `price_sma50_30m_close`. Earlier SMA7/50 and S/R strategies keep
their rules. This profile uses price versus one SMA50, not an SMA7/50 crossover.

SMA50 is the arithmetic mean of the latest 50 completed 30m closes, including the
deciding close. Only a confirmed change of side generates an entry:

- From below to above -> Long.
- From above to below -> Short.
- Additional closes on the same side -> no signal.
- Equality -> no entry and no resetting the last nonzero side. A touch followed
  by a return to that same side does not refire. Departing an initially equal,
  fully warmed average can count as the first crossing.

The first available average baselines the side, rather than opening a trend
position without a prior comparison. Gaps restart warm-up. Checkpoint/restart
replay matches batch calculation, and normal supervisor startup baselines prevent
old signals replaying into orders. A position closed by trailing/stop/manual action
cannot re-enter merely because later candles remain above/below SMA50.

Entries are considered after :00/:30 candle close, subject to scan/order latency
and the existing confirmed-signal queue checks. No :07/:37 delay and no 1m entry
or SMA7/50 exit are used. Opposite CLOSED price/SMA50 crosses use the selected
Reversal or Close-only behavior. Reversals close/reconcile before replacement;
normal account, Max Positions and cooldown checks may block a replacement.

Protective stops, trailing, per-coin breakeven, take profit, daily protection and
saved entry schedules remain active. No S/R, RSI, Order Block, Volume MA, SMA7/50
alignment or other indicator filter participates. General SMA-length settings are
not changed; this profile's period is fixed internally at 50. Entry timeframe is
fixed at 30m, while the displayed chart timeframe remains independent.

The selector and active banner name the exact strategy. P▲ / P▼ are price/SMA50
chart reference arrows, subject to chart arrow visibility settings. Displayed SMA50
uses 50 bars even when the general SMA-length setting belongs to another profile.
Other chart overlays are display-only for this strategy. Forming chart lines may
move, but entry decisions use completed 30m candles only.

Alerts save the deciding close, SMA50, previous close/SMA50, prior nonzero side and
candle/close timestamps. The execution metadata carries this evidence into the
owned position and retained closed-trade history. History has expandable
**Price/SMA50 cross evidence**, and Copy all trades includes it. Older trades do
not gain invented values.

The existing trend/pullback observer can keep collecting from this profile's
cached 30m candles. It remains a separate observation model, never a live filter.
No profile is selected, deployed or activated automatically by this change.
