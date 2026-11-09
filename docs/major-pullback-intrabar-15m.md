# Major Pullback · 30m Intrabar + 15m Close · No SMA

Separate profile: `major_pullback_30m_intrabar`. Existing closed Trend + Major
Pullback and Trend + Major Breakout profiles retain their SMA200 and close rules.
Adding this profile does not select it or enable LIVE orders.

## Confirmation and entry

1. Read **30m Major S/R levels** already confirmed before the relevant 15m candle's
   parent 30m candle. Both long-term and short-term Major families participate.
   Minor signals and breakout events never qualify.
2. Require the latest **completed 15m candle** to show native bounce/rejection
   against that SAME 30m Major level. Support bounce: low <= support, close >
   support and close > previous 15m close. Resistance rejection mirrors this.
   A forming 15m preview cannot grant entry permission.
3. After confirmation, keep checking the **current forming 30m setup**. It must
   show the same native bounce/rejection at the same unchanged level. Its current
   price is compared with the previous completed 30m close, not with any SMA.
   The 30m setup can become valid later while that 15m confirmation is current.
   Do not require the 30m candle to close.
4. Refresh the current 30m snapshot's close/high/low using actual newer forming
   15m data from the same 30m period. No missing candles or prices are invented.
   Entry must still satisfy the 30m native conditions and remain within **0.25
   Wilder ATR14** of its reference, including a **2 bps adverse price buffer**.
   ATR uses completed 30m candles plus the current forming 30m snapshot.
5. Recheck the confirmed source, current price and distance during entry checks
   and immediately before submission. A lost bounce/rejection, overextended
   quote, stale snapshot or obsolete 15m confirmation cannot submit an order.

There is **no SMA filter, no 1h confirmation, no 200-hour warm-up and no custom
entry windows**. Market monitoring continues throughout the day. Normal candle
closes determine which 15m confirmation is current; no particular minute is
excluded. Existing account schedules still apply only as saved by the operator.

Each fresh 15m confirmation replaces the prior one. Startup, restart, stale data,
missed scans and candle repair establish a baseline without replaying old
confirmations. Actual market snapshots/queued orders use the existing 30-second
freshness limit. This is sampled monitoring, not tick-by-tick execution.

A Major event is consumed once per source/level in each current 30m setup candle;
repeated 15m confirmations cannot repeatedly reopen the same event. ATR-rejected
events are consumed too. A distinct fresh source or a later setup candle can
qualify under normal rules. There is no added hourly position cap.

## Sizing and exits

Keep saved position notional, leverage, stop loss, take profit, breakeven,
per-coin/combined trailing, maximum positions, account limits and loss cooldowns.
A qualifying opposite confirmed setup follows the saved Reversal / Close-only
action. No standalone SMA or minute exit is added. No research structural-risk
sizing is used. Risk checks and account schedules are not bypassed.

History → Reason → **15m-confirmed Major Pullback entry evidence**, and Copy all
trades, retain the 15m candle, the 30m snapshot, source, reference, ATR distance and
submission quote check. Routine checks without a matching event stay in Analysis;
actual source events failing distance/setup checks retain their rejection evidence.

## Chart

The chart selector now includes **15m**, with S/R reference markers. On this
profile, the 15m chart explicitly uses the same **30m Major levels** used by LIVE
confirmation, rather than separate 15m-derived pivot levels. The 30m chart shows
Major setup references. Forming 15m previews may change; LIVE still requires the
15m candle to close. Raw arrows alone do not prove that both setup and distance
checks passed, or that an order was placed.

The existing Trend + Major Pullback research panel remains its original closed
1h/30m simulation and does not collect this distinct intrabar profile. Technical
checks do not establish profitability.
