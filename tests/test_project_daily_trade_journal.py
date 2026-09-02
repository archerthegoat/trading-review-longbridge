from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "daily-trade-journal" / "scripts" / "project_daily_trade_journal.py"
SPEC = importlib.util.spec_from_file_location("project_daily_trade_journal", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load daily trade journal projector")
PROJECT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROJECT
SPEC.loader.exec_module(PROJECT)


REVIEW_DATE = "2026-08-31"
TIME = "2026-08-31T15:00:00-04:00"
CALENDAR = {"trading_dates": [REVIEW_DATE]}


def execution(symbol: str, *, side: str = "buy", instrument: dict[str, str] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "side": side,
        "time": TIME,
        "order_id": "private-order-id",
        "execution_id": "private-execution-id",
        "price": "123.45",
        "quantity": "2",
        "commission": "9.99",
    }
    if instrument is not None:
        row["instrument"] = instrument
    return row


def plan(*, underlying: str = "SYNTH.US", action: str = "buy", tool: str = "stock", **extra: object) -> dict[str, object]:
    result: dict[str, object] = {
        "underlying": underlying,
        "action": action,
        "tool": tool,
        "status": "confirmed",
    }
    result.update(extra)
    return result


class DailyTradeJournalProjectorTests(unittest.TestCase):
    def test_complete_merges_split_fills_and_matches_same_row(self) -> None:
        raw = [
            execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
            execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
        ]
        result = PROJECT.project_facts(REVIEW_DATE, raw, trading_calendar=CALENDAR, plans=[plan()])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            result["executions"],
            [{"underlying": "SYNTH.US", "action": "买入", "tool": "正股", "alignment": "按计划"}],
        )

    def test_buy_and_sell_are_separate_rows(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [
                execution("SYNTH.US", side="buy", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
                execution("SYNTH.US", side="sell", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
            ],
            trading_calendar=CALENDAR,
            plans=[plan(action="buy")],
        )
        self.assertEqual(
            {(row["action"], row["alignment"]) for row in result["executions"]},
            {("买入", "按计划"), ("卖出", "偏离计划")},
        )

    def test_zero_dte_never_becomes_long_call(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260831C00010000")],
            trading_calendar=CALENDAR,
            plans=[plan(tool="long_call")],
        )
        row = result["executions"][0]
        self.assertEqual(row["tool"], "0DTE 期权")
        self.assertNotIn("Long Call", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("260831C00010000", json.dumps(result, ensure_ascii=False))

    def test_other_option_and_unknown_tool_are_safe(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [
                execution("SYNTH.US260930P00010000"),
                execution("UNKNOWN.US"),
            ],
            trading_calendar=CALENDAR,
        )
        self.assertEqual({row["tool"] for row in result["executions"]}, {"其他期权", "无法识别"})
        self.assertTrue(all(row["alignment"] == "无法核对" for row in result["executions"]))

    def test_explicit_different_or_prohibited_plan_is_mismatch(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[plan(tool="single_stock_leveraged_etf")],
        )
        self.assertEqual(result["executions"][0]["alignment"], "偏离计划")
        prohibited = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[{"underlying": "SYNTH.US", "prohibited": True, "status": "confirmed"}],
        )
        self.assertEqual(prohibited["executions"][0]["alignment"], "偏离计划")

    def test_no_plan_or_incomplete_plan_is_unknown(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[plan(status="draft")],
        )
        self.assertEqual(result["executions"][0]["alignment"], "无法核对")

    def test_plan_needs_explicit_confirmation_evidence(self) -> None:
        unmarked = {"underlying": "SYNTH.US", "action": "buy", "tool": "stock"}
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[unmarked],
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["executions"][0]["alignment"], "无法核对")
        explicitly_confirmed = {**unmarked, "confirmed": True}
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[explicitly_confirmed],
        )
        self.assertEqual(result["executions"][0]["alignment"], "按计划")

    def test_empty_is_distinct_from_blocked(self) -> None:
        empty = PROJECT.project_facts(REVIEW_DATE, [], trading_calendar=CALENDAR)
        self.assertEqual(empty["status"], "empty")
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(REVIEW_DATE, [{"symbol": "SYNTH.US", "side": "buy"}], trading_calendar=CALENDAR)

    def test_calendar_artifact_is_required_for_complete_or_empty(self) -> None:
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(REVIEW_DATE, [])
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(REVIEW_DATE, [execution("SYNTH.US")])

    def test_open_calendar_entry_requires_completed_true(self) -> None:
        open_calendar = {"sessions": [{"date": REVIEW_DATE, "status": "open"}]}
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(REVIEW_DATE, [], trading_calendar=open_calendar)
        completed_open_calendar = {"sessions": [{"date": REVIEW_DATE, "status": "open", "completed": True}]}
        result = PROJECT.project_facts(REVIEW_DATE, [], trading_calendar=completed_open_calendar)
        self.assertEqual(result["status"], "empty")

    def test_contradictory_empty_envelopes_fail_closed(self) -> None:
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(
                REVIEW_DATE,
                {"status": "empty", "executions": [execution("SYNTH.US")]},
                trading_calendar=CALENDAR,
            )
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(
                REVIEW_DATE,
                [],
                trading_calendar=CALENDAR,
                plans=[{"status": "empty", "plans": [plan()]}],
            )

    def test_calendar_half_open_window_and_review_date(self) -> None:
        calendar = {
            "trading_dates": [REVIEW_DATE],
            "start_at": "2026-08-31T09:30:00-04:00",
            "end_at": "2026-08-31T16:00:00-04:00",
        }
        self.assertEqual(
            PROJECT.project_facts(REVIEW_DATE, [execution("SYNTH.US")], trading_calendar=calendar)["status"],
            "complete",
        )
        outside = execution("SYNTH.US")
        outside["time"] = "2026-08-31T16:00:00-04:00"
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(REVIEW_DATE, [outside], trading_calendar=calendar)

    def test_public_output_contains_no_sensitive_fields(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260930C00010000")],
            trading_calendar=CALENDAR,
        )
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in ("price", "quantity", "order_id", "execution_id", "commission", "private-order-id", "260930C00010000"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(set(result), {"schema_version", "review_date", "status", "executions"})

    def test_malformed_or_private_field_is_blocked_without_success_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-trade-journal-") as directory:
            root = Path(directory)
            raw_path = root / "raw.json"
            calendar_path = root / "calendar.json"
            output_path = root / "facts.json"
            raw_path.write_text(json.dumps([execution("SYNTH.US")]), encoding="utf-8")
            os.chmod(raw_path, 0o600)
            calendar_path.write_text(json.dumps(CALENDAR), encoding="utf-8")
            os.chmod(calendar_path, 0o600)
            command = [
                sys.executable,
                str(SCRIPT),
                "--review-date",
                REVIEW_DATE,
                "--raw-executions",
                str(raw_path),
                "--trading-calendar",
                str(calendar_path),
                "--output",
                str(output_path),
            ]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            raw_path.write_text(json.dumps([{**execution("SYNTH.US"), "api_key": "secret"}]), encoding="utf-8")
            os.chmod(raw_path, 0o600)
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            blocked = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["executions"], [])
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertNotIn("secret", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
