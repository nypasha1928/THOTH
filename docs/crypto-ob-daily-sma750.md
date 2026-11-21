# Crypto OB · 1h Close · Daily SMA7/50 · 6-Hour Entry Block

Separate strategy ID: `crypto_ob_strength_daily_sma750_hours`.
Adding/deploying this code does not select the strategy, change saved settings,
activate LIVE entries, or place orders.

## Entry and exit

- Entry requires a **completed 1h formed Order Block** at the same fixed per-coin
  raw-width minimum as the six-hour SMA200 profile. No OB breaks, S/R, RSI, volume
  filter, or hourly SMA200 permission.
- **Last completed Daily SMA7 > SMA50:** permit Long entries; **SMA7 < SMA50:**
  permit Short entries. This is the current relationship, not a new crossover
  requirement. An hourly formation must still qualify independently.
- Daily equality, nonfinite values, missing candles, fewer than 50 consecutive
  completed Daily candles, or stale Daily evidence blocks new entries only.
  The forming Daily candle is excluded. Daily candles close at **00:00 UTC**;
  their boundary is not New York midnight. Confirmation can therefore lag a turn.
- Opposite **completed 1h OB at entry minimum × 32/33** closes only. The exact
  reduction remains 3.030303%; it does not reverse on that exit signal. Daily
  direction, missing Daily history and entry-hour restrictions never prevent this
  required exit. Example GRT: 0.00033 entry / 0.00032 opposite exit minimum.
- Six entry blocks remain: **05–06, 08–09, 10–11, 12–13, 21–22, 23–00
  America/New_York**, every day and DST-aware, independently of the general
  trading-window switch. Stops, trailing, exits and account management continue.
- Entry is checked on signal queueing, direct entry and final order submission.
  A completed Daily boundary change invalidates older confirmation. Startup
  establishes a fresh execution baseline; blocked/expired signals are not replayed.

## Candidates and settings

Reuse **Crypto · OB Strength / Hours Candidates 48** (or a saved subset). All
48 positive entry minima and 50 historical exit minima match the existing profile.
INJ/WLD zero entry minima remain excluded; legacy positions can still exit.
The settings table lists each entry and opposite exit threshold; exit display is
rounded only, with full precision retained for comparisons.

First explicit save has its own preset flag and applies:

| Control | Preset |
|---|---|
| Position notional | $50 per position |
| Maximum positions | 10 |
| Gross hard stop | $0.50 |
| Per-coin P&L trailing | ON, $2.50 activation / $0.10 fixed drop |
| Combined P&L trailing / breakeven | OFF |

Saved leverage and account limits are preserved. Subsequent saves preserve editable
position count, notional and per-coin/combined trailing choices. This profile fixes
the hourly timeframe, $0.50 stop, formed-only OB sources, close-only opposite action
and disables unrelated exits/filters and breakeven, like its source hours profile.
Fees/slippage can increase losses; server-sampled trailing cannot guarantee a fill
at its threshold. These dollar amounts are not margin or leverage percentages.

## Switching with existing positions

Pause entries before changing settings. A guarded transition from the six-hour
SMA200 profile retains existing protected positions, ownership/history identities,
exchange order links and original hard stops. It changes their future opposite
exit policy to this strategy. Pending entry/close orders must settle, management
must be recovered, no flatten/scheduled exit can be pending, and owned symbols
must remain selected. Unrelated strategy changes still require a flat account.
No migration or activation occurs just because this code is deployed.

## Daily data and evidence

`FeedAdapter.daily_confirmation_inputs` uses existing higher-timeframe download,
validation and recovery routines. Runner stores the real Daily candles separately
in its candle cache and caches valid confirmation for the completed UTC day.
Failed fetches retry on later scans. A Daily failure is reported as degraded entry
confirmation while still delivering valid hourly observations to position exits.
The recorded entry context includes the 50 completed closes, SMA7/SMA50, Daily
source bar/close time and direction. Validation recomputes the means and rejects
stale or inconsistent evidence again at submission. Hourly checkpoints remain
separate from Daily evidence, with a distinct strategy/source fingerprint.

## Research basis and limits

The earlier shared-period retrospective replay (Oct 19–Nov 10, 2026, approximately
21.625 days, 48 entry candidates, up to 50 positions) reported closed net **$114.68**
for Daily confirmation versus **$88.36** for hourly SMA200; modelled drawdown was
**$28.62** versus **$31.09**. Same $50 notional, $0.50 stop, $2.50/$0.10 trailing,
32/33 exit and blocked hours; estimated fees/slippage included. Open valuations
were separate. The settings preset caps positions at 10, so these 50-position
results do not describe the preset's expected return. Repeatedly inspected data
and omitted funding/capital/liquidation/account constraints limit the comparison.
These historical results do not establish a market bottom or future profitability.

## Verification

Synthetic tests cover Daily closed-only calculation, persistent direction, missing,
gapped, stale, equal and tampered data, blocked hours, relaxed independent exits,
zero-minimum legacy exits, preset preservation, hourly checkpoint parity, final
submission UTC rollover, protected-position transition, runner retry/cache and
Daily failure handling. API and browser checks cover the selectable profile,
48-candidate threshold table, locked/optional controls and explicit save behavior.

Completed validation: **835 automated tests passed**, followed by 54 focused
crypto-OB checks after diagnostic-message cleanup. Full Chromium desktop/mobile
smoke checks passed, including the new Daily strategy selection, explicit save,
48-row strength catalogue and preset controls. No activation requests were made.
No real exchange or production deployment was used for these checks.
