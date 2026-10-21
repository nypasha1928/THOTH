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
