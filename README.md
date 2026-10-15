# Price Alert Tool

A reliable, dependency-free Python CLI tool that evaluates configurable price alert rules (level-crossing and moving-average crossover) over historical CSV price records.

Built for the **Wissen Technology Campus Assessment**.

---

## 1. Setup & Execution

Requires **Python 3.8+** (standard library only; no external dependencies).

### Run the CLI
```bash
python alerts.py prices.csv rules.json
```
Output:
```text
2026-09-03  r1  INFY  level below 1500
2026-09-24  r2  TCS   ma_cross 5/20 above
```

### Run Automated Unit Tests
```bash
python -m unittest test_alerts.py -v
```

---

## 2. Design & Crossing Semantics

1. **State-Based Level Crossing (`type: "level"`)**:
   - Configured via PDF field `level` (numeric threshold, e.g., `{"level": 1500}`).
   - An alert fires only when moving into a side from the strictly opposite side.
   - The engine tracks the last known side: `+1` (above) or `-1` (below).
   - **Boundary Equality Policy**: When price lands exactly on the threshold ($P = \text{level}$), it is treated as a neutral boundary state (`0`). The engine retains the previous side. This prevents false alerts on support/resistance bounces (e.g., $160 \to 150 \to 160$ correctly produces **no** alert).
   - Day 0 establishes the baseline state; it cannot fire an alert.

2. **Moving-Average Crossovers (`type: "ma_cross"`)**:
   - Configured via PDF fields `short` and `long` (e.g., `{"short": 5, "long": 20}`).
   - Simple Moving Averages ($\text{SMA}$) require full windows to be valid.
   - For short window $S$ and long window $L$ ($1 \le S < L$), both SMAs become valid at index $L - 1$.
   - A crossover is detected when the difference $(\text{SMA}_S - \text{SMA}_L)$ strictly changes sign relative to the previous observation.

3. **Input Validation & Data Integrity**:
   - **Duplicate Records**: Explicitly rejected. Any duplicate `(date, instrument)` raises a descriptive error with line number to `sys.stderr` and exits with code `1`.
   - **Chronological Sorting**: Input rows can be in any order; records are grouped by instrument and sorted by date before evaluation.
   - **Deterministic Output**: Alerts are sorted chronologically by `date` ascending, then alphabetically by `rule_id` ascending. When no alerts trigger, stdout is empty.

---

## 3. AI Usage & Genuine Discovered Mistake

- **AI Prompt**: Supplied the complete Wissen assessment specification requiring incremental delivery and crossing semantics analysis.
- **Genuine Discovered Mistake**: The assistant initially drafted a naive two-point comparison:
  ```python
  # Initial flawed logic
  if direction == "above" and prev_close <= threshold and curr_close > threshold:
      fire_alert()
  ```
  During boundary analysis with test cases such as `[160.0, 150.0, 160.0]` for threshold `150.0`:
  On Day 3, `prev_close` was `150.0` ($\le 150$) and `curr_close` was `160.0` ($> 150$). The naive logic falsely declared that the price crossed *above* 150 from below, even though it started above and merely bounced off support.
- **Correction Made**: Refactored to a discrete state tracker that preserves the prior non-boundary side. An "above" alert now strictly requires the preceding state to have been `below` (`last_side == -1`), correctly ignoring bounces and repeated levels.
