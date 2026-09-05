"""Synthetic-only regression tests at the stdin/stdout privacy boundary."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/daily-trade-journal/scripts/sanitize_broker_facts.py"
DAY = "2026-09-04"
PRIVATE = "SYNTHETIC_PRIVATE_SENTINEL"


def row(symbol="SYNTH.US", time="2026-09-04T10:00:00-04:00", **extra):
    return {"symbol": symbol, "side": "buy", "time": time,
            "order_id": PRIVATE, "price": PRIVATE, "quantity": PRIVATE, **extra}


class SanitizeTests(unittest.TestCase):
    def call(self, value, *, kind="executions", flags=(), raw=False, date_args=None):
        if date_args is None:
            date_args = ("--review-date", DAY) if kind == "executions" else ("--as-of-date", DAY)
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--kind", kind,
                               *date_args, *flags],
                              input=value if raw else json.dumps(value), text=True, capture_output=True)
        self.assertEqual(proc.stderr, "")
        self.assertNotIn(PRIVATE, proc.stdout)
        return proc, json.loads(proc.stdout)

    def blocked(self, value, **kwargs):
        proc, result = self.call(value, **kwargs)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["reason"], "input_validation_failed")

    def test_approved_fields_only(self):
        proc, result = self.call([row(instrument={"underlying": "SYNTH.US", "tool_kind": "stock"})])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result["schema_version"], "daily-trade-journal-broker-preview.v2")
        self.assertEqual(result["rows"], [{"underlying": "SYNTH.US", "action": "买入",
                                         "tool": "正股", "sequence": 1, "cutoff_relations": []}])
        self.assertNotIn("10:00", proc.stdout)

    def test_options_expose_only_underlying_and_tool(self):
        symbols = ["SYNTH260904C00100000.US", "SYNTH260911P00100000.US"]
        proc, result = self.call([row(s) for s in symbols])
        self.assertEqual([r["tool"] for r in result["rows"]], ["0DTE Call", "Put"])
        for symbol in symbols:
            self.assertNotIn(symbol, proc.stdout)
        self.assertNotIn("00100000", proc.stdout)

    def test_sort_ties_and_public_cutoffs(self):
        _, result = self.call([row(time="2026-09-04T11:00:00-04:00"), row(), row()],
                             flags=("--cutoff", "2026-09-04T14:00:00Z",
                                    "--cutoff", "2026-09-04T14:30:00Z"))
        self.assertEqual([r["sequence"] for r in result["rows"]], [1, 1, 2])
        self.assertEqual(result["rows"][0]["cutoff_relations"], ["equal", "before"])
        self.assertEqual(result["rows"][2]["cutoff_relations"], ["after", "after"])

    def test_atomic_failure_after_valid_row(self):
        self.blocked([row(), row(symbol=PRIVATE)])

    def test_unknown_option_shape_and_digit_root_fail_closed(self):
        for symbol in ["SYNTH260904X00100000.US", "SYNTH260904CWRONG.US", "SYNTH123.US"]:
            with self.subTest(symbol=symbol):
                self.blocked([row(symbol)])

    def test_missing_or_conflicting_required_fields(self):
        for value in [[{"symbol": "SYNTH.US"}], [row(filled_at="2026-09-04T14:00:00Z")],
                      [row(underlying="OTHER.US")], [row(side=PRIVATE)], [None]]:
            self.blocked(value)

    def test_invalid_dates_and_naive_time(self):
        for time in ["2026-09-05T10:00:00-04:00", "2026-09-04T10:00:00", PRIVATE]:
            self.blocked([row(time=time)])

    def test_window_uses_new_york_date_not_utc_date(self):
        proc, result = self.call([row(time="2026-09-05T00:30:00Z")])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result["review_date"], DAY)
        self.blocked([row(time="2026-09-04T00:30:00Z")])

    def test_empty_success_is_not_empty_provider_output(self):
        proc, result = self.call([])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result["status"], "empty")
        for content in ["", PRIVATE, '{"rows": []}', '[{"symbol":"SYNTH.US","symbol":"OTHER.US"}]', '[NaN]']:
            self.blocked(content, raw=True)

    def test_positions_are_current_rows_without_quantity_or_inferred_equity_class(self):
        _, result = self.call([row(), row("SYNTH260911P00100000.US")], kind="positions")
        self.assertEqual(result["rows"], [{"underlying": "SYNTH.US", "tool": "无法识别"},
                                         {"underlying": "SYNTH.US", "tool": "Put"}])
        self.assertEqual(result["as_of_date"], DAY)
        self.assertNotIn("review_date", result)
        self.blocked([row()], kind="positions", flags=("--cutoff", "2026-09-04T14:00:00Z"))

    def test_positions_require_collection_date_not_review_date(self):
        self.blocked([row("SYNTH260904C00100000.US")], kind="positions",
                     date_args=("--review-date", DAY))
        self.blocked([row()], kind="positions", date_args=())
        self.blocked([row()], date_args=("--as-of-date", DAY))

    def test_us_positions_wrapper_extracts_only_stocks_and_options(self):
        value = {
            "account_type": PRIVATE,
            "cash_buy_power": PRIVATE,
            "cash_list": [{"total_cash": PRIVATE}],
            "stock_list": [{"counter_id": "SYNTH.US", "quantity": PRIVATE}],
            "option_list": [{"counter_id": "SYNTH260904P00100000.US",
                             "underlying_counter_id": "SYNTH.US", "average_cost": PRIVATE}],
            "crypto_list": [],
        }
        proc, result = self.call(value, kind="positions")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result["rows"], [{"underlying": "SYNTH.US", "tool": "无法识别"},
                                         {"underlying": "SYNTH.US", "tool": "0DTE Put"}])

    def test_us_positions_wrapper_fails_closed_on_conflicts_or_unsupported_assets(self):
        self.blocked({"stock_list": [], "option_list": [],
                      "crypto_list": [{"counter_id": PRIVATE}], "cash_list": []}, kind="positions")
        self.blocked({"stock_list": [{"symbol": "SYNTH.US", "counter_id": "OTHER.US"}],
                      "option_list": [], "crypto_list": [], "cash_list": []}, kind="positions")
        self.blocked({"stock_list": [],
                      "option_list": [{"counter_id": "SYNTH260904P00100000.US",
                                       "underlying_counter_id": "OTHER.US"}],
                      "crypto_list": [], "cash_list": []}, kind="positions")
        self.blocked({"stock_list": [], "option_list": [], "crypto_list": []},
                     kind="positions")

    def test_instrument_mapping_needs_consistent_evidence(self):
        evidence = {"underlying": "SYNTH.US", "tool_kind": "单股杠杆 ETF"}
        _, result = self.call([row(instrument=evidence)], kind="positions")
        self.assertEqual(result["rows"][0]["tool"], "单股杠杆 ETF")
        self.blocked([row(instrument={**evidence, "underlying": "OTHER.US"})])

    def test_batch_limit_and_malformed_json_do_not_echo(self):
        self.blocked('"' + PRIVATE + 'x' * (8 * 1024 * 1024) + '"', raw=True)
        self.blocked('[{"symbol":"' + PRIVATE, raw=True)


if __name__ == "__main__":
    unittest.main()
