# S/R + RSI 4h/D → Volume Delta 1m

This is a separate selectable strategy. Existing strategies and the uploaded Pine files are unchanged.

1. Monitor both 4-hour and Daily S/R and fixed RSI crossings while candles form. Either source on either timeframe gives that coin permission. Major/minor families remain selectable; S/R/RSI entry filters are off. Fixed RSI thresholds remain editable.
2. Permission alone never places an order. A **new subsequent 1-minute Volume Delta condition** chooses Long OR Short independently of the higher signal's direction. The minute SMA 7/50 alignment filter is mandatory. Higher signals are a permission reference, not a demonstrated measure of market volume or protection against sideways markets.
3. Permission remains active across winning/losing closes and restarts. Later higher signals do not change or reset it. Wins reset that coin's consecutive loss count to zero; flat results leave it unchanged.
4. At the configured consecutive-loss limit (default three), lock only that coin and discard its queued entries/reversals. Wait the configured cooldown (default 60 minutes), then require a **fresh higher signal observed after cooldown**. Expiry alone does not unlock. A new subsequent minute condition is still required after rearming.

No SMA exit is used. Existing exchange dollar stop, optional take profit, per-coin/combined trailing, opposite-signal close/reversal selection and account risk controls continue to manage positions. Three losses is an entry lock based on settled results, not a replacement for a stop loss on an open position. Pausing entries does not suppress an opposite Volume Delta exit for an existing armed position.

## Settings and monitoring

Pause entries and close/reconcile positions before changing strategy. Choose **S/R + RSI 4h/D → Volume Delta 1m**. Entry timeframe shows 1m and is locked because permission always monitors both 4h and Daily. Chart timeframe is independent. SMA exit is off, the Volume Delta SMA filter is on, and consecutive-loss scope is per coin. The loss limit and cooldown remain editable.

On Live positions, expand **Two-stage coin permissions** for Waiting / Armed / Locked / Waiting for fresh signal, source timeframe, consecutive losses and cooldown countdown. Activity and Signal alerts record arming/locking/unlocking. History sources preserve both the higher trigger and the minute Volume Delta source.

Changing entry rules or removing/readding a symbol invalidates armed permission; existing lock/cooldown state is retained. Restart requires normal manual entry activation. Observations made while paused are not replayed as fresh permissions. Existing armed permission persists, but minute signals still require a fresh worker observation baseline after restart.

## Implementation and limits

`staged_entry.py` is the pure durable per-coin state machine; supervisor state stores gates, queues and settlement counts. `FeedAdapter` fetches both higher sources while waiting and always obtains minute data last. Once armed, it fetches only minute data. Higher observations use existing causal S/R levels and fixed RSI calculations with separate 4h/D histories and restart guards. Healthy sources can arm a coin when another source is unavailable; missing minute data cannot create an entry. There is no extra inverse strategy or 60-second entry delay.

Volume Delta mathematics remain the uploaded strategy's candle-volume proxy, not aggressor trade delta. A minute condition already consumed in the arming observation is not replayed to enter: a new eligible condition is required. Sampling, large watchlists, API rate limits, entry queue expiry and account safeguards can delay or prevent orders. Historical chart arrows do not reconstruct permission state or prove an order occurred.

Validation uses local simulated exchanges, actual indicator calculations, restart/freshness tests and desktop/mobile browser fixtures. It does not establish profitability or TradingView feed/timing parity.

### Minute data freshness fix

Higher histories are now prefetched with a maximum of four outstanding packets. The runner completes each coin's higher calculations before requesting its 60 most recent minute candles and refreshing the observation clock. Minute candles are not stored in the prefetched packets, which previously allowed them to expire while queued on large watchlists. Current-minute validation remains strict. Initial higher-history bootstrap and the full watchlist sweep may still be slow; inspect `scan_seconds` in the worker report rather than assuming every coin is sampled every five seconds.

After initial history bootstrap, higher-timeframe downloads refresh a recent three-candle overlap plus elapsed periods; saved warmup is retained. The staged source check reads only the latest closed indicator result and avoids rewriting unchanged checkpoints. Initial bootstrap still requests up to 1,000 candles, and large-watchlist sweep duration must be measured on the server.

At a minute rollover, if Bybit returns exactly the previous minute within the first five seconds, the worker makes at most two additional reads with short bounded waits. It still requires the actual current candle. Exhaustion, older/future data and missing data remain errors; diagnostics include expected and received bar times.

### Armed-coin priority

Waiting and cooldown coins do not request minute candles. When a higher signal arms a coin, its first later minute read establishes a fresh baseline; a subsequent qualifying observation may enter. While higher histories for other coins are being fetched, the runner revisits armed coins in rotating groups of up to four at five-second scheduling opportunities. These checks can dispatch an entry before the full higher watchlist scan finishes. Group sizes are bounded so permission scans still progress. The strict 300-second freshness guards are unchanged.

This is a scheduling target, not an execution guarantee: eleven armed coins require roughly three groups (15 seconds) plus request/processing delays. Larger armed sets, API backoff and CPU load increase the interval. Once detected, a qualifying signal goes directly to normal entry checks/queue; there is no deliberate delay. The existing 30-second detected-signal expiry remains a maximum signal age, not a wait. Worker reports include `priority_minute_checks` per completed scan. Full-sweep time alone no longer describes armed-coin check frequency; confirm actual signal/order timestamps too.

### Independent workers (current; replaces the shared scan heartbeat above)

Higher permission scanning and minute confirmation now run in separate owned workers. The higher worker only processes 4h/Daily histories for waiting coins. The minute worker continuously revisits armed coins in bounded rotating groups; it does not wait for the higher sweep to finish. Both use the existing supervisor lock, journal and global API pacing. Starting/stopping the app owns both workers; the minute worker is dormant for other strategies.

Minute observation continuity follows each coin's actual check attempts. Rotation through other coins does not itself invalidate continuity; a failed check, restart or excessive gap still establishes a fresh baseline. The 300-second maximum remains. Higher packets that crossed a 4h/Daily boundary get one current-bar refresh. A higher observation gap exceeding its freshness budget is now an explicit source error, not merely a successful data response. Past missed crossings are not replayed after update.

The worker report now separates higher `scan_seconds` from nested `confirmation` metrics: batch duration, estimated complete round, armed count, total checks, pending first checks, maximum check age and stale coins. `priority_minute_checks` now counts since worker start and is labelled accordingly. Overall degraded status can originate in either worker. Actual performance must still be measured on the server; concurrent workers share API, CPU and account-state limits, so neither immediate fills nor a guaranteed sweep duration is promised.
