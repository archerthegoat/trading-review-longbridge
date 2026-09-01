from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "trading-center-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location(
    "refresh_daily_operations", SCRIPTS / "refresh_daily_operations.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load operations refresh helper")
REFRESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)

import render_trade_review_dashboard_v2 as DASHBOARD


REVIEW_DATE = "2026-08-31"
EXECUTION_TIME = "2026-08-31T15:00:00-04:00"
SYNTHETIC_CALL = "SYNTH.US260930C00010000"
SYNTHETIC_PUT = "SYNTH.US260930P00010000"
SYNTHETIC_0DTE_CALL = "SYNTH.US260831C00010000"
SYNTHETIC_0DTE_PUT = "SYNTH.US260831P00010000"


def execution(symbol: str, *, instrument: dict[str, str] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "side": "buy",
        "time": EXECUTION_TIME,
        # These fields model the private input boundary and must never be
        # copied into a display row.
        "order_id": "synthetic-order",
        "price": "123.45",
        "quantity": "2",
    }
    if instrument is not None:
        row["instrument"] = instrument
    return row


def plan(
    *,
    tool_kind: str = "leap_call",
    trade_symbol: str = "SYNTH.US:OPTION",
    underlying: str = "SYNTH.US",
    effective_at: str = "2026-08-30T09:00:00-04:00",
    confirmed_at: str = "2026-08-30T08:00:00-04:00",
    expires_at: str = "2026-09-30T00:00:00-04:00",
    plan_status: str = "confirmed",
    data_status: str = "complete",
    plan_stage: str = "pre_entry",
    direction: str = "long",
) -> dict[str, object]:
    return {
        "plan_stage": plan_stage,
        "underlying": underlying,
        "direction": direction,
        "effective_at": effective_at,
        "confirmed_at": confirmed_at,
        "expires_at": expires_at,
        "plan_status": plan_status,
        "data_status": data_status,
        "tool_kind": tool_kind,
        "trade_symbol": trade_symbol,
    }


class DailyOperationsRefreshTests(unittest.TestCase):
    def test_zero_dte_call_and_put_never_become_long_call(self) -> None:
        confirmed_leap = plan()
        for symbol, right in (
            (SYNTHETIC_0DTE_CALL, "call"),
            (SYNTHETIC_0DTE_PUT, "put"),
        ):
            with self.subTest(right=right):
                safe = REFRESH.safe_execution(execution(symbol), REVIEW_DATE)
                result = REFRESH.classify_execution(safe, [confirmed_leap])
                self.assertEqual(result["trade_type"], "zero_dte_option")
                self.assertEqual(result["option_right"], right)
                self.assertEqual(result["plan_status"], "mismatch")
                self.assertNotIn("Long Call", json.dumps(result, ensure_ascii=False))
                self.assertNotIn(symbol, json.dumps(result, ensure_ascii=False))

    def test_generic_option_without_plan_stays_generic_and_outside_plan(self) -> None:
        safe = REFRESH.safe_execution(execution(SYNTHETIC_CALL), REVIEW_DATE)
        result = REFRESH.classify_execution(safe, [])
        self.assertEqual(result["trade_type"], "other_option")
        self.assertEqual(result["option_right"], "call")
        self.assertEqual(result["plan_status"], "outside_plan")

    def test_exact_confirmed_leap_plan_supports_long_call(self) -> None:
        safe = REFRESH.safe_execution(execution(SYNTHETIC_CALL), REVIEW_DATE)
        result = REFRESH.classify_execution(safe, [plan()])
        self.assertEqual(result["trade_type"], "long_call")
        self.assertEqual(result["plan_status"], "confirmed_plan")
        self.assertEqual(result["symbol"], "SYNTH.US:OPTION")
        self.assertNotIn("260930", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("synthetic-order", json.dumps(result, ensure_ascii=False))

    def test_tool_fact_without_confirmed_plan_stays_outside_plan(self) -> None:
        safe = REFRESH.safe_execution(
            execution(
                SYNTHETIC_CALL,
                instrument={"tool_kind": "leap_call", "underlying": "SYNTH.US"},
            ),
            REVIEW_DATE,
        )
        result = REFRESH.classify_execution(safe, [])
        self.assertEqual(result["trade_type"], "other_option")
        self.assertEqual(result["plan_status"], "outside_plan")

    def test_leap_call_tool_fact_cannot_match_a_put(self) -> None:
        with self.assertRaises(REFRESH.OperationsRefreshError):
            REFRESH.safe_execution(
                execution(
                    SYNTHETIC_PUT,
                    instrument={"tool_kind": "leap_call", "underlying": "SYNTH.US"},
                ),
                REVIEW_DATE,
            )

    def test_same_underlying_different_tool_is_not_plan_aligned(self) -> None:
        safe = REFRESH.safe_execution(
            execution(
                "SYNTH.US",
                instrument={"tool_kind": "stock", "underlying": "SYNTH.US"},
            ),
            REVIEW_DATE,
        )
        result = REFRESH.classify_execution(safe, [plan()])
        self.assertEqual(result["trade_type"], "stock")
        self.assertEqual(result["plan_status"], "mismatch")

    def test_bare_ticker_without_tool_evidence_is_pending(self) -> None:
        safe = REFRESH.safe_execution(execution("SYNTH.US"), REVIEW_DATE)
        result = REFRESH.classify_execution(safe, [])
        self.assertEqual(result["trade_type"], "unknown")
        self.assertEqual(result["plan_status"], "unknown")

    def test_unknown_tool_evidence_is_unknown(self) -> None:
        safe = REFRESH.safe_execution(
            execution(
                SYNTHETIC_CALL,
                instrument={"tool_kind": "unknown", "underlying": "SYNTH.US"},
            ),
            REVIEW_DATE,
        )
        result = REFRESH.classify_execution(safe, [])
        self.assertEqual(result["trade_type"], "unknown")
        self.assertEqual(result["plan_status"], "unknown")

    def test_future_incomplete_partial_stale_and_expired_plans_are_pending(self) -> None:
        safe = REFRESH.safe_execution(execution(SYNTHETIC_CALL), REVIEW_DATE)
        cases = {
            "future": plan(effective_at="2026-09-01T09:00:00-04:00"),
            "at_execution": plan(effective_at=EXECUTION_TIME),
            "missing": {**plan(), "effective_at": None},
            "missing_expiry": {**plan(), "expires_at": None},
            "partial": plan(data_status="partial"),
            "stale": plan(data_status="stale"),
            "draft": plan(plan_status="draft"),
            "expired": plan(plan_status="expired"),
        }
        for label, candidate in cases.items():
            with self.subTest(status=label):
                result = REFRESH.classify_execution(safe, [candidate])
                self.assertEqual(result["plan_status"], "unknown")

    def test_incompatible_plan_direction_is_a_mismatch_not_long_call(self) -> None:
        safe = REFRESH.safe_execution(execution(SYNTHETIC_CALL), REVIEW_DATE)
        result = REFRESH.classify_execution(safe, [plan(direction="hedge")])
        self.assertEqual(result["trade_type"], "other_option")
        self.assertEqual(result["plan_status"], "mismatch")

    def test_refresh_rejects_zero_or_unverified_totals_but_allows_verified_zero(self) -> None:
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "dashboard_v2_complete.json").read_text()
        )
        display = DASHBOARD.project_display_snapshot(fixture)
        display["daily"]["operations"]["executions"] = {
            "count": 0,
            "data_status": "complete",
            "note": "合成成功空结果",
        }
        display["daily"]["operations"]["items"] = []
        before = copy.deepcopy(display)
        refreshed = REFRESH.refresh_snapshot(display, [], [])
        self.assertEqual(refreshed["daily"]["operations"]["status"], "empty")
        self.assertEqual(refreshed["daily"]["operations"]["items"], [])
        self.assertEqual(display, before)

        for status in ("partial", "stale"):
            broken = copy.deepcopy(before)
            broken["daily"]["meta"]["overall_status"] = "partial"
            broken["daily"]["operations"]["status"] = status
            broken["daily"]["operations"]["executions"] = {
                "count": None,
                "data_status": status,
                "note": "合成缺口",
            }
            with self.subTest(status=status), self.assertRaises(REFRESH.OperationsRefreshError):
                REFRESH.refresh_snapshot(broken, [], [])

    def test_refresh_rejects_mixed_dates_and_unknown_raw_structure(self) -> None:
        with self.assertRaises(REFRESH.OperationsRefreshError):
            REFRESH.safe_execution(
                {**execution("SYNTH.US"), "unexpected": "field"}, REVIEW_DATE
            )
        with self.assertRaises(REFRESH.OperationsRefreshError):
            REFRESH.safe_execution(
                {**execution("SYNTH.US"), "time": "2026-09-01T15:00:00-04:00"}, REVIEW_DATE
            )

    def test_output_does_not_mutate_private_input_or_retain_sensitive_fields(self) -> None:
        raw = execution(SYNTHETIC_CALL)
        raw_before = copy.deepcopy(raw)
        safe = REFRESH.safe_execution(raw, REVIEW_DATE)
        result = REFRESH.classify_execution(safe, [plan()])
        self.assertEqual(raw, raw_before)
        self.assertEqual(
            set(result),
            {
                "symbol", "display_name", "side", "trade_type", "option_right",
                "plan_status", "plan_status_note", "execution_count", "data_status",
            },
        )
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in ("order_id", "price", "quantity", "synthetic-order", "260930"):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()
