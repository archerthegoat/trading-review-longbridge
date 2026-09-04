from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
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


def option_execution(symbol: str, *, side: str = "buy") -> dict[str, object]:
    return execution(symbol, side=side)


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
    def run_cli(
        self,
        root: Path,
        raw: list[dict[str, object]],
        output: Path,
        *,
        private_preview: Path | None = None,
        raw_positions: list[dict[str, object]] | None = None,
        owner_preview: Path | None = None,
        confirmed_plans: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raw_path = root / "raw.json"
        calendar_path = root / "calendar.json"
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
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
            str(output),
        ]
        if private_preview is not None:
            command.extend(["--private-preview", str(private_preview)])
        if raw_positions is not None:
            positions_path = root / "positions.json"
            positions_path.write_text(json.dumps(raw_positions), encoding="utf-8")
            os.chmod(positions_path, 0o600)
            command.extend(["--raw-positions", str(positions_path)])
        if owner_preview is not None:
            command.extend(["--owner-preview", str(owner_preview)])
        if confirmed_plans is not None:
            plans_path = root / "plans.json"
            plans_path.write_text(json.dumps(confirmed_plans), encoding="utf-8")
            os.chmod(plans_path, 0o600)
            command.extend(["--confirmed-plans", str(plans_path)])
        bootstrap = """
import importlib.util
from pathlib import Path
import sys

script = Path(sys.argv[1])
private_root = Path(sys.argv[2]).resolve()
arguments = sys.argv[3:]
spec = importlib.util.spec_from_file_location("project_daily_trade_journal", script)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load daily trade journal projector")
project = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = project
spec.loader.exec_module(project)
project.PRIVATE_PREVIEW_ROOT = private_root
raise SystemExit(project.main(arguments))
        """
        return subprocess.run(
            [sys.executable, "-c", bootstrap, str(SCRIPT), str(root.resolve()), *command[2:]],
            capture_output=True,
            text=True,
            check=False,
        )

    def version(
        self,
        *,
        version: str,
        confirmed_at: str,
        plans: list[dict[str, object]],
        context_available: bool = False,
        holding_underlyings: list[str] | None = None,
        observation_underlyings: list[str] | None = None,
        ignored_underlyings: list[str] | None = None,
        context_underlyings: list[str] | None = None,
        tool_by_underlying: dict[str, str] | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": PROJECT.CONFIRMED_PLAN_SCHEMA_VERSION,
            "version": version,
            "review_date": REVIEW_DATE,
            "status": "confirmed",
            "confirmation_status": "confirmed",
            "confirmed_at": confirmed_at,
            "effective_at": confirmed_at,
            "source_schema": "synthetic-plan.v1",
            "source_content_hash": "a" * 64,
            "approved_draft_schema_version": "synthetic-draft.v1",
            "approved_draft_hash": "b" * 64,
            "plans": plans,
            "context_available": context_available,
        }
        optional = {
            "holding_underlyings": holding_underlyings,
            "observation_underlyings": observation_underlyings,
            "ignored_underlyings": ignored_underlyings,
            "context_underlyings": context_underlyings,
            "tool_by_underlying": tool_by_underlying,
        }
        result.update({key: value for key, value in optional.items() if value is not None})
        return result

    def test_complete_merges_split_fills_and_matches_same_row(self) -> None:
        raw = [
            execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
            execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
        ]
        result = PROJECT.project_facts(REVIEW_DATE, raw, trading_calendar=CALENDAR, plans=[plan()])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            result["executions"],
            [{"underlying": "SYNTH.US", "action": "买入", "alignment": "按计划"}],
        )

    def test_private_preview_separates_contracts_merges_fills_and_sides(self) -> None:
        rows = [
            option_execution("SYNTH260930C190000.US"),
            option_execution("SYNTH260930C190000.US"),
            option_execution("SYNTH260930C190000.US", side="sell"),
            option_execution("SYNTH261001C190000.US"),
            option_execution("SYNTH260930P190000.US"),
            option_execution("SYNTH260930C191000.US"),
        ]
        projected, facts = PROJECT._project_facts(REVIEW_DATE, rows, trading_calendar=CALENDAR)
        text = PROJECT._private_preview_text(PROJECT.parse_date(REVIEW_DATE), facts)
        PROJECT._assert_private_preview_text(text, PROJECT.parse_date(REVIEW_DATE))
        self.assertEqual(projected["status"], "complete")
        data_rows = [line for line in text.splitlines()[5:] if "｜" in line]
        self.assertEqual(len(data_rows), 5)
        self.assertEqual(sum("SYNTH.US｜买入｜2026-09-30｜`Call`｜$190.00" in line for line in data_rows), 1)
        self.assertEqual(sum("SYNTH.US｜卖出｜2026-09-30｜`Call`｜$190.00" in line for line in data_rows), 1)
        self.assertEqual(sum("｜`Put`｜" in line for line in data_rows), 1)
        self.assertEqual(sum("｜Call｜" in line for line in data_rows), 0)
        self.assertEqual(sum("｜Put｜" in line for line in data_rows), 0)
        self.assertIn("标的｜动作｜到期日｜Call / Put｜行权价｜工具｜对齐结果", text)
        self.assertNotIn("认购", text)
        self.assertNotIn("认沽", text)
        self.assertEqual(sum("$191.00" in line for line in data_rows), 1)
        self.assertNotIn("SYNTH260930C190000.US", text)

    def test_private_preview_uses_decimal_strike_and_zero_dte_classification(self) -> None:
        zero_day = PROJECT.project_execution(
            option_execution("SYNTH260831C00010000"), REVIEW_DATE
        )
        fractional = PROJECT.project_execution(
            option_execution("SYNTH260930P00001125.US"), REVIEW_DATE
        )
        self.assertEqual(zero_day.option.strike, Decimal("10"))
        self.assertEqual(zero_day.tool, "0DTE 期权")
        self.assertEqual(fractional.option.strike, Decimal("1.125"))
        self.assertEqual(fractional.tool, "其他期权")
        text = PROJECT._private_preview_text(
            PROJECT.parse_date(REVIEW_DATE),
            [(zero_day, "无法核对"), (fractional, "无法核对")],
        )
        self.assertIn("｜0DTE 期权｜无法核对", text)
        self.assertIn("｜`Call`｜", text)
        self.assertIn("｜`Put`｜", text)
        self.assertIn("$10.00", text)
        self.assertIn("$1.125", text)
        self.assertNotIn("Long Call", text)
        self.assertNotIn("SYNTH260831C00010000", text)

    def test_private_preview_validator_requires_english_inline_right_labels(self) -> None:
        fact = PROJECT.project_execution(option_execution("SYNTH260930C190000.US"), REVIEW_DATE)
        text = PROJECT._private_preview_text(PROJECT.parse_date(REVIEW_DATE), [(fact, "无法核对")])
        self.assertRaises(
            PROJECT.ProjectionError,
            PROJECT._assert_private_preview_text,
            text.replace("`Call`", "Call"),
            PROJECT.parse_date(REVIEW_DATE),
        )
        self.assertRaises(
            PROJECT.ProjectionError,
            PROJECT._assert_private_preview_text,
            text.replace("Call / Put", "认购/认沽"),
            PROJECT.parse_date(REVIEW_DATE),
        )

    def test_private_preview_is_opt_in_and_cli_writes_owner_only_markdown(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="daily-trade-journal-private-",
        ) as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            output = root / "facts.json"
            without_preview = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                output,
            )
            self.assertEqual(without_preview.returncode, 0)
            self.assertFalse((root / "preview.md").exists())
            public_before = output.read_text(encoding="utf-8")
            self.assertNotIn("260831C00010000", public_before)
            private = root / "preview.md"
            with_preview = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                output,
                private_preview=private,
            )
            self.assertEqual(with_preview.returncode, 0)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
            private_text = private.read_text(encoding="utf-8")
            self.assertIn("# 期权核对 · 2026-08-31（美东）", private_text)
            self.assertEqual(public_before, output.read_text(encoding="utf-8"))
            self.assertNotIn("260831C00010000", private_text)

    def test_owner_preview_reconciles_holdings_tools_plan_roles_and_unknown_triggers(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[
                {
                    "underlying": "META.US",
                    "actions": ["sell"],
                    "tool": "Call",
                    "status": "confirmed",
                    "plan_stage": "holding_management",
                }
            ],
            context_available=True,
            holding_underlyings=["META.US", "TSMX.US"],
            observation_underlyings=["AAPL.US", "SNOW.US"],
            ignored_underlyings=["MSTR.US"],
            context_underlyings=["AAPL.US", "META.US", "MSTR.US", "SNOW.US", "TSMX.US"],
            tool_by_underlying={
                "AAPL.US": "Call",
                "META.US": "Call",
                "MSTR.US": "Call",
                "SNOW.US": "Call",
                "TSMX.US": "single_stock_leveraged_etf",
            },
        )
        envelope = {"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version]}
        raw = [option_execution("META260930C00010000.US", side="sell")]
        payload, projected = PROJECT._project_facts(
            REVIEW_DATE,
            raw,
            trading_calendar=CALENDAR,
            plans=[envelope],
        )
        plans = PROJECT.parse_plans([envelope])
        positions = [
            {"symbol": "AAPL260930C00010000.US", "quantity": "private"},
            {"symbol": "META260930C00010000.US", "available_quantity": "private"},
            {"symbol": "MSTR260930C00010000.US", "cost_price": "private"},
            {"underlying": "NONE.US", "tool": "stock"},
            {"symbol": "SNOW260930C00010000.US"},
            {"symbol": "TSMX.US"},
        ]
        text = PROJECT._owner_preview_text(PROJECT.parse_date(REVIEW_DATE), projected, plans, positions)
        PROJECT._assert_owner_preview_text(text, PROJECT.parse_date(REVIEW_DATE))

        self.assertEqual(payload["executions"][0]["alignment"], "无法核对")
        self.assertIn("META.US｜Call｜持仓管理", text)
        self.assertIn("AAPL.US｜Call｜观察计划（当前已持仓）", text)
        self.assertIn("SNOW.US｜Call｜观察计划（当前已持仓）", text)
        self.assertIn("MSTR.US｜Call｜无具体计划", text)
        self.assertIn("NONE.US｜正股｜未提及", text)
        self.assertIn("TSMX.US｜单股杠杆 ETF｜持仓管理", text)
        self.assertIn("META.US｜卖出｜Call｜明确计划｜无法核对", text)
        for forbidden in (
            "META260930C00010000",
            "AAPL260930C00010000",
            "price",
            "quantity",
            "cost_price",
            "private",
        ):
            self.assertNotIn(forbidden, text)

    def test_owner_preview_cli_is_owner_only_and_requires_positions(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[],
            context_available=True,
            observation_underlyings=["AAPL.US"],
            context_underlyings=["AAPL.US"],
            tool_by_underlying={"AAPL.US": "Call"},
        )
        envelope = {"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version]}
        with tempfile.TemporaryDirectory(prefix="daily-trade-journal-owner-") as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            output = root / "facts.json"
            owner = root / "owner.md"
            completed = self.run_cli(
                root,
                [],
                output,
                raw_positions=[{"symbol": "AAPL260930C00010000.US", "quantity": "private"}],
                owner_preview=owner,
                confirmed_plans=envelope,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(stat.S_IMODE(owner.stat().st_mode), 0o600)
            owner_text = owner.read_text(encoding="utf-8")
            self.assertIn("AAPL.US｜Call｜观察计划（当前已持仓）", owner_text)
            self.assertNotIn("AAPL260930C00010000", owner_text)
            self.assertNotIn("private", owner_text)

            orphan = root / "orphan.md"
            failed = self.run_cli(root, [], output, owner_preview=orphan)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(orphan.exists())

    def test_malformed_option_components_fail_without_private_preview_or_raw_symbol(self) -> None:
        malformed = (
            "SYNTH260231C190000.US",
            "SYNTH260831X190000.US",
            "SYNTH260831C19.000.US",
        )
        for symbol in malformed:
            with self.subTest(symbol=symbol), tempfile.TemporaryDirectory(
                prefix="daily-trade-journal-malformed-",
            ) as directory:
                root = Path(directory).resolve()
                os.chmod(root, 0o700)
                output = root / "facts.json"
                private = root / "preview.md"
                completed = self.run_cli(root, [option_execution(symbol)], output, private_preview=private)
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(private.exists())
                blocked = output.read_text(encoding="utf-8")
                self.assertIn('"status": "blocked"', blocked)
                self.assertNotIn(symbol, blocked)

    def test_private_preview_path_rejects_relative_symlink_unsafe_and_colliding_targets(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="daily-trade-journal-private-path-",
        ) as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            output = root / "facts.json"
            raw = [option_execution("SYNTH260831C00010000")]
            relative = self.run_cli(root, raw, output, private_preview=Path("preview.md"))
            self.assertNotEqual(relative.returncode, 0)
            self.assertFalse((root / "preview.md").exists())

            secure = root / "secure"
            secure.mkdir()
            os.chmod(secure, 0o700)
            alias = root / "alias"
            os.symlink(secure, alias)
            symlinked = self.run_cli(root, raw, output, private_preview=alias / "preview.md")
            self.assertNotEqual(symlinked.returncode, 0)
            self.assertFalse((secure / "preview.md").exists())

            unsafe = root / "unsafe"
            unsafe.mkdir()
            os.chmod(unsafe, 0o755)
            unsafe_result = self.run_cli(root, raw, output, private_preview=unsafe / "preview.md")
            self.assertNotEqual(unsafe_result.returncode, 0)
            self.assertFalse((unsafe / "preview.md").exists())

            collision = self.run_cli(root, raw, output, private_preview=output)
            self.assertNotEqual(collision.returncode, 0)
            self.assertFalse(output.exists())

            with tempfile.TemporaryDirectory(
                prefix="daily-trade-journal-private-outside-",
            ) as outside_directory:
                outside_preview = Path(outside_directory).resolve() / "preview.md"
                outside = self.run_cli(root, raw, output, private_preview=outside_preview)
                self.assertNotEqual(outside.returncode, 0)
                self.assertFalse(outside_preview.exists())

    def test_private_preview_existing_unsafe_file_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="daily-trade-journal-private-existing-",
        ) as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            private = root / "preview.md"
            private.write_text("sentinel", encoding="utf-8")
            os.chmod(private, 0o644)
            completed = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                root / "facts.json",
                private_preview=private,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(private.read_text(encoding="utf-8"), "sentinel")
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o644)

    def test_preflight_collisions_and_invalid_private_path_never_overwrite_targets(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="daily-trade-journal-preflight-",
        ) as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)

            raw_output = root / "raw.json"
            raw_collision = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                raw_output,
            )
            self.assertNotEqual(raw_collision.returncode, 0)
            self.assertNotIn('"status": "blocked"', raw_output.read_text(encoding="utf-8"))
            self.assertIn("SYNTH260831C00010000", raw_output.read_text(encoding="utf-8"))

            calendar_output = root / "calendar.json"
            calendar_collision = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                calendar_output,
            )
            self.assertNotEqual(calendar_collision.returncode, 0)
            calendar_text = calendar_output.read_text(encoding="utf-8")
            self.assertNotIn('"status": "blocked"', calendar_text)
            self.assertIn(REVIEW_DATE, calendar_text)

            existing_output = root / "existing.json"
            existing_output.write_text("existing-output", encoding="utf-8")
            os.chmod(existing_output, 0o600)
            invalid_private = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                existing_output,
                private_preview=Path("relative-preview.md"),
            )
            self.assertNotEqual(invalid_private.returncode, 0)
            self.assertEqual(existing_output.read_text(encoding="utf-8"), "existing-output")

            private_collision_output = root / "facts.md"
            private_collision = self.run_cli(
                root,
                [option_execution("SYNTH260831C00010000")],
                private_collision_output,
                private_preview=private_collision_output,
            )
            self.assertNotEqual(private_collision.returncode, 0)
            self.assertFalse(private_collision_output.exists())

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

    def test_confirmed_call_and_put_plans_align_by_internal_option_right(self) -> None:
        call_execution = [execution("SYNTH.US260930C00010000")]
        call_result = PROJECT.project_facts(
            REVIEW_DATE,
            call_execution,
            trading_calendar=CALENDAR,
            plans=[plan(tool="Long Call")],
        )
        self.assertEqual(call_result["executions"][0]["alignment"], "按计划")
        call_fact = PROJECT.parse_plans([plan(tool="Call")]).plans[0]
        self.assertEqual(call_fact.tool, "其他期权")
        self.assertEqual(call_fact.option_right, "Call")

        put_result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260930P00010000")],
            trading_calendar=CALENDAR,
            plans=[plan(tool="Call")],
        )
        self.assertEqual(put_result["executions"][0]["alignment"], "偏离计划")

        put_plan_result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260930P00010000")],
            trading_calendar=CALENDAR,
            plans=[plan(tool="Long Put")],
        )
        self.assertEqual(put_plan_result["executions"][0]["alignment"], "按计划")
        reverse_result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260930C00010000")],
            trading_calendar=CALENDAR,
            plans=[plan(tool="Put")],
        )
        self.assertEqual(reverse_result["executions"][0]["alignment"], "偏离计划")

    def test_intraday_revision_call_plan_is_used_without_public_right_or_identity(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            {
                "executions": [execution("SYNTH.US260831C00010000")],
                "intraday_revisions": [plan(tool="Call")],
            },
            trading_calendar=CALENDAR,
        )
        self.assertEqual(result["executions"][0]["alignment"], "按计划")
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("Call", encoded)
        self.assertNotIn("260831C00010000", encoded)

    def test_generic_other_option_keeps_category_only_matching(self) -> None:
        other_option = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260930C00010000")],
            trading_calendar=CALENDAR,
            plans=[plan(tool="other_option")],
        )
        self.assertEqual(other_option["executions"][0]["alignment"], "按计划")
        zero_dte = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US260831C00010000")],
            trading_calendar=CALENDAR,
            plans=[plan(tool="other_option")],
        )
        self.assertEqual(zero_dte["executions"][0]["alignment"], "偏离计划")

    def test_other_option_and_unknown_tool_are_safe(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [
                execution("SYNTH.US260930P00010000"),
                execution("UNKNOWN.US"),
            ],
            trading_calendar=CALENDAR,
        )
        self.assertEqual(
            [row.get("tool") for row in result["executions"] if "tool" in row],
            ["其他期权"],
        )
        self.assertTrue(all(row["alignment"] == "无法核对" for row in result["executions"]))
        self.assertTrue(
            any(set(row) == {"underlying", "action", "alignment"} for row in result["executions"])
        )

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

    def test_invalid_plan_time_fails_closed_without_alignment(self) -> None:
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(
                REVIEW_DATE,
                [execution("SYNTH.US260930C00010000")],
                trading_calendar=CALENDAR,
                plans=[plan(tool="Call", confirmed_at="not-a-timestamp")],
            )

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

    def test_native_longbridge_calendar_shape_is_accepted(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [],
            trading_calendar={"trading_days": ["2026-08-30", REVIEW_DATE], "half_trading_days": []},
        )
        self.assertEqual(result["status"], "empty")

    def test_native_calendar_open_entry_requires_completed_true(self) -> None:
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(
                REVIEW_DATE,
                [],
                trading_calendar={"trading_days": [{"date": REVIEW_DATE, "status": "open"}]},
            )

    def test_confirmed_version_row_inherits_envelope_confirmation(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[{"underlying": "SYNTH.US", "action": "buy", "tool": "stock"}],
        )
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version]}],
        )
        self.assertEqual(result["executions"][0]["alignment"], "按计划")

    def test_confirmed_context_without_exact_plan_emits_only_context_note(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[],
            context_available=True,
        )
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version]}],
        )
        self.assertEqual(result["context_note"], PROJECT.CONTEXT_NOTE)
        self.assertEqual(
            set(result),
            {"schema_version", "review_date", "status", "executions", "context_note"},
        )
        self.assertEqual(set(result["executions"][0]), {"underlying", "action", "alignment"})
        self.assertNotIn("context_available", json.dumps(result, ensure_ascii=False))

    def test_context_note_is_not_emitted_when_exact_plan_rows_exist(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[{"underlying": "SYNTH.US", "action": "buy", "tool": "stock"}],
            context_available=True,
        )
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version]}],
        )
        self.assertNotIn("context_note", result)
        self.assertEqual(result["executions"][0]["alignment"], "按计划")

    def test_context_signal_must_be_boolean(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[],
            context_available=True,
        )
        version["context_available"] = "yes"
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(
                REVIEW_DATE,
                [],
                trading_calendar=CALENDAR,
                plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version]}],
            )

    def test_ordinary_tools_are_hidden_but_alignment_is_conservative_when_collapsed(self) -> None:
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [
                execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"}),
                execution("SYNTH.US", instrument={"tool_kind": "single_stock_leveraged_etf", "underlying": "SYNTH.US"}),
            ],
            trading_calendar=CALENDAR,
            plans=[plan(tool="stock")],
        )
        self.assertEqual(
            result["executions"],
            [{"underlying": "SYNTH.US", "action": "买入", "alignment": "无法核对"}],
        )

    def test_latest_confirmed_version_is_global_and_temporal(self) -> None:
        older = self.version(
            version="2026-08-30-090000",
            confirmed_at="2026-08-30T09:00:00-04:00",
            plans=[{"underlying": "SYNTH.US", "action": "buy", "tool": "stock", "status": "confirmed"}],
        )
        newer_without_underlying = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[{"underlying": "OTHER.US", "action": "buy", "tool": "stock", "status": "confirmed"}],
        )
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [older, newer_without_underlying]}],
        )
        self.assertEqual(result["executions"][0]["alignment"], "无法核对")

        after_execution = self.version(
            version="2026-08-31-160000",
            confirmed_at="2026-08-31T16:00:00-04:00",
            plans=[{"underlying": "SYNTH.US", "action": "buy", "tool": "stock", "status": "confirmed"}],
        )
        result = PROJECT.project_facts(
            REVIEW_DATE,
            [execution("SYNTH.US", instrument={"tool_kind": "stock", "underlying": "SYNTH.US"})],
            trading_calendar=CALENDAR,
            plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [older, after_execution]}],
        )
        self.assertEqual(result["executions"][0]["alignment"], "按计划")

    def test_confirmed_plan_version_duplicates_fail_closed(self) -> None:
        version = self.version(
            version="2026-08-31-090000",
            confirmed_at="2026-08-31T09:00:00-04:00",
            plans=[],
        )
        with self.assertRaises(PROJECT.ProjectionError):
            PROJECT.project_facts(
                REVIEW_DATE,
                [],
                trading_calendar=CALENDAR,
                plans=[{"schema_version": PROJECT.PLAN_INPUT_SCHEMA_VERSION, "versions": [version, version]}],
            )

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
