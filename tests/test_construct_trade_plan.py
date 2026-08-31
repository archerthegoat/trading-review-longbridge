from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import math
import os
import tempfile
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "trading-center-review" / "scripts" / "construct_trade_plan.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("construct_trade_plan", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load construct_trade_plan")
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)


def completed_dates(count: int) -> list[dt.date]:
    values: list[dt.date] = []
    current = dt.date(2025, 1, 2)
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += dt.timedelta(days=1)
    return values


def bars(count: int = 330) -> list[dict[str, object]]:
    rows = []
    for index, date in enumerate(completed_dates(count)):
        centre = 70 + index * 0.12 + math.sin(index / 7) * 3.5
        open_value = centre - 0.25
        close = centre + 0.25
        rows.append(
            {
                "timestamp": f"{date.isoformat()}T16:00:00-04:00",
                "open": f"{open_value:.4f}",
                "high": f"{centre + 1.1:.4f}",
                "low": f"{centre - 1.1:.4f}",
                "close": f"{close:.4f}",
                "volume": str(1_000_000 + index * 100),
                "is_complete": True,
            }
        )
    return rows


def request(setup: str = "pullback", stage: str = "pre_entry") -> dict[str, object]:
    rows = bars()
    last_date = str(rows[-1]["timestamp"])[:10]
    start_date = str(rows[0]["timestamp"])[:10]
    position_management = stage == "position_management"
    return {
        "schema_version": "trading-plan-request.v1",
        "generated_at": f"{last_date}T18:00:00-04:00",
        "plan_id": "plan-demo-pullback",
        "version": 1,
        "symbol": "DEMO.US",
        "display_name": "演示标的",
        "direction": "long",
        "setup_type": setup,
        "plan_stage": stage,
        "holding_horizon_sessions": 20,
        "minimum_reward_risk": "0.1",
        "max_invalidation_pct": "50",
        "tick_size": "0.01",
        "currency": "USD",
        "expires_at": f"{(dt.date.fromisoformat(last_date) + dt.timedelta(days=30)).isoformat()}T18:00:00-04:00",
        "source": {
            "provider": "Longbridge",
            "capability": "kline history",
            "period": "1D",
            "timezone": "America/New_York",
            "adjustment": "forward",
            "requested_start": start_date,
            "requested_end": last_date,
            "as_of": last_date,
        },
        "bars": rows,
        "actual_buy_verified": position_management,
        "parent_plan_id": "plan-demo-entry" if position_management else None,
        "parent_plan_version": 1 if position_management else None,
        "initial_buy_episode_key": "2026-04-08|DEMO.US|buy" if position_management else None,
    }


class ConstructTradePlanTests(unittest.TestCase):
    def test_qualified_plan_uses_ema_and_never_pre_entry_add(self) -> None:
        result = PLAN.construct_plan(request())
        self.assertIn(result["data_status"], {"complete", "partial"})
        self.assertEqual(result["plan_status"], "draft")
        self.assertIn("ema20", result["evidence"])
        self.assertIn("ema50", result["evidence"])
        self.assertIn("ema200", result["evidence"])
        self.assertNotIn("sma", json.dumps(result).lower())
        self.assertNotIn("add", {row["kind"] for row in result["zones"]})
        self.assertEqual(len(result["evidence_id"]), 64)
        self.assertEqual(len(result["content_hash"]), 64)

    def test_bottom_reversal_without_confirmation_is_observation_only(self) -> None:
        payload = request("bottom_reversal")
        previous = payload["bars"][-2]
        latest = payload["bars"][-1]
        previous.update({"open": "92", "high": "95", "low": "87", "close": "89"})
        latest.update({"open": "88", "high": "90", "low": "85", "close": "86"})
        result = PLAN.construct_plan(payload)
        self.assertEqual(result["plan_readiness"], "observation_only")
        self.assertFalse(result["evidence"]["bottom_reversal_confirmed"])
        self.assertNotIn("entry", {row["kind"] for row in result["zones"]})
        self.assertIn("bottom_reversal_confirmation_missing", result["gaps"])

    def test_bottom_reversal_confirmation_can_create_entry_candidate(self) -> None:
        payload = request("bottom_reversal")
        previous = payload["bars"][-2]
        latest = payload["bars"][-1]
        previous.update({"open": "81", "high": "82", "low": "77", "close": "79"})
        latest.update({"open": "80", "high": "86", "low": "78", "close": "85"})
        result = PLAN.construct_plan(payload)
        self.assertTrue(result["evidence"]["bottom_reversal_confirmed"])
        self.assertIn(result["plan_readiness"], {"ready_for_confirmation", "observation_only"})
        if result["plan_readiness"] == "ready_for_confirmation":
            self.assertIn("entry", {row["kind"] for row in result["zones"]})

    def test_position_management_requires_buy_and_is_only_stage_with_add(self) -> None:
        payload = request("position_management", "position_management")
        result = PLAN.construct_plan(payload)
        self.assertNotEqual(result["data_status"], "blocked")
        if result["plan_readiness"] == "ready_for_confirmation":
            self.assertIn("add", {row["kind"] for row in result["zones"]})
        invalid = copy.deepcopy(payload)
        invalid["actual_buy_verified"] = False
        blocked = PLAN.construct_plan(invalid)
        self.assertEqual(blocked["data_status"], "blocked")
        self.assertEqual(blocked["gap"]["category"], "stage")

    def test_provider_adjustment_and_coverage_fail_closed(self) -> None:
        provider = request()
        provider["source"]["provider"] = "yfinance"
        result = PLAN.construct_plan(provider)
        self.assertEqual(result["data_status"], "blocked")
        self.assertEqual(result["gap"]["category"], "provider")

        adjustment = request()
        adjustment["source"]["adjustment"] = "unknown"
        result = PLAN.construct_plan(adjustment)
        self.assertEqual(result["gap"]["category"], "adjustment")

        coverage = request()
        coverage["bars"] = coverage["bars"][:318]
        coverage["source"]["as_of"] = str(coverage["bars"][-1]["timestamp"])[:10]
        coverage["source"]["requested_end"] = coverage["source"]["as_of"]
        result = PLAN.construct_plan(coverage)
        self.assertEqual(result["gap"]["category"], "coverage")

    def test_terminal_incomplete_bar_is_removed_but_internal_incomplete_is_rejected(self) -> None:
        terminal = request()
        terminal["bars"].append(
            {
                **terminal["bars"][-1],
                "timestamp": "2026-04-09T16:00:00-04:00",
                "is_complete": False,
            }
        )
        terminal["source"]["requested_end"] = "2026-04-09"
        result = PLAN.construct_plan(terminal)
        self.assertNotEqual(result["data_status"], "blocked")
        self.assertEqual(result["evidence"]["bars_used"], 330)

        internal = request()
        internal["bars"][100]["is_complete"] = False
        result = PLAN.construct_plan(internal)
        self.assertEqual(result["gap"]["category"], "completion")

    def test_cli_writes_owner_only_packet(self) -> None:
        PLAN.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=PLAN.PRIVATE_ROOT, prefix="trading-plan-test-") as root_name:
            root = Path(root_name)
            root.chmod(0o700)
            input_path = root / "request.json"
            output_path = root / "draft.json"
            input_path.write_text(json.dumps(request(), ensure_ascii=False), encoding="utf-8")
            input_path.chmod(0o600)
            result = PLAN.main(["--input", str(input_path), "--output", str(output_path)])
            self.assertEqual(result, 0)
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["plan_status"], "draft")

    def test_daily_market_date_window_and_freshness_gates(self) -> None:
        duplicate = request()
        duplicate["bars"][1]["timestamp"] = str(duplicate["bars"][0]["timestamp"])[:10] + "T17:00:00-04:00"
        self.assertEqual(PLAN.construct_plan(duplicate)["gap"]["category"], "ordering")
        outside = request()
        outside["source"]["requested_start"] = str(outside["bars"][1]["timestamp"])[:10]
        self.assertEqual(PLAN.construct_plan(outside)["gap"]["category"], "scope")
        stale = request()
        stale["generated_at"] = "2026-04-20T18:00:00-04:00"
        self.assertEqual(PLAN.construct_plan(stale)["gap"]["category"], "freshness")

    def test_risk_gate_uses_rounded_worst_case_boundaries(self) -> None:
        result = PLAN.construct_plan(request())
        self.assertEqual(result["data_status"], "complete")
        from decimal import Decimal
        zones = {row["kind"]: row for row in result["zones"]}
        entry = Decimal(zones["entry"]["high"])
        stop = Decimal(zones["invalidation"]["low"])
        target = Decimal(zones["reduce"]["low"])
        rr = (target - entry) / (entry - stop)
        self.assertLess(abs(rr - Decimal(result["evidence"]["reward_risk"])), Decimal("0.000001"))


if __name__ == "__main__":
    unittest.main()
