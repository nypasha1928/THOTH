# Order Block + S/R · 30m Close · Delayed :15/:45

Separate profile: `ob_sr_30m_delayed`. Earlier two-direction and intrabar strategies keep
their behavior.

The main signal is **Order Block OR Major S/R on a CLOSED 30-minute candle**, using the
same closed-source calculation as the prior 30m profile:

- All four Order Block events are enabled, without extra formation/break filters.
- Both long-term and short-term MAJOR support/resistance are enabled.
- S/R includes breakout/breakdown AND native bounce/rejection.
- NO MINOR signals, SMA, Volume MA, opposing-wick, ATR-distance or strength entry filters.

Save the confirmed direction and delay the entry until 15 minutes after candle close:

| Candle closes | Scheduled entry |
| --- | --- |
| :00 | :15 |
| :30 | :45 |

The time is anchored to candle close, not when a scan finishes detecting the signal.
At the due time, enter DIRECTLY in the saved Long/Short direction using current market
price and normal order/risk checks. No 1m entry, reversal or exit signal is requested.
There is no two-direction minute sequence. Same-direction signals while already holding
a position are skipped. An opposite delayed 30m signal uses the selected Reversal/Close-only
action; reversal confirms flat and reconciles P&L before a replacement order.

The account monitor releases due signals independently of the full market scan. Order
requests must pass final checks during :15–:16 or :45–:46 (end exclusive). A full account
can wait for capacity within that minute; missed windows are discarded, not replayed.
Fills may arrive later. Capturing a signal and submitting its order are separate events.

Stops, take profit, trailing, Max Positions, daily/weekend schedules and loss cooldowns
remain active. Capture and execution both require eligible account/schedule state. A loss
on closing for reversal can trigger a cooldown and block the replacement. No protection
is bypassed to complete a scheduled entry.

Pending delayed signals are cleared on manual pause, restart or settings save. Startup
and data gaps baseline the closed source rather than replaying old candles. Real missing
30m data is backfilled through the existing bounded recovery path. No minute history is
needed. The minute worker reports `not_required` for this profile.

The expandable **Scheduled 30m signals** panel lists pending direction/countdown. Activity
logs show capture and scheduled release separately. Alerts/history identify the 30-minute
source; order metadata retains source close, due time and capture time for review.

This change does not select, deploy or enable live trading automatically.
