# UTC daily loss reset

At an observed 00:00 UTC boundary, the first valid equity sample starts a new
risk day. Starting equity, peak, timestamps, allowance, loss used, remaining
allowance, loss percentage, stop threshold, and the daily-stop latch are
replaced together. Previous daily loss is not deducted from the new allowance.
For example, new starting equity 54 USDT with a 15% setting means 8.10 USDT
remaining and zero used, regardless of the previous day's drawdown.

An open losing position is already reflected in the new day's equity. Closing
it later at the same mark does not charge that historical loss again. Only
subsequent equity changes (including fees) and drawdown from the new tracked
peak use the new allowance. Deposits and withdrawals remain neutral.

Same-day restarts, deployments, and repeated observations do not reset the
allowance. Independent coin cooldowns and portfolio-stop latches are retained.
If monitoring misses midnight, the existing flat-account baseline-review rule
still applies; the page explicitly shows a pending baseline rather than
presenting the previous day's allowance as today's.

The dashboard exposes baseline and peak UTC timestamps. Older stored peaks
without a timestamp are labelled as such, not assigned an invented time. A new
high records its observation time; subsequent drops preserve it. Activity logs
the observed daily rollover with the new starting equity and full allowance.

Validation covers rollover after a latched loss, restart persistence, cooldown
preservation, peak timing, pending-baseline display, and an overnight managed
position closed after reset without charging its pre-midnight loss twice.
These tests use synthetic exchange responses; they do not establish a historical
exchange value or directly verify a deployed server's next midnight boundary.
