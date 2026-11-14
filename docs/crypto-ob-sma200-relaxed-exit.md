# Crypto OB · 1h Close · SMA200 · 3.03% Lower Exit

A separate selectable strategy, `crypto_ob_strength_sma200_closeonly`.
The original Crypto OB Strength strategy remains available.

## Entries and exits

- Use completed **1h formed Order Blocks** with the original raw per-coin entry minimum.
- Long entry requires the same hourly close **above** SMA200; Short requires **below**. Equality, invalid values or fewer than 200 completed consecutive hourly candles block entries.
- An opposite completed hourly formation with width at least the **exit minimum** closes the position only. No immediate reverse order comes from that signal. A later fresh qualifying formation is needed for another entry.
- SMA200 entry permission does not restrict exits. Merely crossing SMA200 does not exit. No breaks, S/R, RSI, volume gate, SMA7/50 or minute confirmation.
- Two independent native Order Block states use the entry and exit thresholds. Both are checkpointed and consume closed candles only.
- Raw strength is the block high minus low in quote-price units, not a volume or ATR score. Native formation rules still apply.

## Automatic settings for all 15 coins

Settings → Strategy → **Crypto OB · 1h Close · SMA200 · 3.03% Lower Exit** opens a table with **Entry minimum** and **Opposite exit minimum** for every candidate. No threshold entry is required. Exit minima display three significant digits (for example, LDO shows approximately 0.00485). Hover over a value to see its exact minimum. Display rounding never changes the trading threshold.

The fixed exit multiplier is **32/33**, approximately **3.0303% lower**. This preserves the requested GRT example exactly: **0.00033 → 0.00032**. It is not a rounded 3% multiplier. Calculations use the full derived values; this documentation table limits display precision.

| Coin | Entry minimum | Opposite exit minimum |
| --- | ---: | ---: |
| LDOUSDT | 0.005 | 0.00484848484848 |
| UNIUSDT | 0.1 | 0.0969696969697 |
| WIFUSDT | 0.0019 | 0.00184242424242 |
| LINKUSDT | 0.107 | 0.103757575758 |
| JASMYUSDT | 5.2e-05 | 5.04242424242e-05 |
| GRTUSDT | 0.00033 | 0.00032 |
| JUPUSDT | 0.0045 | 0.00436363636364 |
| GALAUSDT | 2.1e-05 | 2.03636363636e-05 |
| ETHUSDT | 7.45 | 7.22424242424 |
| SEIUSDT | 0.00081 | 0.000785454545455 |
| BNBUSDT | 2.58 | 2.50181818182 |
| AVAXUSDT | 0.07 | 0.0678787878788 |
| OPUSDT | 0.00235 | 0.00227878787879 |
| POLUSDT | 0.00087 | 0.000843636363636 |
| ENAUSDT | 0.004 | 0.00387878787879 |

The saved candidate list may be a subset; all 15 thresholds remain visible. Maximum Positions, sizing and leverage remain under the operator's control.

## Protection preset

Explicit first save of this strategy applies the agreed per-coin P&L trailing **ON**, trigger **$5**, fixed drop **$0.10**, combined trailing **OFF**. Trailing controls remain editable after the preset. New entries use the strategy's **$0.50 hard stop**. Breakeven, price trailing, take profit and SMA exits are off for this profile. Account risk limits, cooldowns and schedules remain active. Threshold qualification does not bypass risk checks.

## Switch without closing current positions

Pause entries, choose this profile, and save. A transition **from the original Crypto OB Strength closed-hour profile** keeps protected managed positions open. All open symbols must remain selected. Pending entries and closes must settle, and account management must be connected. Other strategy changes retain the existing flat-account restriction.

Saving does not send close or replacement orders, rewrite existing hard stops, change entry prices/quantities, or enable entries. The selected policy manages the next fresh opposite hourly formation. Existing trailing peaks stay intact if their trailing settings are unchanged; changing those settings retains the existing reset behavior. Standard startup/settings baseline handling prevents replaying old signals.

History retains each position's original entry strategy and entry evidence. Adopted positions also record the new exit policy; the actual exit evidence records the opposite block and threshold. The History evidence expansion and copy export include these records. Selecting a new exit policy does not relabel historical entries.

Chart Order Block boxes use **entry thresholds**, with below-threshold boxes and break arrows hidden. They are references, not proof of execution or SMA permission. Actual E/X markers identify recorded trades. The intrabar observer still collects only for the original profile; its API reports unsupported collection when this profile is selected.

This implements an alternative rule set, not a proven improvement or a guarantee to preserve profit. Hourly confirmation, sampling, fees and execution slippage can affect outcomes.
