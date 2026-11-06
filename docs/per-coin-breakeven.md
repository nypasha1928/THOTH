# Per-coin Breakeven: fixed profit floor

Settings has a separate **Per-coin Breakeven · fixed profit floor** group:

- Enable per-coin breakeven (OFF by default).
- Trigger profit in USDT (default 0.20).
- Close floor in USDT (default 0.10).

A verified unrealized P&L sample at or above the trigger arms that position. After
arming, a sample at or below the close floor requests a reduce-only close of only
that position. The same P&L rule works for Long and Short positions. Profit rising
from 0.20 to 2.00 leaves the floor at 0.10: no peak-following calculation is used.
Trigger must be positive; floor must be nonnegative and strictly below trigger.
Values must be finite. This initial feature uses dollars, not a percentage basis.

The armed state survives restart. Triggered exits are persisted before submission
and retry through the existing close reconciler, including while new entries are
paused or the feature is subsequently disabled. Entry permission is retained;
normal schedules, cooldowns and risk checks still apply to any later entry.

The feature applies to current managed positions when enabled. Saving changed
breakeven settings resets an untriggered breakeven cycle; unrelated settings leave
it intact. It cannot reconstruct profit reached before it was enabled or between
server samples. No additional market requests are introduced.

All existing trailing stops, take profit, protective stops, strategy exits and
account protections remain active. Breakeven runs before per-coin trailing in the
same monitor pass; if both trigger in that pass, the close reason is breakeven.
An existing close obligation is never replaced. It closes before wallet reads,
which can fail independently. This is a server-monitored close, not an exchange
stop order, and requires a running server and successful exchange access.

Positions shows Waiting, Armed with the fixed floor, or Closing. History has an
expandable per-coin breakeven record and **Copy all trades** includes its state.
Activation/trigger and settings-reset events are retained in the per-position
diagnostics; close attempts/errors use the existing durable diagnostic path.
Earlier trades have no invented records.

The floor is based on sampled unrealized P&L. Fees, funding, slippage, sampling gaps
and submission/fill delays mean the realized amount can be lower, even negative.
Enabling this feature does not guarantee a net breakeven result.
