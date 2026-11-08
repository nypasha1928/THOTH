# Trend + Major Pullback · 1h / 30m Close

Separate selectable strategy: `trend_major_pullback_30m_close`. It does not change
the existing S/R 4h/Daily strategy or enable LIVE orders by itself.

## Entry rules

1. Build completed UTC hourly candles from consecutive actual closed 30m pairs.
   Require the latest 200 consecutive complete hours. Last hourly close above
   SMA200 permits Long; below permits Short. Equality or insufficient history
   cannot enter. The forming hour is excluded.
2. A newly CLOSED 30m candle must produce the native Major support bounce (Long)
   or resistance rejection (Short) in that hourly direction. Both long-term and
   short-term Majors participate; no Minors. Levels must already exist before
   the deciding candle. There are no breakout, Order Block, RSI, Volume MA or
   SMA7/50 entry triggers.
3. Enter at the first timely post-close decision, within 120 seconds of close,
   using a fresh current-price snapshot (no more than 30 seconds old). No :07/:37
   delay. After a missed scan, restart or recovery, establish a new baseline and
   require a future closed candle; never replay old setups. Each candle has one
   decision, so rejected checks do not keep retrying as its price changes.
4. Candidate entry must be on the signal side of the prior level and no more
   than **0.25 Wilder ATR14** beyond it. ATR uses completed 30m candles, including
   the signal candle. Include a fixed **2 basis point adverse price buffer**.
   Agreeing levels choose the nearest eligible reference and record it as the
   exact source. Recheck that reference against the ticker during entry checks
   and immediately before submission; an expired or overextended entry skips.
   The buffer is not a guarantee of fill price.

These entry predicates are shared with the research observer. LIVE constants are
fixed in this profile; editing Research assumptions does not change LIVE rules.
No winning-trade or profitability claim is made by technical tests.

## LIVE sizing and exits

As requested, use the account's existing position notional, leverage, dollar stop,
take profit, per-coin and combined trailing, breakeven and other risk settings.
Do not use the research observer's $0.40 structural-risk sizing, $1,000 modeled
notional cap, structural ATR stop or R-based exits. There is no extra SMA or
1-minute exit. A qualifying opposite closed setup follows the saved Reversal /
Close-only action. Stops, account schedule, loss cooldowns, portfolio capacity
and exchange checks remain in force.

History retains trend evidence, exact Major source, reference level, ATR distance
and submission quote check. Expand Reason → Trend + Major Pullback entry evidence,
or Copy all trades. Actual account entry/quantity/P&L remain the real execution
fields; the buffered candidate price in the evidence is a calculation reference.
Routine no-event checks go to Analysis candle checks, not Signal alerts. Actual
S/R events rejected by trend/distance retain their source and rejection evidence.

## Charts and observation

Use the **30m chart** for Major bounce/rejection references, and the **1h chart**
with SMA200 for the trend. Chart averages follow the displayed timeframe; a
30m SMA200 is not this strategy's hourly trend. Reference arrows alone do not
prove all entry checks passed. Forming chart previews do not cause LIVE entries.

The existing expandable **Analysis → Trend + Major Pullback · Observation only**
panel receives the needed 30m snapshots alongside this LIVE profile. The 1h
candles are derived from those complete pairs, with no extra hourly download.
The 30m feed bootstraps enough candles for the hourly warm-up and uses bounded
historical gap recovery. Research can continue while LIVE entries are paused,
provided market management remains connected. It is still unavailable alongside
unsupported strategies that do not supply the required 30m feed.

The observer remains a separate simulation with its saved research assumptions,
structural-risk sizing and simulated exits. Its P&L is not the expected LIVE P&L.
Exports include current collection support/connectivity and selected strategy.
Earlier observations are retained under their original model/settings identity;
shared-code changes create a new identity rather than rewriting old outcomes.
