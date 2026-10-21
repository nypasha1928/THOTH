# Uploaded RSI source audit

The original uploaded Pine is preserved in `reference/ml_rsi_900_uploaded.pine`. The separate `reference/ml_rsi_900_array_corrected.pine` is a diagnostic TradingView reference, with only three changes: both distance arrays and the new-centroid array start empty before values are appended. The fixed-size initial centroid array is intentionally unchanged because it is filled using array.set.

## Why this reference is needed

The uploaded source allocates three empty elements and then appends distances/means. The appended indices therefore do not correspond to its subsequent 0/1/2 cluster selection and threshold reads. This is a source defect, not a reason to invent numerical thresholds. No empty-cluster fallback or history correction has been silently added to the comparison copy. If this minimal repair still yields unavailable thresholds, that result is evidence to inspect before further corrections.

The source filters all six arrow families through volume (optional for static), VWAP, SMA and optional MACD gates. A threshold crossing alone does not imply a displayed source arrow. Showing unfiltered arrows as accepted signals would change the uploaded rules.

THOTH intentionally differs in additional ways: its ML sample window rolls over valid RSI values, it retains empty-cluster centers, and it uses numerical convergence tolerance. The source selects samples relative to last_bar_index and appends through execution. Historical thresholds can differ even when the most recent sample window agrees. These differences remain unresolved parity questions, not proven explanations for every screenshot discrepancy.

## Comparison procedure

Keep RSI entries paused. Add the diagnostic Pine as a separate indicator on the same Bybit ETHUSDT perpetual 1-minute chart with the uploaded defaults. Retain the original indicator for side-by-side inspection. Compare one exact **closed** candle in UTC: close, smoothed RSI, upper/lower ML thresholds, and which filtered arrow family occurred. A forming candle or the latest quote at different refresh instants is not a controlled comparison.

If the original and diagnostic Pine differ, first establish which implementation represents the desired behavior. If the diagnostic Pine and THOTH differ, compare identical OHLCV and RSI history before altering clustering or filters. Do not tune thresholds or disable filters to reproduce arrows by appearance.

This change adds comparison material only. It does not modify exchange access, settings, live signals, order management or risk controls.

## In-app Uploaded Pine mode

Settings → RSI calculation settings → RSI calculation version now offers `uploaded_pine` alongside `corrected`. Saving this selection applies it to both chart calculations and RSI hourly entry observations. The default remains `corrected` for existing installations; installing this update never silently switches a running strategy or enables entries.

Uploaded mode preserves the original new-centroid array outcome: indices 0 and 2 stay `na` after means are appended to a three-element array. The port returns JSON null for those thresholds and no ML events or cluster dots; the chart explicitly reports the source defect rather than suggesting a temporary warm-up. It retains RSI/smoothing and filtered static 30/70 rules. Static signals remain eligible even when the ML family switch is selected, because the uploaded Pine does not gate static signals on ML availability. ML-only selection therefore produces no entries. Minute exits and exchange protections are unchanged.

The original history-dependent ML loop is not run wastefully when its observable threshold result is always unavailable. No corrected clustering, empty-cluster retention or rolling-window threshold is substituted in uploaded mode. This is a Python reproduction of that source outcome, not an embedded Pine interpreter or an independently TradingView-certified port of every input combination. The uploaded source file remains byte-for-byte unchanged. Matching the pictured valid ML thresholds remains unresolved; this mode intentionally does not manufacture them.
