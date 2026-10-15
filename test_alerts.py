#!/usr/bin/env python3
"""
Automated unit tests for Price Alert Tool.
Covers level crossing, moving-average crossovers, equality semantics,
date parsing, duplicate rejection, schema validation, and CLI execution.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from alerts import (
    Alert,
    AlertValidationError,
    PriceRecord,
    evaluate_all_rules,
    evaluate_level_rule,
    evaluate_ma_cross_rule,
    load_prices,
    load_rules,
)


class TestLevelCrossing(unittest.TestCase):
    def setUp(self):
        self.rule_above = {
            "id": "R_ABOVE",
            "instrument": "AAPL",
            "type": "level",
            "direction": "above",
            "threshold": 100.0,
        }
        self.rule_below = {
            "id": "R_BELOW",
            "instrument": "AAPL",
            "type": "level",
            "direction": "below",
            "threshold": 100.0,
        }

    def test_crossing_above(self):
        prices = [
            PriceRecord("2024-01-01", "AAPL", 98.0),
            PriceRecord("2024-01-02", "AAPL", 102.0),
        ]
        alerts = evaluate_level_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-02")
        self.assertEqual(alerts[0].description, "level above 100")

    def test_crossing_below(self):
        prices = [
            PriceRecord("2024-01-01", "AAPL", 105.0),
            PriceRecord("2024-01-02", "AAPL", 95.0),
        ]
        alerts = evaluate_level_rule(prices, self.rule_below)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-02")
        self.assertEqual(alerts[0].rule_id, "R_BELOW")
        self.assertEqual(alerts[0].description, "level below 100")

    def test_no_repeated_alerts_remaining_above(self):
        prices = [
            PriceRecord("2024-01-01", "AAPL", 95.0),
            PriceRecord("2024-01-02", "AAPL", 105.0),  # cross above
            PriceRecord("2024-01-03", "AAPL", 110.0),  # still above
            PriceRecord("2024-01-04", "AAPL", 108.0),  # still above
        ]
        alerts = evaluate_level_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-02")

    def test_no_repeated_alerts_remaining_below(self):
        prices = [
            PriceRecord("2024-01-01", "AAPL", 105.0),
            PriceRecord("2024-01-02", "AAPL", 95.0),   # cross below
            PriceRecord("2024-01-03", "AAPL", 92.0),   # still below
            PriceRecord("2024-01-04", "AAPL", 90.0),   # still below
        ]
        alerts = evaluate_level_rule(prices, self.rule_below)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-02")

    def test_boundary_equality_then_cross_above(self):
        # 95 -> 100 (equal, no alert) -> 105 (crosses above)
        prices = [
            PriceRecord("2024-01-01", "AAPL", 95.0),
            PriceRecord("2024-01-02", "AAPL", 100.0),
            PriceRecord("2024-01-03", "AAPL", 105.0),
        ]
        alerts = evaluate_level_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-03")

    def test_boundary_equality_then_cross_below(self):
        # 105 -> 100 (equal, no alert) -> 95 (crosses below)
        prices = [
            PriceRecord("2024-01-01", "AAPL", 105.0),
            PriceRecord("2024-01-02", "AAPL", 100.0),
            PriceRecord("2024-01-03", "AAPL", 95.0),
        ]
        alerts = evaluate_level_rule(prices, self.rule_below)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-03")

    def test_bounce_off_threshold_does_not_alert(self):
        # Price starts above (110), touches 100, bounces back up to 110.
        # It never came from below, so crossing 'above' must not fire!
        prices = [
            PriceRecord("2024-01-01", "AAPL", 110.0),
            PriceRecord("2024-01-02", "AAPL", 100.0),
            PriceRecord("2024-01-03", "AAPL", 110.0),
        ]
        alerts_above = evaluate_level_rule(prices, self.rule_above)
        self.assertEqual(len(alerts_above), 0)

        alerts_below = evaluate_level_rule(prices, self.rule_below)
        self.assertEqual(len(alerts_below), 0)

    def test_bounce_from_below_does_not_alert(self):
        # Price starts below (90), touches 100, falls back down to 90.
        # It never came from above, so crossing 'below' must not fire!
        prices = [
            PriceRecord("2024-01-01", "AAPL", 90.0),
            PriceRecord("2024-01-02", "AAPL", 100.0),
            PriceRecord("2024-01-03", "AAPL", 90.0),
        ]
        alerts_above = evaluate_level_rule(prices, self.rule_above)
        self.assertEqual(len(alerts_above), 0)

        alerts_below = evaluate_level_rule(prices, self.rule_below)
        self.assertEqual(len(alerts_below), 0)

    def test_single_price_produces_no_alerts(self):
        prices = [PriceRecord("2024-01-01", "AAPL", 120.0)]
        alerts = evaluate_level_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 0)


class TestMovingAverageCrossover(unittest.TestCase):
    def setUp(self):
        self.rule_above = {
            "id": "MA_ABOVE",
            "instrument": "MSFT",
            "type": "ma_cross",
            "direction": "above",
            "short_window": 2,
            "long_window": 3,
        }
        self.rule_below = {
            "id": "MA_BELOW",
            "instrument": "MSFT",
            "type": "ma_cross",
            "direction": "below",
            "short_window": 2,
            "long_window": 3,
        }

    def test_ma_crossover_above(self):
        # Day 1: 100 (SMA2: -, SMA3: -)
        # Day 2: 90  (SMA2: 95, SMA3: -)
        # Day 3: 80  (SMA2: 85, SMA3: 90.0) -> short(85) < long(90) -> last_side = -1
        # Day 4: 120 (SMA2: 100, SMA3: 96.67) -> short(100) > long(96.67) -> Crosses above!
        prices = [
            PriceRecord("2024-01-01", "MSFT", 100.0),
            PriceRecord("2024-01-02", "MSFT", 90.0),
            PriceRecord("2024-01-03", "MSFT", 80.0),
            PriceRecord("2024-01-04", "MSFT", 120.0),
        ]
        alerts = evaluate_ma_cross_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-04")
        self.assertEqual(alerts[0].rule_id, "MA_ABOVE")
        self.assertEqual(alerts[0].description, "ma_cross 2/3 above")

    def test_ma_crossover_below(self):
        # Day 1: 100
        # Day 2: 110
        # Day 3: 120 (SMA2: 115, SMA3: 110) -> short > long -> last_side = 1
        # Day 4: 80  (SMA2: 100, SMA3: 103.33) -> short < long -> Crosses below!
        prices = [
            PriceRecord("2024-01-01", "MSFT", 100.0),
            PriceRecord("2024-01-02", "MSFT", 110.0),
            PriceRecord("2024-01-03", "MSFT", 120.0),
            PriceRecord("2024-01-04", "MSFT", 80.0),
        ]
        alerts = evaluate_ma_cross_rule(prices, self.rule_below)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-04")
        self.assertEqual(alerts[0].rule_id, "MA_BELOW")
        self.assertEqual(alerts[0].description, "ma_cross 2/3 below")

    def test_no_repeated_ma_crossover_alerts(self):
        # Short stays above long for multiple consecutive days
        prices = [
            PriceRecord("2024-01-01", "MSFT", 100.0),
            PriceRecord("2024-01-02", "MSFT", 90.0),
            PriceRecord("2024-01-03", "MSFT", 80.0),   # short < long
            PriceRecord("2024-01-04", "MSFT", 120.0),  # cross above
            PriceRecord("2024-01-05", "MSFT", 130.0),  # still above
            PriceRecord("2024-01-06", "MSFT", 140.0),  # still above
        ]
        alerts = evaluate_ma_cross_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].date, "2024-01-04")

    def test_insufficient_history(self):
        # long_window = 3 requires at least 4 data points (long_window + 1)
        prices = [
            PriceRecord("2024-01-01", "MSFT", 100.0),
            PriceRecord("2024-01-02", "MSFT", 110.0),
            PriceRecord("2024-01-03", "MSFT", 120.0),
        ]
        alerts = evaluate_ma_cross_rule(prices, self.rule_above)
        self.assertEqual(len(alerts), 0)


class TestInputValidation(unittest.TestCase):
    def test_shuffled_dates_and_multiple_instruments(self):
        csv_content = (
            "date,instrument,close\n"
            "2024-01-05,AAPL,152.50\n"
            "2024-01-02,MSFT,300.00\n"
            "2024-01-02,AAPL,148.00\n"
            "2024-01-03,MSFT,302.00\n"
            "2024-01-03,AAPL,149.50\n"
        )
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            records = load_prices(temp_path)
            self.assertIn("AAPL", records)
            self.assertIn("MSFT", records)
            # Check AAPL sorted chronologically
            aapl_dates = [r.date for r in records["AAPL"]]
            self.assertEqual(aapl_dates, ["2024-01-02", "2024-01-03", "2024-01-05"])
        finally:
            os.remove(temp_path)

    def test_duplicate_record_rejected(self):
        csv_content = (
            "date,instrument,close\n"
            "2024-01-02,AAPL,148.00\n"
            "2024-01-02,AAPL,149.00\n"
        )
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_prices(temp_path)
            self.assertIn("Duplicate record", str(ctx.exception))
            self.assertIn("AAPL", str(ctx.exception))
            self.assertIn("2024-01-02", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_invalid_date_rejected(self):
        csv_content = "date,instrument,close\n2024-02-30,AAPL,150.0\n"
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_prices(temp_path)
            self.assertIn("Invalid date format", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_non_numeric_price_rejected(self):
        csv_content = "date,instrument,close\n2024-01-02,AAPL,N/A\n"
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_prices(temp_path)
            self.assertIn("Invalid close price", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_negative_price_rejected(self):
        csv_content = "date,instrument,close\n2024-01-02,AAPL,-10.5\n"
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_prices(temp_path)
            self.assertIn("Must be a positive finite number", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_missing_csv_columns_rejected(self):
        csv_content = "date,price\n2024-01-02,100.0\n"
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_prices(temp_path)
            self.assertIn("Missing required CSV column", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_duplicate_rule_id_rejected(self):
        rules_json = (
            '[{"id": "R1", "instrument": "AAPL", "type": "level", "direction": "above", "threshold": 100},'
            ' {"id": "R1", "instrument": "AAPL", "type": "level", "direction": "below", "threshold": 100}]'
        )
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f:
            f.write(rules_json)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_rules(temp_path)
            self.assertIn("Duplicate rule ID 'R1'", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_invalid_rule_type_rejected(self):
        rules_json = '[{"id": "R1", "instrument": "AAPL", "type": "rsi", "direction": "above", "threshold": 100}]'
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f:
            f.write(rules_json)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_rules(temp_path)
            self.assertIn("'type' must be 'level' or 'ma_cross'", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_invalid_ma_window_size_rejected(self):
        # short >= long
        rules_json = (
            '[{"id": "R1", "instrument": "AAPL", "type": "ma_cross", "direction": "above", '
            '"short": 5, "long": 5}]'
        )
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f:
            f.write(rules_json)
            temp_path = f.name

        try:
            with self.assertRaises(AlertValidationError) as ctx:
                load_rules(temp_path)
            self.assertIn("strictly greater than 'short'", str(ctx.exception))
        finally:
            os.remove(temp_path)

    def test_pdf_schema_rule_parsing(self):
        rules_json = (
            '[\n'
            '  {"id": "r1", "instrument": "INFY", "type": "level", "direction": "below", "level": 1500},\n'
            '  {"id": "r2", "instrument": "TCS", "type": "ma_cross", "direction": "above", "short": 5, "long": 20}\n'
            ']'
        )
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f:
            f.write(rules_json)
            temp_path = f.name

        try:
            rules = load_rules(temp_path)
            self.assertEqual(len(rules), 2)
            self.assertEqual(rules[0]["level"], 1500.0)
            self.assertEqual(rules[0]["threshold"], 1500.0)
            self.assertEqual(rules[1]["short"], 5)
            self.assertEqual(rules[1]["long"], 20)
        finally:
            os.remove(temp_path)


class TestDeterministicSorting(unittest.TestCase):
    def test_sorting_by_date_then_rule_id(self):
        prices = {
            "AAPL": [
                PriceRecord("2024-01-01", "AAPL", 90.0),
                PriceRecord("2024-01-02", "AAPL", 110.0),  # cross above 100
                PriceRecord("2024-01-03", "AAPL", 125.0),  # cross above 120
            ],
            "MSFT": [
                PriceRecord("2024-01-01", "MSFT", 190.0),
                PriceRecord("2024-01-02", "MSFT", 210.0),  # cross above 200
            ]
        }
        rules = [
            {"id": "Z_RULE_MSFT", "instrument": "MSFT", "type": "level", "direction": "above", "threshold": 200.0},
            {"id": "A_RULE_AAPL", "instrument": "AAPL", "type": "level", "direction": "above", "threshold": 100.0},
            {"id": "B_RULE_AAPL", "instrument": "AAPL", "type": "level", "direction": "above", "threshold": 120.0},
        ]
        alerts = evaluate_all_rules(prices, rules)
        # Expected:
        # 2024-01-02: A_RULE_AAPL, then Z_RULE_MSFT (sorted by date, then rule_id)
        # 2024-01-03: B_RULE_AAPL
        self.assertEqual(len(alerts), 3)
        self.assertEqual(alerts[0].date, "2024-01-02")
        self.assertEqual(alerts[0].rule_id, "A_RULE_AAPL")
        self.assertEqual(alerts[1].date, "2024-01-02")
        self.assertEqual(alerts[1].rule_id, "Z_RULE_MSFT")
        self.assertEqual(alerts[2].date, "2024-01-03")
        self.assertEqual(alerts[2].rule_id, "B_RULE_AAPL")

    def test_no_alerts_empty_list(self):
        prices = {"AAPL": [PriceRecord("2024-01-01", "AAPL", 100.0), PriceRecord("2024-01-02", "AAPL", 100.0)]}
        rules = [{"id": "R1", "instrument": "AAPL", "type": "level", "direction": "above", "threshold": 150.0}]
        alerts = evaluate_all_rules(prices, rules)
        self.assertEqual(len(alerts), 0)


class TestCLIExecution(unittest.TestCase):
    def test_subprocess_sample_run(self):
        result = subprocess.run(
            [sys.executable, "alerts.py", "prices.csv", "rules.json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        lines = [line.rstrip() for line in result.stdout.strip().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "2026-09-03  r1  INFY  level below 1500")
        self.assertEqual(lines[1], "2026-09-24  r2  TCS   ma_cross 5/20 above")

    def test_subprocess_missing_args(self):
        result = subprocess.run(
            [sys.executable, "alerts.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)

    def test_subprocess_nonexistent_file(self):
        result = subprocess.run(
            [sys.executable, "alerts.py", "nonexistent.csv", "rules.json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: Price file not found", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
