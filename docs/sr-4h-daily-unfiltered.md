# S/R · 4h + Daily · Intrabar · No Filters

Separate strategy ID: `sr_4h_daily_unfiltered`. Existing strategies are unchanged.
Select this profile in Settings and save to use it. It fixes trading to independent
4-hour and Daily sources; the chart timeframe is display only. Saving does not
activate live orders.

## Signals

THOTH observes the current forming candle on each timeframe. No candle-close
wait, hourly entry window, lower-timeframe permission or indicator entry filter.
Both long-term and short-term **Major** levels participate. Minor levels never do.
Levels come from the latest completed source candle's confirmed S/R state.

- Resistance breakout: previous sampled price <= resistance and current price
  > resistance → Long.
- Support breakdown: previous sampled price >= support and current price
  < support → Short.
- Support bounce: forming candle low <= support, current price > support and
  current price > the previous completed candle's close → Long.
- Resistance rejection: forming candle high >= resistance, current price
  < resistance and current price < the previous completed candle's close → Short.

Bounce/rejection reuses the uploaded indicator's native predicates, including its
previous-bar close comparison. That comparison defines the signal; it is not an
added filter. Candle extremes may include touches earlier in the forming candle.
Observation is sampled, not tick-by-tick, and can miss brief events.

Either timeframe alone can trigger. Agreeing sources combine into one decision;
fresh opposing events in the same observation skip the decision. Signals retain
source timeframe, event type and level family. Levels, candle snapshots and prior
observations are saved with the entry and available under History → Reason →
4h/Daily S/R entry evidence, and in Copy all trades.

## Freshness and repeat handling

Startup, restart, missing scans and stale observations baseline without replaying
old events. Newly discovered or changed levels also need a baseline. Each native
event fires once per level per source candle; holding above/below a level does not
repeat a breakout. Different fresh events can still trigger during that candle.
There is no inherited one-trade-per-four-hour cap from the older Volume MA profile.

Source snapshots older than 30 seconds cannot trigger orders. A source frame with
unavailable candles/levels is reported as an error; the other frame can still
independently trigger from valid data. Queued signals use the existing short-lived
order expiry and normal entry checks.

## Exits and protections

No SMA, RSI, Volume Delta or Order Block exit is added. A valid opposite S/R signal
uses the saved **Reversal / Close position only** action. Reversal closes and
reconciles before attempting a replacement, subject to account limits.

Saved stop loss, take profit, trailing, breakeven, maximum positions, cooldowns,
schedule and account protections remain active and unchanged. “No Filters” means
no indicator entry filters; it does not bypass risk or exchange checks. This
change does not enable schedules or orders.

Chart BO/B/R arrows on 4h and Daily show breakout and bounce/rejection references.
Forming previews can change. Candle-based historical references cannot reconstruct
every live sampled event and are not proof that an order was placed.

## Historical gap recovery

Recent overlap downloads alone cannot repair an older internal candle gap.
The higher-timeframe download path now uses the existing bounded Bybit backfill
for 4h and Daily history as well as 30m. It fetches only missing real candles,
up to two 1,000-candle pages per source frame per scan. It preserves the cache,
account records and risk settings. Partial progress persists through the normal
history writer; a missing/unavailable page waits 60 seconds before retrying.
Restoring a missing candle before an existing indicator checkpoint invalidates
that checkpoint and its analysis so levels are rebuilt from the complete history.
Each frame keeps its own retry state so a healthy Daily scan cannot clear a
4h retry delay.

The runner report includes per-frame recovery progress. An incomplete source
remains blocked by full continuity checks; another healthy source can still
qualify independently. Recovered sources establish a fresh observation baseline,
so repairs cannot replay historical entries. Shifted or mixed-resolution 4h/Daily
cache timestamps are repaired using the existing hourly quarantine mechanism.
Before archiving anything, THOTH downloads and validates a clean 1,000-bar
replacement window. Failed, empty or wrong-interval downloads leave the original
cache intact for retry, including across restarts. Original wrong-interval rows
and affected aligned neighbors are retained in `candle_quarantine`; aligned
neighbors may also have been overwritten by the wrong-resolution source.
Affected source checkpoints are invalidated and the normal bounded backfill
repairs any remaining older holes. The runner report includes quarantine and
replacement-download counts for the repair scan. No account/order/risk storage
is changed, and no synthetic OHLC or inferred prices are used.
