# Crypto OB · 1h Close · SMA200 · 6-Hour Entry Filter

Strategy ID: `crypto_ob_strength_sma200_hours`.
This is a separate selectable strategy. Deploying the code does not select it,
change saved trading settings, enable entries, or place orders.

## Trading rules

- Use only completed one-hour Order Block **formations** at the symbol's fixed
  raw quote-price width threshold. No block breaks, S/R, RSI or volume-MA filter.
- Enter Long only when that completed hourly close is above its SMA200; enter
  Short only below. Equality, missing SMA200 or an unsupported symbol blocks entry.
- An opposite completed hourly formation at `entry minimum × 32 / 33` closes the
  position only. SMA200 permission and entry-hour exclusions never block this exit.
  There is no same-signal reversal, and crossing SMA200 alone is not an exit.
- New entries are prohibited during these end-exclusive **America/New_York** hours,
  on every day, including weekends:

  | Block start | Reopen |
  |---|---|
  | 05:00 | 06:00 |
  | 08:00 | 09:00 |
  | 10:00 | 11:00 |
  | 12:00 | 13:00 |
  | 21:00 | 22:00 |
  | 23:00 | 00:00 next day |

The timezone follows daylight saving time, not a fixed UTC offset. This strategy's
exclusions remain active when Settings → Trading windows is OFF. General account
schedules and risk controls may impose additional restrictions. No position is
closed merely because an excluded entry hour begins. Existing stops, trailing,
opposite-signal exits and account risk management remain active continuously.

Hour eligibility is checked before queueing a new signal, on direct entry, and
again at journal submission. Blocked or expired signals are not replayed at the
next permitted hour. Startup/recovery still establishes a fresh execution baseline.

## Explicit settings preset

The first explicit save of this strategy applies the tested preset:

| Control | Preset |
|---|---|
| Position value (notional, not margin) | $50 per position |
| Maximum simultaneous positions | 10 |
| Hard stop | $0.50 gross per position |
| Per-coin P&L trailing | ON: $2.50 activation / $0.10 fixed drop |
| Breakeven | OFF |
| Combined P&L trailing | OFF |
| Opposite action | Close only |

The button **Apply agreed stop / trailing preset** stages this preset without
saving. Subsequent saves preserve edited position size, maximum positions and
P&L trailing controls; account leverage and risk allowances are never changed by
the preset. The fixed $0.50 stop, closed hourly rules, close-only action and six
excluded entry hours belong to the strategy. Optional unrelated exits are disabled.

Changing global per-coin trailing settings also applies to existing managed
positions and resets untriggered peaks. Existing positions retain their original
entry size and exchange hard stop. Fees, gaps and slippage can make realized
loss exceed the gross stop or realized profit differ from a monitored threshold.

## Selecting the new version

1. Deploy the update, then refresh THOTH. The previous strategy remains selected.
2. Pause new entries and let pending entries/closes reconcile.
3. Select **Crypto OB · 1h Close · SMA200 · 6-Hour Entry Filter**.
4. The separate **Crypto · OB Strength / Hours Candidates 50** list loads into the
   draft. Review or reduce it; loading a list does not create positions.
5. Review the strength table and the preset, then save settings.
6. Review readiness and manually enable entries when appropriate.

A switch from the existing SMA200/lower-exit profile can retain protected positions
because its fifteen thresholds and exit rules are unchanged. No pending entry,
unknown order, close in progress, or unprotected position can use that exception.
Position history and original hard stops remain intact. An unrelated strategy
change still requires a flat, reconciled account. The two older crypto strategies
and their saved fifteen-coin list retain their existing behavior.

## Frozen research catalogue

`thoth/crypto_ob_hours.py` contains exactly the fifty thresholds used in the
six-hour-exclusion replay, not the later intrabar candidate thresholds. The fifteen
existing values are preserved. The remaining values came from prior research
calibration; this is not a live market-cap ranking or a guarantee every symbol is
profitable. The curated list includes the existing gold-linked symbols PAXG/XAUT.
Two minima, INJ and WLD, are zero: those symbols have **no strength restriction**,
though they still need a formation and SMA200 permission. This reproduces the
research definition rather than inventing positive thresholds.

The settings table displays each entry minimum and its separate `32/33` exit
minimum, rounded for display with exact-value tooltips. Chart boxes use the entry
minimum, only completed 1h candles, and never imply an actual fill. E/X markers
show recorded trades. Each strategy's checkpoint and verification identity includes
its own policy, so stale fifteen-coin or differently configured contexts cannot
be submitted as this strategy's signals.

## Research provenance and limits

The matched replay used fifty curated symbols and actual minute candles, with
201-hour SMA200 warmup. Scored period: October 19, 2026 2 PM EDT through November 10,
2026 4 AM EST, exclusive (21.625 elapsed days). Maximum ten positions, $50 notional,
$0.50 stop, $2.50/$0.10 per-coin trailing, breakeven and combined OFF. It estimated
5.5 bps fees and 2 bps adverse slippage each side; funding, cash/margin/liquidation
constraints and live account-wide risk pauses were not modeled.

| Replay | Net closed profit | Ending open valuation | Estimated drawdown |
|---|---:|---:|---:|
| All hours | +$22.64 | +$1.17 | $33.73 |
| Six excluded entry hours | +$46.51 | +$0.79 | $20.85 |

The hours were selected **after inspecting this same history**. These results are
retrospective, not independent validation or promised returns. New forward data
is needed. Live account controls and execution can differ materially.

### Entry minima (raw quote-price width)

| Symbol | Minimum |
|---|---:|
| 1000BONKUSDT | 2.6e-05 |
| 1000PEPEUSDT | 0.0001 |
| AAVEUSDT | 2.51 |
| ADAUSDT | 0.0017 |
| ARBUSDT | 0.00271 |
| ARUSDT | 0.057 |
| ATOMUSDT | 0.03 |
| AVAXUSDT | 0.07 |
| BNBUSDT | 2.58 |
| BTCUSDT | 254.4 |
| CAKEUSDT | 0.031 |
| COMPUSDT | 0.34 |
| DOGEUSDT | 0.00109 |
| DOTUSDT | 0.0181 |
| ENAUSDT | 0.004 |
| ETHUSDT | 7.45 |
| FILUSDT | 0.0182 |
| GALAUSDT | 2.1e-05 |
| GRTUSDT | 0.00033 |
| HBARUSDT | 0.00043 |
| ICPUSDT | 0.053 |
| INJUSDT | 0.0 |
| JASMYUSDT | 5.2e-05 |
| JUPUSDT | 0.0045 |
| KASUSDT | 0.00067 |
| LDOUSDT | 0.005 |
| LINKUSDT | 0.107 |
| LTCUSDT | 0.42 |
| MNTUSDT | 0.0038 |
| NEARUSDT | 0.062 |
| OPUSDT | 0.00235 |
| PAXGUSDT | 12.69 |
| POLUSDT | 0.00087 |
| RENDERUSDT | 0.015 |
| SANDUSDT | 0.00054 |
| SEIUSDT | 0.00081 |
| SHIB1000USDT | 3.2e-05 |
| SOLUSDT | 1.34 |
| SUIUSDT | 0.0193 |
| TAOUSDT | 5.5 |
| TRXUSDT | 0.0009 |
| UNIUSDT | 0.1 |
| VETUSDT | 4.4e-05 |
| WIFUSDT | 0.0019 |
| WLDUSDT | 0.0 |
| XAUTUSDT | 8.91 |
| XLMUSDT | 0.0021 |
| XMRUSDT | 6.65 |
| XRPUSDT | 0.0158 |
| ZECUSDT | 26.27 |

## Validation

- Full synthetic regression suite: **823 tests passed**.
- Desktop/mobile browser smoke: passed, including a distinct strategy selection,
  50-row threshold catalogue, draft-only list loading, preset save, disabled
  breakeven, preserved older profile behavior and no order activation requests.
- Boundary checks cover all 24 hours in both EST and EDT, the repeated fall-back
  hour, queue/direct/submission guards, and an opposite close during an excluded
  entry hour. Protected-position switching retains the original exchange stop.
- The frozen 50-coin map was checked against the actual six-hour study input.
