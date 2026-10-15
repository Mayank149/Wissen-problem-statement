#!/usr/bin/env python3
"""
Price Alert Tool
Evaluates level-crossing and moving-average crossover alerts from CSV price data
against configurable JSON rules.
"""

import csv
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, NamedTuple, Set, Tuple


class AlertValidationError(Exception):
    """Raised when input files contain invalid format, schema, or values."""
    pass


class PriceRecord(NamedTuple):
    date: str
    instrument: str
    close: float


class Alert(NamedTuple):
    date: str
    rule_id: str
    instrument: str
    description: str


def parse_date(date_str: str, file_context: str, line_no: int) -> str:
    """Validate and normalize YYYY-MM-DD date."""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        normalized = dt.strftime("%Y-%m-%d")
        if normalized != date_str.strip():
            raise ValueError
        return normalized
    except ValueError:
        raise AlertValidationError(
            f"{file_context}:line {line_no}: Invalid date format '{date_str}'. Expected 'YYYY-MM-DD'."
        )


def load_prices(csv_path: str) -> Dict[str, List[PriceRecord]]:
    """
    Read and validate price history from a CSV file.
    Groups records by instrument and sorts them chronologically by date.
    Rejects duplicate (date, instrument) entries with a clear error.
    Handles flexible column ordering (date, instrument, close).
    """
    if not os.path.exists(csv_path):
        raise AlertValidationError(f"Price file not found: '{csv_path}'")

    seen_keys: Set[Tuple[str, str]] = set()
    records_by_instrument: Dict[str, List[PriceRecord]] = {}

    try:
        with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                raise AlertValidationError(f"{csv_path}: CSV file is empty.")

            normalized_header = [col.strip().lower() for col in header]
            required_cols = {"date", "instrument", "close"}
            if not required_cols.issubset(set(normalized_header)):
                missing = sorted(list(required_cols - set(normalized_header)))
                raise AlertValidationError(
                    f"{csv_path}: Missing required CSV column(s): {', '.join(missing)}."
                )

            date_idx = normalized_header.index("date")
            inst_idx = normalized_header.index("instrument")
            close_idx = normalized_header.index("close")
            expected_col_count = len(normalized_header)

            for line_no, row in enumerate(reader, start=2):
                if not row or all(c.strip() == "" for c in row):
                    continue  # Skip blank lines

                if len(row) != expected_col_count:
                    raise AlertValidationError(
                        f"{csv_path}:line {line_no}: Expected {expected_col_count} columns but found {len(row)}."
                    )

                date_raw = row[date_idx]
                instrument_raw = row[inst_idx]
                close_raw = row[close_idx]

                date_val = parse_date(date_raw, csv_path, line_no)

                instrument = instrument_raw.strip()
                if not instrument:
                    raise AlertValidationError(
                        f"{csv_path}:line {line_no}: Instrument name cannot be empty."
                    )

                try:
                    close = float(close_raw.strip())
                    if close <= 0.0 or not (close == close and abs(close) != float("inf")):
                        raise ValueError
                except ValueError:
                    raise AlertValidationError(
                        f"{csv_path}:line {line_no}: Invalid close price '{close_raw}'. Must be a positive finite number."
                    )

                record_key = (date_val, instrument)
                if record_key in seen_keys:
                    raise AlertValidationError(
                        f"{csv_path}:line {line_no}: Duplicate record for instrument '{instrument}' on date '{date_val}'."
                    )
                seen_keys.add(record_key)

                rec = PriceRecord(date=date_val, instrument=instrument, close=close)
                records_by_instrument.setdefault(instrument, []).append(rec)

    except OSError as err:
        raise AlertValidationError(f"Could not read price file '{csv_path}': {err}")

    # Sort each instrument's price records chronologically
    for instrument in records_by_instrument:
        records_by_instrument[instrument].sort(key=lambda r: r.date)

    return records_by_instrument


def load_rules(rules_path: str) -> List[dict]:
    """
    Read and validate alert rules from a JSON file.
    Validates types, required fields, and constraints.
    """
    if not os.path.exists(rules_path):
        raise AlertValidationError(f"Rules file not found: '{rules_path}'")

    try:
        with open(rules_path, mode="r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as err:
        raise AlertValidationError(f"{rules_path}: Invalid JSON syntax - {err}")
    except OSError as err:
        raise AlertValidationError(f"Could not read rules file '{rules_path}': {err}")

    if not isinstance(data, list):
        raise AlertValidationError(f"{rules_path}: Root JSON element must be an array of rules.")

    seen_rule_ids: Set[str] = set()
    validated_rules: List[dict] = []

    for idx, rule in enumerate(data):
        rule_prefix = f"{rules_path}:rule[{idx}]"
        if not isinstance(rule, dict):
            raise AlertValidationError(f"{rule_prefix}: Each rule must be a JSON object.")

        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise AlertValidationError(f"{rule_prefix}: 'id' must be a non-empty string.")
        rule_id = rule_id.strip()

        if rule_id in seen_rule_ids:
            raise AlertValidationError(f"{rule_prefix}: Duplicate rule ID '{rule_id}'. Rule IDs must be unique.")
        seen_rule_ids.add(rule_id)

        instrument = rule.get("instrument")
        if not isinstance(instrument, str) or not instrument.strip():
            raise AlertValidationError(f"{rule_prefix} ('{rule_id}'): 'instrument' must be a non-empty string.")
        instrument = instrument.strip()

        rule_type = rule.get("type")
        if rule_type not in ("level", "ma_cross"):
            raise AlertValidationError(
                f"{rule_prefix} ('{rule_id}'): 'type' must be 'level' or 'ma_cross', got '{rule_type}'."
            )

        direction = rule.get("direction")
        if direction not in ("above", "below"):
            raise AlertValidationError(
                f"{rule_prefix} ('{rule_id}'): 'direction' must be 'above' or 'below', got '{direction}'."
            )

        validated_rule = {
            "id": rule_id,
            "instrument": instrument,
            "type": rule_type,
            "direction": direction,
        }

        if rule_type == "level":
            # Primary PDF field is 'level'; fallback to 'threshold'
            level_val = rule.get("level") if "level" in rule else rule.get("threshold")
            if level_val is None:
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): Missing required field 'level' for level rule."
                )
            if isinstance(level_val, bool) or not isinstance(level_val, (int, float)):
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): 'level' must be a numeric value."
                )
            if float(level_val) <= 0.0:
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): 'level' must be greater than zero."
                )
            validated_rule["level"] = float(level_val)
            validated_rule["threshold"] = float(level_val)

        elif rule_type == "ma_cross":
            # Primary PDF fields are 'short' and 'long'; fallback to 'short_window' and 'long_window'
            short_w = rule.get("short") if "short" in rule else rule.get("short_window")
            long_w = rule.get("long") if "long" in rule else rule.get("long_window")

            if short_w is None:
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): Missing required field 'short' for ma_cross rule."
                )
            if isinstance(short_w, bool) or not isinstance(short_w, int) or short_w < 1:
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): 'short' must be an integer >= 1."
                )

            if long_w is None:
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): Missing required field 'long' for ma_cross rule."
                )
            if isinstance(long_w, bool) or not isinstance(long_w, int) or long_w <= short_w:
                raise AlertValidationError(
                    f"{rule_prefix} ('{rule_id}'): 'long' must be an integer strictly greater than 'short' ({short_w})."
                )
            validated_rule["short"] = short_w
            validated_rule["long"] = long_w
            validated_rule["short_window"] = short_w
            validated_rule["long_window"] = long_w

        validated_rules.append(validated_rule)

    return validated_rules


def evaluate_level_rule(records: List[PriceRecord], rule: dict) -> List[Alert]:
    """
    Evaluate a level-crossing rule on chronologically sorted price records.

    Semantics:
    - Tracks previous non-equal side relative to threshold (+1 for above, -1 for below).
    - Crossing 'above' fires at date t iff the previous side was below (-1) and price is now strictly above (+1).
    - Crossing 'below' fires at date t iff the previous side was above (+1) and price is now strictly below (-1).
    - Touching the threshold (price == threshold) is a boundary state; it does not change the side,
      preventing false alerts if the price bounces off the threshold back to the same side.
    - No alert fires on day 0 (no prior state).
    """
    alerts: List[Alert] = []
    if len(records) < 2:
        return alerts

    threshold = rule["threshold"]
    direction = rule["direction"]
    rule_id = rule["id"]
    instrument = rule["instrument"]

    # Initial state determination on day 0
    p0 = records[0].close
    if p0 > threshold:
        last_side = 1
    elif p0 < threshold:
        last_side = -1
    else:
        last_side = 0

    for i in range(1, len(records)):
        curr_close = records[i].close
        curr_date = records[i].date
        prev_close = records[i - 1].close

        if curr_close > threshold:
            if direction == "above" and last_side == -1:
                level_str = f"{int(threshold)}" if threshold.is_integer() else f"{threshold}"
                desc = f"level above {level_str}"
                alerts.append(Alert(date=curr_date, rule_id=rule_id, instrument=instrument, description=desc))
            last_side = 1
        elif curr_close < threshold:
            if direction == "below" and last_side == 1:
                level_str = f"{int(threshold)}" if threshold.is_integer() else f"{threshold}"
                desc = f"level below {level_str}"
                alerts.append(Alert(date=curr_date, rule_id=rule_id, instrument=instrument, description=desc))
            last_side = -1
        else:
            # Price landed exactly on threshold: boundary state; last_side remains unchanged.
            pass

    return alerts


def evaluate_ma_cross_rule(records: List[PriceRecord], rule: dict) -> List[Alert]:
    """
    Evaluate moving average crossover rule on chronologically sorted price records.
    Requires at least (long_window + 1) records to detect an actual crossover.

    Semantics:
    - Moving averages are defined starting at index (long_window - 1).
    - Day (long_window - 1) establishes the baseline initial relative side (+1 if short > long, -1 if short < long).
    - Crossover 'above' fires when previous side was -1 (short <= long) and current short > long.
    - Crossover 'below' fires when previous side was +1 (short >= long) and current short < long.
    - Bounces (touching equality and retreating) do not trigger false crossovers.
    """
    alerts: List[Alert] = []
    short_w = rule["short_window"]
    long_w = rule["long_window"]
    direction = rule["direction"]
    rule_id = rule["id"]
    instrument = rule["instrument"]

    if len(records) <= long_w:
        return alerts

    closes = [r.close for r in records]

    def compute_sma(window: int, end_idx: int) -> float:
        return sum(closes[end_idx - window + 1 : end_idx + 1]) / window

    # Baseline on the very first day both SMAs exist: index long_w - 1
    init_short = compute_sma(short_w, long_w - 1)
    init_long = compute_sma(long_w, long_w - 1)
    diff = init_short - init_long

    if diff > 1e-9:
        last_side = 1
    elif diff < -1e-9:
        last_side = -1
    else:
        last_side = 0

    for i in range(long_w, len(records)):
        curr_date = records[i].date
        curr_short = compute_sma(short_w, i)
        curr_long = compute_sma(long_w, i)
        diff = curr_short - curr_long

        if diff > 1e-9:
            if direction == "above" and last_side == -1:
                desc = f"ma_cross {short_w}/{long_w} above"
                alerts.append(Alert(date=curr_date, rule_id=rule_id, instrument=instrument, description=desc))
            last_side = 1
        elif diff < -1e-9:
            if direction == "below" and last_side == 1:
                desc = f"ma_cross {short_w}/{long_w} below"
                alerts.append(Alert(date=curr_date, rule_id=rule_id, instrument=instrument, description=desc))
            last_side = -1
        else:
            # SMAs are exactly equal (within numerical epsilon); boundary state
            pass

    return alerts


def evaluate_all_rules(
    prices_by_instrument: Dict[str, List[PriceRecord]], rules: List[dict]
) -> List[Alert]:
    """Evaluate all rules across instruments and return sorted alerts."""
    all_alerts: List[Alert] = []

    for rule in rules:
        instrument = rule["instrument"]
        records = prices_by_instrument.get(instrument, [])

        if not records:
            continue

        if rule["type"] == "level":
            alerts = evaluate_level_rule(records, rule)
        elif rule["type"] == "ma_cross":
            alerts = evaluate_ma_cross_rule(records, rule)
        else:
            alerts = []

        all_alerts.extend(alerts)

    # Sort alerts by date ascending, then rule ID ascending
    all_alerts.sort(key=lambda a: (a.date, a.rule_id))
    return all_alerts


def format_alert(alert: Alert, id_width: int = 0, inst_width: int = 0) -> str:
    """Format an alert into a single readable output line matching the PDF specification."""
    id_str = f"{alert.rule_id:<{id_width}}" if id_width > 0 else alert.rule_id
    inst_str = f"{alert.instrument:<{inst_width}}" if inst_width > 0 else alert.instrument
    return f"{alert.date}  {id_str}  {inst_str}  {alert.description}"


def run(prices_path: str, rules_path: str) -> int:
    """Core application entrypoint."""
    prices_by_instrument = load_prices(prices_path)
    rules = load_rules(rules_path)

    alerts = evaluate_all_rules(prices_by_instrument, rules)

    if not alerts:
        return 0

    max_id_width = max(len(a.rule_id) for a in alerts)
    max_inst_width = max(len(a.instrument) for a in alerts)

    for alert in alerts:
        print(format_alert(alert, max_id_width, max_inst_width))

    return 0


def main() -> None:
    """CLI handler."""
    if len(sys.argv) != 3:
        sys.stderr.write("Usage: python alerts.py <prices.csv> <rules.json>\n")
        sys.exit(2)

    prices_path = sys.argv[1]
    rules_path = sys.argv[2]

    try:
        exit_code = run(prices_path, rules_path)
        sys.exit(exit_code)
    except AlertValidationError as err:
        sys.stderr.write(f"Error: {err}\n")
        sys.exit(1)
    except Exception as err:
        sys.stderr.write(f"Unexpected error: {err}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
