from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "trading-center-review" / "scripts" / "trading_review_state.py"
SPEC = importlib.util.spec_from_file_location("trading_review_state", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load trading_review_state")
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)


def trade_rows(quantity: str = "2") -> list[dict[str, object]]:
    return [
        {
            "market_date": "2026-08-28",
            "symbol": "DEMO.US",
            "side": "buy",
            "order_count": 1,
            "execution_count": 1,
            "executed_quantity": quantity,
            "data_status": "complete",
        }
    ]


def weekly_bundle(dependency: dict[str, object], summary: str = "计划权威缺失") -> dict[str, object]:
    modules = []
    for name in sorted(STATE.WEEKLY_MODULES):
        status = "blocked" if name == "plan" else "complete"
        modules.append(
            {
                "name": name,
                "status": status,
                "requested_start": None,
                "requested_end": None,
                "returned_start": None,
                "returned_end": None,
                "error_category": "confirmed_plan_authority_missing" if name == "plan" else None,
            }
        )
    return {
        "schema_version": "trading-review-weekly-state.v1",
        "run_id": "weekly-run",
        "review_key": "weekly:2026-08-24:2026-08-28",
        "period_start": "2026-08-24",
        "period_end": "2026-08-28",
        "generated_at": "2026-08-30T08:00:00+08:00",
        "source_contract_version": "source.v1",
        "data_status": "partial",
        "plan_hash": None,
        "dependencies": [dependency],
        "modules": modules,
        "performance": {
            "currency": "USD",
            "initial_asset_value": "1000",
            "ending_asset_value": "980",
            "profit": "-20",
            "profit_rate": "-2",
            "time_weighted_return": "-2",
            "invest_amount": "0",
            "mechanical_asset_change": "-20",
            "reconciliation_residual": "0",
            "requested_utc_start": "2026-08-24T04:00:00Z",
            "requested_utc_end": "2026-08-29T04:00:00Z",
            "returned_utc_start": "2026-08-24T04:00:00Z",
            "returned_utc_end": "2026-08-29T04:00:00Z",
            "data_status": "complete",
        },
        "attributions": [
            {
                "underlying": "DEMO.US",
                "instrument_group": "combined",
                "display_name": "示例标的",
                "profit": "-20",
                "underlying_profit": "-15",
                "derivatives_profit": "-5",
                "currency": "USD",
                "data_status": "complete",
            }
        ],
        "cash_flow_aggregates": [
            {
                "category": "stock_buy",
                "currency": "USD",
                "amount": "-100",
                "row_count": 1,
                "data_status": "complete",
            }
        ],
        "review_items": [
            {
                "item_kind": "gap",
                "subject": "计划复核｜计划与实际",
                "summary": summary,
                "evidence_boundary": "不从持仓或成交反推计划",
                "evidence_kind": "gap",
                "data_status": "blocked",
            }
        ],
    }


def plan_state(
    *,
    plan_id: str = "plan-demo",
    version: int = 1,
    status: str = "confirmed",
    stage: str = "pre_entry",
    setup: str = "pullback",
    content_digest: str = "a" * 64,
    supersedes_version: Optional[int] = None,
    parent_plan_id: Optional[str] = None,
    parent_plan_version: Optional[int] = None,
    initial_buy_episode_key: Optional[str] = None,
) -> dict[str, object]:
    confirmed = status == "confirmed"
    zone_kind = "add" if stage == "position_management" else "entry"
    return {
        "schema_version": "trading-plan-state.v1",
        "plan_id": plan_id,
        "version": version,
        "plan_stage": stage,
        "underlying": "DEMO.US",
        "direction": "long",
        "setup_type": setup,
        "plan_status": status,
        "generated_at": "2026-08-19T18:00:00-04:00",
        "effective_at": "2026-08-20T08:00:00-04:00" if confirmed else None,
        "confirmed_at": "2026-08-20T07:55:00-04:00" if confirmed else None,
        "expires_at": "2026-09-30T16:00:00-04:00",
        "evidence": {
            "evidence_id": "b" * 64,
            "source": "Longbridge",
            "as_of": "2026-08-19",
            "timezone": "America/New_York",
            "adjustment": "forward",
            "bars_used": 319,
            "atr14": "3.5",
        },
        "constraints": {
            "minimum_reward_risk": "2",
            "max_invalidation_pct": "5",
        },
        "content_hash": content_digest,
        "supersedes_version": supersedes_version,
        "parent_plan_id": parent_plan_id,
        "parent_plan_version": parent_plan_version,
        "initial_buy_episode_key": initial_buy_episode_key,
        "data_status": "complete",
        "zones": [
            {
                "kind": "observation",
                "low": "95",
                "high": "97",
                "currency": "USD",
                "condition": "只开始观察",
                "derived_from": "ema20_atr14",
                "data_status": "complete",
            },
            {
                "kind": zone_kind,
                "low": "98",
                "high": "100",
                "currency": "USD",
                "condition": "条件确认后执行",
                "derived_from": "ema20_swing",
                "data_status": "complete",
            },
            {
                "kind": "reduce",
                "low": "111",
                "high": "112",
                "currency": "USD",
                "condition": "到达目标区评估减仓",
                "derived_from": "swing_high",
                "data_status": "complete",
            },
            {
                "kind": "invalidation",
                "low": "95",
                "high": "96",
                "currency": "USD",
                "condition": "已完成日线确认失效",
                "derived_from": "support_minus_atr",
                "data_status": "complete",
            },
        ],
    }


def weekly_v2_bundle(
    dependency: dict[str, object],
    *,
    blocked: bool = False,
) -> dict[str, object]:
    modules = []
    for name in sorted(STATE.WEEKLY_MODULES):
        if name in {"performance", "attribution", "cash_flow"}:
            status = "empty"
            error = None
        elif blocked and name == "plan":
            status = "blocked"
            error = "confirmed_plan_authority_missing"
        else:
            status = "complete"
            error = None
        modules.append(
            {
                "name": name,
                "status": status,
                "requested_start": None,
                "requested_end": None,
                "returned_start": None,
                "returned_end": None,
                "error_category": error,
            }
        )
    assessments = [] if blocked else [
        {
            "market_date": "2026-08-26",
            "underlying": "DEMO.US",
            "side": "buy",
            "plan_id": "plan-demo",
            "plan_version": 2,
            "coverage_status": "covered",
            "compliance_status": "compliant",
            "outcome_status": "success",
            "deviation_type": None,
            "reason": "按已确认进场区执行",
            "next_rule": "保留规则",
            "data_status": "complete",
        },
        {
            "market_date": "2026-08-27",
            "underlying": "DEMO.US",
            "side": "buy",
            "plan_id": "plan-demo",
            "plan_version": 2,
            "coverage_status": "covered",
            "compliance_status": "non_compliant",
            "outcome_status": "success",
            "deviation_type": "entry_outside_zone",
            "reason": "盈利但未按计划执行",
            "next_rule": "计划外交易一律复盘",
            "data_status": "complete",
        },
        {
            "market_date": "2026-08-28",
            "underlying": "DEMO.US",
            "side": "sell",
            "plan_id": "plan-demo",
            "plan_version": 2,
            "coverage_status": "covered",
            "compliance_status": "compliant",
            "outcome_status": "failure",
            "deviation_type": None,
            "reason": "按计划止损，设定失败",
            "next_rule": "检查设定质量",
            "data_status": "complete",
        },
    ]
    return {
        "schema_version": "trading-review-weekly-state.v2",
        "run_id": "weekly-run",
        "review_key": "weekly:2026-08-24:2026-08-28",
        "period_start": "2026-08-24",
        "period_end": "2026-08-28",
        "generated_at": "2026-08-30T08:00:00+08:00",
        "source_contract_version": "source.v1",
        "data_status": "partial" if blocked else "complete",
        "plan_hash": None if blocked else "a" * 64,
        "dependencies": [dependency],
        "modules": modules,
        "performance": None,
        "attributions": [],
        "cash_flow_aggregates": [],
        "review_items": [
            {
                "item_kind": "gap" if blocked else "discipline",
                "subject": "计划执行",
                "summary": "无可核验事前计划" if blocked else "有一笔计划外执行",
                "evidence_boundary": "只使用事前 confirmed 计划和成交事实",
                "evidence_kind": "gap" if blocked else "fact",
                "data_status": "blocked" if blocked else "complete",
            }
        ],
        "episode_assessments": assessments,
        "execution_metrics": {
            "data_status": "blocked" if blocked else "complete",
            "gap": "confirmed_plan_or_execution_evidence_missing" if blocked else None,
        },
    }


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="trading-state-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.state_dir = self.root / "state"
        self.db_path = self.state_dir / "review.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def open_store(self, *, busy_timeout_ms: int = 200):
        return STATE.open_state_store(
            self.db_path,
            test_root=self.root,
            busy_timeout_ms=busy_timeout_ms,
        )

    def test_schema_and_owner_only_permissions(self) -> None:
        with self.open_store() as store:
            version = store.connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual(version, 3)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o600)
        self.assertTrue(
            {
                "schema_meta",
                "runs",
                "partitions",
                "account_snapshots",
                "position_snapshots",
                "trade_aggregates",
                "market_snapshots",
                "relevant_events",
                "analysis_snapshots",
                "confirmations",
                "weekly_reviews",
                "weekly_review_dependencies",
                "weekly_module_statuses",
                "weekly_performance",
                "weekly_attributions",
                "weekly_cash_flow_aggregates",
                "weekly_review_items",
                "plan_versions",
                "plan_zones",
                "trade_episode_assessments",
                "weekly_execution_metrics",
            }.issubset(tables)
        )

    def test_schema_has_no_generic_raw_or_metadata_escape_column(self) -> None:
        with self.open_store() as store:
            columns = {
                row[1].lower()
                for table in STATE.SCHEMA_TABLES
                for row in store.connection.execute(f"PRAGMA table_info({table})")
            }
        self.assertFalse({"raw_json", "raw_payload", "metadata_json", "payload_json"} & columns)

    def test_current_schema_tampering_fails_closed_on_open(self) -> None:
        with self.open_store():
            pass
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("ALTER TABLE partitions ADD COLUMN raw_json TEXT")
        self.db_path.chmod(0o600)
        with self.assertRaises(STATE.StateMigrationError):
            self.open_store()

    def test_rejects_unsafe_locations_symlinks_and_broad_permissions(self) -> None:
        with self.assertRaises(STATE.UnsafeStatePathError):
            STATE.validate_state_db_path(Path("relative.sqlite3"))
        with self.assertRaises(STATE.UnsafeStatePathError):
            STATE.validate_state_db_path(Path("/private/tmp/forbidden.sqlite3"))
        with self.assertRaises(STATE.UnsafeStatePathError):
            STATE.validate_state_db_path(ROOT / "forbidden.sqlite3")

        target = self.root / "target"
        target.mkdir(mode=0o700)
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(STATE.UnsafeStatePathError):
            STATE.validate_state_db_path(link / "review.sqlite3", test_root=self.root)

        self.state_dir.mkdir(mode=0o700)
        self.db_path.write_bytes(b"")
        self.db_path.chmod(0o644)
        with self.assertRaises(STATE.UnsafeStatePathError):
            STATE.open_state_store(self.db_path, test_root=self.root)

    def test_complete_and_empty_are_cache_hits_while_non_success_retries(self) -> None:
        with self.open_store() as store:
            written = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows(),
            )
            reused = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-29T08:01:00+08:00",
                payload=trade_rows(),
            )
            self.assertEqual(written.action, "written")
            self.assertEqual(reused.action, "reused")
            self.assertEqual(store.partition_decision("trades", "2026-08-28", "2026-08-28", "trades.v1"), "cache_hit")
            snapshot = store.get_trade_partition_snapshot(
                "2026-08-28", "2026-08-28", "trades.v1"
            )
            self.assertEqual(snapshot["payload"], trade_rows())
            self.assertEqual(snapshot["status"], "complete")
            self.assertEqual(store.table_count("trade_aggregates"), 1)

            store.ingest_partition(
                dataset="trades",
                period_start="2026-08-27",
                period_end="2026-08-27",
                contract_version="trades.v1",
                status="empty",
                collected_at="2026-08-28T08:00:00+08:00",
                payload=[],
            )
            self.assertEqual(store.partition_decision("trades", "2026-08-27", "2026-08-27", "trades.v1"), "cache_hit")

            for status in ("partial", "stale", "blocked"):
                date = {"partial": "2026-08-26", "stale": "2026-08-25", "blocked": "2026-08-24"}[status]
                store.ingest_partition(
                    dataset="trades",
                    period_start=date,
                    period_end=date,
                    contract_version="trades.v1",
                    status=status,
                    collected_at="2026-08-29T08:00:00+08:00",
                    payload=[],
                    error_category=f"{status}_fixture",
                )
                self.assertEqual(store.partition_decision("trades", date, date, "trades.v1"), "retry")

    def test_cached_partition_hash_mismatch_fails_closed(self) -> None:
        with self.open_store() as store:
            store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows(),
            )
            store.connection.execute(
                "UPDATE trade_aggregates SET executed_quantity='999' WHERE market_date='2026-08-28'"
            )
            with self.assertRaises(STATE.StateContractError):
                store.get_trade_partition_snapshot(
                    "2026-08-28", "2026-08-28", "trades.v1"
                )

    def test_changed_payload_creates_revision_without_overwriting_old_rows(self) -> None:
        with self.open_store() as store:
            first = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows("2"),
            )
            second = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-29T08:05:00+08:00",
                payload=trade_rows("3"),
            )
            revisions = store.connection.execute(
                "SELECT revision, supersedes_revision FROM partitions ORDER BY revision"
            ).fetchall()
            quantities = store.connection.execute(
                "SELECT revision, executed_quantity FROM trade_aggregates ORDER BY revision"
            ).fetchall()
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertEqual([tuple(row) for row in revisions], [(1, None), (2, 1)])
        self.assertEqual([tuple(row) for row in quantities], [(1, "2"), (2, "3")])

    def test_fact_revisions_do_not_collide_across_contracts_or_review_dates(self) -> None:
        event = {
            "derived_event_key": "synthetic-event-key",
            "et_at": "2026-08-28T08:30:00-04:00",
            "shanghai_at": "2026-08-28T20:30:00+08:00",
            "title": "合成事件",
            "status": "已发生",
            "source_category": "calendar",
            "impact_channel": "组合波动",
            "data_status": "complete",
        }
        with self.open_store() as store:
            first_trade = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows(),
            )
            second_trade = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v2",
                status="complete",
                collected_at="2026-08-29T08:01:00+08:00",
                payload=trade_rows(),
            )
            first_event = store.ingest_partition(
                dataset="relevant_events",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="events.v1",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=[event],
            )
            second_event = store.ingest_partition(
                dataset="relevant_events",
                period_start="2026-08-29",
                period_end="2026-08-29",
                contract_version="events.v1",
                status="complete",
                collected_at="2026-08-29T08:01:00+08:00",
                payload=[event],
            )
            self.assertEqual(store.table_count("trade_aggregates"), 2)
            self.assertEqual(store.table_count("relevant_events"), 2)
        self.assertEqual((first_trade.revision, second_trade.revision), (1, 2))
        self.assertEqual((first_event.revision, second_event.revision), (1, 2))

    def test_success_empty_and_factual_child_statuses_fail_closed(self) -> None:
        with self.open_store() as store:
            with self.assertRaises(STATE.StateContractError):
                store.ingest_partition(
                    dataset="trades",
                    period_start="2026-08-28",
                    period_end="2026-08-28",
                    contract_version="trades.v1",
                    status="complete",
                    collected_at="2026-08-29T08:00:00+08:00",
                    payload=[],
                )
            blocked_row = trade_rows()
            blocked_row[0]["data_status"] = "blocked"
            with self.assertRaises(STATE.StateContractError):
                store.ingest_partition(
                    dataset="trades",
                    period_start="2026-08-28",
                    period_end="2026-08-28",
                    contract_version="trades.v1",
                    status="partial",
                    collected_at="2026-08-29T08:00:00+08:00",
                    payload=blocked_row,
                    error_category="synthetic_gap",
                )
            self.assertEqual(store.table_count("partitions"), 0)

    def test_duplicate_natural_keys_and_wrong_trade_dates_fail_before_persistence(self) -> None:
        duplicate = trade_rows() * 2
        wrong_date = [{**trade_rows()[0], "market_date": "2026-08-27"}]
        with self.open_store() as store:
            for payload in (duplicate, wrong_date):
                with self.subTest(payload=payload), self.assertRaises(STATE.StateContractError):
                    store.ingest_partition(
                        dataset="trades",
                        period_start="2026-08-28",
                        period_end="2026-08-28",
                        contract_version="trades.v1",
                        status="complete",
                        collected_at="2026-08-29T08:00:00+08:00",
                        payload=payload,
                    )
            self.assertEqual(store.table_count("partitions"), 0)
            self.assertEqual(store.table_count("trade_aggregates"), 0)

    def test_event_dual_timezones_must_identify_one_instant(self) -> None:
        mismatched = {
            "derived_event_key": "synthetic-event-key",
            "et_at": "2026-08-28T08:30:00-04:00",
            "shanghai_at": "2026-08-28T21:30:00+08:00",
            "title": "合成事件",
            "status": "预期",
            "source_category": "calendar",
            "impact_channel": "组合波动",
            "data_status": "complete",
        }
        with self.open_store() as store:
            with self.assertRaises(STATE.StateContractError):
                store.ingest_partition(
                    dataset="relevant_events",
                    period_start="2026-08-28",
                    period_end="2026-08-28",
                    contract_version="events.v1",
                    status="complete",
                    collected_at="2026-08-29T08:00:00+08:00",
                    payload=[mismatched],
                )
            self.assertEqual(store.table_count("partitions"), 0)

    def test_unknown_or_sensitive_fields_fail_before_persistence(self) -> None:
        unknown = trade_rows()
        unknown[0]["unexpected"] = "x"
        sensitive = trade_rows()
        sensitive[0]["order_id"] = "synthetic"
        with self.open_store() as store:
            for payload in (unknown, sensitive):
                with self.subTest(payload=payload), self.assertRaises(STATE.StateContractError):
                    store.ingest_partition(
                        dataset="trades",
                        period_start="2026-08-28",
                        period_end="2026-08-28",
                        contract_version="trades.v1",
                        status="complete",
                        collected_at="2026-08-29T08:00:00+08:00",
                        payload=payload,
                    )
            self.assertEqual(store.table_count("partitions"), 0)
            self.assertEqual(store.table_count("trade_aggregates"), 0)

    def test_analysis_cache_requires_all_three_hash_dimensions(self) -> None:
        output = {
            "headline": "条件式判断",
            "facts": ["已验证事实"],
            "interpretation": ["事实解释"],
            "risks": ["主要风险"],
            "checks": [
                {
                    "if": "条件满足",
                    "then": "核对已确认计划",
                    "else": "保持不行动",
                    "evidence_refs": ["facts:1"],
                    "boundary": "仅限当前数据窗口",
                }
            ],
            "gaps": [],
        }
        with self.open_store() as store:
            store.put_analysis("facts-a", "plan-a", "analysis.v1", output, "codex", "2026-08-29T08:00:00+08:00", "complete")
            self.assertIsNotNone(store.get_analysis("facts-a", "plan-a", "analysis.v1"))
            self.assertIsNone(store.get_analysis("facts-b", "plan-a", "analysis.v1"))
            self.assertIsNone(store.get_analysis("facts-a", "plan-b", "analysis.v1"))
            self.assertIsNone(store.get_analysis("facts-a", "plan-a", "analysis.v2"))

    def test_analysis_cache_reuse_requires_the_original_snapshot_metadata(self) -> None:
        output = {
            "headline": "条件式判断",
            "facts": ["已验证事实"],
            "interpretation": ["事实解释"],
            "risks": ["主要风险"],
            "checks": [
                {
                    "if": "条件满足",
                    "then": "核对已确认计划",
                    "else": "保持不行动",
                    "evidence_refs": ["facts:1"],
                    "boundary": "仅限当前数据窗口",
                }
            ],
            "gaps": [],
        }
        with self.open_store() as store:
            store.put_analysis(
                "facts-a",
                "plan-a",
                "analysis.v1",
                output,
                "codex",
                "2026-08-29T08:00:00+08:00",
                "complete",
            )
            with self.assertRaises(STATE.StateContractError):
                store.put_analysis(
                    "facts-a",
                    "plan-a",
                    "analysis.v1",
                    output,
                    "codex",
                    "2026-08-29T08:01:00+08:00",
                    "complete",
                )

    def test_confirmation_is_independent_from_data_status(self) -> None:
        with self.open_store() as store:
            store.start_run(
                run_id="run-partial",
                mode="daily",
                period_start="2026-08-28",
                period_end="2026-08-28",
                started_at="2026-08-29T08:00:00+08:00",
                data_status="partial",
                source_contract_version="source.v1",
            )
            store.confirm("daily:2026-08-28", 1, "confirmed", "2026-08-29T09:00:00+08:00", "facts-partial")
            row = store.connection.execute(
                "SELECT confirmation_status, facts_hash FROM confirmations"
            ).fetchone()
            run = store.connection.execute(
                "SELECT data_status, confirmation_status FROM runs WHERE run_id='run-partial'"
            ).fetchone()
        self.assertEqual(tuple(row), ("confirmed", "facts-partial"))
        self.assertEqual(tuple(run), ("partial", "pending"))

    def test_confirmation_timestamp_and_supersedes_semantics_fail_closed(self) -> None:
        with self.open_store() as store:
            with self.assertRaises(STATE.StateContractError):
                store.confirm("daily:2026-08-28", 1, "confirmed", None, "facts-a")
            with self.assertRaises(STATE.StateContractError):
                store.confirm(
                    "daily:2026-08-28",
                    1,
                    "pending",
                    "2026-08-29T09:00:00+08:00",
                    "facts-a",
                )
            with self.assertRaises(STATE.StateContractError):
                store.confirm(
                    "daily:2026-08-28",
                    2,
                    "confirmed",
                    "2026-08-29T09:00:00+08:00",
                    "facts-b",
                    supersedes_version=2,
                )
            self.assertEqual(store.table_count("confirmations"), 0)

    def test_weekly_aggregate_requires_every_expected_trade_date(self) -> None:
        with self.open_store() as store:
            store.ingest_partition(
                dataset="trades",
                period_start="2026-08-27",
                period_end="2026-08-27",
                contract_version="trades.v1",
                status="complete",
                collected_at="2026-08-28T08:00:00+08:00",
                payload=[{**trade_rows("2")[0], "market_date": "2026-08-27"}],
            )
            partial = store.aggregate_weekly_trades(
                ["2026-08-27", "2026-08-28"], "trades.v1"
            )
            store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="trades.v1",
                status="empty",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=[],
            )
            complete = store.aggregate_weekly_trades(
                ["2026-08-27", "2026-08-28"], "trades.v1"
            )
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["missing_dates"], ["2026-08-28"])
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["rows"][0]["executed_quantity"], "2")

    def test_write_lock_times_out_and_fails_closed(self) -> None:
        with self.open_store(busy_timeout_ms=50) as first, self.open_store(busy_timeout_ms=50) as second:
            first.connection.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaises(STATE.StateBusyError):
                    second.ingest_partition(
                        dataset="trades",
                        period_start="2026-08-28",
                        period_end="2026-08-28",
                        contract_version="trades.v1",
                        status="complete",
                        collected_at="2026-08-29T08:00:00+08:00",
                        payload=trade_rows(),
                    )
            finally:
                first.connection.rollback()
        with sqlite3.connect(self.db_path) as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM partitions").fetchone()[0], 0)

    def test_migration_failure_rolls_back_and_keeps_owner_only_backup(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
            connection.execute("INSERT INTO sentinel VALUES ('before')")
        self.db_path.chmod(0o600)

        with mock.patch.object(STATE, "_apply_migration_v1", side_effect=RuntimeError("synthetic migration failure")):
            with self.assertRaises(STATE.StateMigrationError):
                self.open_store()

        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT value FROM sentinel").fetchone()[0], "before")
        backups = list(self.state_dir.glob("review.sqlite3.backup-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    def test_partial_ddl_failure_does_not_leave_half_migrated_schema(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
            connection.execute("INSERT INTO sentinel VALUES ('before')")
        self.db_path.chmod(0o600)
        broken_schema = "CREATE TABLE should_rollback(value TEXT NOT NULL); THIS IS NOT SQL;"
        with mock.patch.object(STATE, "SCHEMA_V1_SQL", broken_schema):
            with self.assertRaises(STATE.StateMigrationError):
                self.open_store()
        with sqlite3.connect(self.db_path) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("sentinel", names)
            self.assertNotIn("should_rollback", names)

    def test_v1_to_v3_migration_preserves_existing_rows_and_creates_owner_only_backup(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as connection:
            for statement in STATE.SCHEMA_V1_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta VALUES (1, 1, '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z')"
            )
            connection.execute(
                "INSERT INTO runs VALUES ('legacy-run','weekly','2026-08-24','2026-08-28','2026-08-29T00:00:00Z',NULL,'partial','pending','source.v1')"
            )
            connection.execute("PRAGMA user_version=1")
        self.db_path.chmod(0o600)
        with self.open_store() as store:
            self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(store.table_count("runs"), 1)
            self.assertEqual(store.table_count("weekly_reviews"), 0)
            self.assertEqual(store.table_count("plan_versions"), 0)
            self.assertEqual(store.table_count("weekly_execution_metrics"), 0)
            self.assertEqual(store.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        backups = list(self.state_dir.glob("review.sqlite3.backup-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(backup.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_v2_migration_failure_rolls_back_to_intact_v1_database(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as connection:
            for statement in STATE.SCHEMA_V1_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta VALUES (1, 1, '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z')"
            )
            connection.execute(
                "INSERT INTO runs VALUES ('legacy-run','weekly','2026-08-24','2026-08-28','2026-08-29T00:00:00Z',NULL,'partial','pending','source.v1')"
            )
            connection.execute("PRAGMA user_version=1")
        self.db_path.chmod(0o600)
        broken = "CREATE TABLE should_rollback(value TEXT NOT NULL); THIS IS NOT SQL;"
        with mock.patch.object(STATE, "SCHEMA_V2_SQL", broken):
            with self.assertRaises(STATE.StateMigrationError):
                self.open_store()
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("should_rollback", names)
            self.assertNotIn("weekly_reviews", names)

    def test_v2_to_v3_migration_preserves_weekly_rows_and_backup(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as connection:
            for schema in (STATE.SCHEMA_V1_SQL, STATE.SCHEMA_V2_SQL):
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta VALUES (1, 2, '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z')"
            )
            connection.execute(
                "INSERT INTO runs VALUES ('legacy-run','weekly','2026-08-24','2026-08-28','2026-08-29T00:00:00Z',NULL,'partial','pending','source.v1')"
            )
            connection.execute(
                """
                INSERT INTO weekly_reviews VALUES (
                    'weekly:2026-08-24:2026-08-28', 1, 'legacy-run',
                    '2026-08-24', '2026-08-28', '2026-08-29T01:00:00Z',
                    'source.v1', ?, NULL, ?, 'partial', NULL
                )
                """,
                ("c" * 64, "d" * 64),
            )
            connection.execute("PRAGMA user_version=2")
        self.db_path.chmod(0o600)

        with self.open_store() as store:
            self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(store.table_count("runs"), 1)
            self.assertEqual(store.table_count("weekly_reviews"), 1)
            self.assertEqual(store.table_count("plan_versions"), 0)
            row = store.connection.execute(
                "SELECT facts_hash, dependency_hash FROM weekly_reviews"
            ).fetchone()
            self.assertEqual(tuple(row), ("c" * 64, "d" * 64))
            self.assertEqual(store.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(store.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

        backups = list(self.state_dir.glob("review.sqlite3.backup-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM weekly_reviews").fetchone()[0], 1)

    def test_v3_migration_failure_rolls_back_to_intact_v2_database(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as connection:
            for schema in (STATE.SCHEMA_V1_SQL, STATE.SCHEMA_V2_SQL):
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta VALUES (1, 2, '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z')"
            )
            connection.execute(
                "INSERT INTO runs VALUES ('legacy-run','weekly','2026-08-24','2026-08-28','2026-08-29T00:00:00Z',NULL,'partial','pending','source.v1')"
            )
            connection.execute("PRAGMA user_version=2")
        self.db_path.chmod(0o600)
        broken = "CREATE TABLE should_rollback(value TEXT NOT NULL); THIS IS NOT SQL;"
        with mock.patch.object(STATE, "SCHEMA_V3_SQL", broken):
            with self.assertRaises(STATE.StateMigrationError):
                self.open_store()
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertNotIn("should_rollback", names)
            self.assertNotIn("plan_versions", names)

    def test_plan_versions_are_append_only_and_confirmation_preserves_draft_hash(self) -> None:
        draft = plan_state(status="draft")
        with self.open_store() as store:
            with self.assertRaises(STATE.StateContractError):
                store.put_plan_version(plan_state())
            first = store.put_plan_version(draft)
            reused = store.put_plan_version(draft)
            conflicting = json.loads(json.dumps(draft))
            conflicting["expires_at"] = "2026-10-01T16:00:00-04:00"
            with self.assertRaises(STATE.StateContractError):
                store.put_plan_version(conflicting)

            confirmed = plan_state(
                version=2,
                status="confirmed",
                supersedes_version=1,
            )
            altered = json.loads(json.dumps(confirmed))
            altered["zones"][1]["low"] = "98.5"
            with self.assertRaises(STATE.StateContractError):
                store.put_plan_version(altered)
            second = store.put_plan_version(confirmed)
            readback = store.get_plan_version("plan-demo", 2)
        self.assertEqual((first.action, reused.action, second.action), ("written", "reused", "written"))
        self.assertEqual(
            STATE.normalize_plan_version(readback),
            STATE.normalize_plan_version(confirmed),
        )

    def test_plan_state_rejects_pre_entry_add_and_unverified_position_management(self) -> None:
        pre_entry_add = plan_state()
        pre_entry_add["zones"][1]["kind"] = "add"
        orphan = plan_state(
            plan_id="manage-demo",
            status="draft",
            stage="position_management",
            setup="position_management",
            parent_plan_id="missing-parent",
            parent_plan_version=1,
            initial_buy_episode_key="2026-08-21|DEMO.US|buy",
        )
        with self.open_store() as store:
            with self.assertRaises(STATE.StateContractError):
                store.put_plan_version(pre_entry_add)
            with self.assertRaises(STATE.StateContractError):
                store.put_plan_version(orphan)
            self.assertEqual(store.table_count("plan_versions"), 0)

    def test_position_management_add_requires_confirmed_matching_parent(self) -> None:
        parent = plan_state(version=2, supersedes_version=1)
        management = plan_state(
            plan_id="manage-demo",
            status="draft",
            stage="position_management",
            setup="position_management",
            parent_plan_id="plan-demo",
            parent_plan_version=2,
            initial_buy_episode_key="2026-08-21|DEMO.US|buy",
            content_digest="e" * 64,
        )
        management["generated_at"] = "2026-08-22T08:00:00-04:00"
        with self.open_store() as store:
            store.put_plan_version(plan_state(status="draft"))
            store.put_plan_version(parent)
            with self.assertRaises(STATE.StateContractError):
                store.put_plan_version(management)
            buy = {**trade_rows()[0], "market_date": "2026-08-21", "side": "buy"}
            store.ingest_partition(
                dataset="trades", period_start="2026-08-21", period_end="2026-08-21",
                contract_version="source.v1:trades", status="complete",
                collected_at="2026-08-22T07:00:00-04:00", payload=[buy],
            )
            written = store.put_plan_version(management)
            self.assertEqual(written.action, "written")
            self.assertEqual(store.get_plan_version("manage-demo")["zones"][1]["kind"], "add")

    def test_weekly_v2_derives_execution_metrics_and_keeps_legacy_pnl_tables_empty(self) -> None:
        with self.open_store() as store:
            partition = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=[{**trade_rows()[0], "side": "sell"}],
            )
            dependency = {
                "dataset": "trades",
                "period_start": "2026-08-28",
                "period_end": "2026-08-28",
                "contract_version": "source.v1:trades",
                "partition_revision": partition.revision,
                "payload_hash": partition.payload_hash,
            }
            store.put_plan_version(plan_state(status="draft"))
            store.put_plan_version(plan_state(version=2, supersedes_version=1))
            store.start_run(
                run_id="weekly-run",
                mode="weekly",
                period_start="2026-08-24",
                period_end="2026-08-28",
                started_at="2026-08-30T08:00:00+08:00",
                data_status="complete",
                source_contract_version="source.v1",
            )
            bundle = weekly_v2_bundle(dependency)
            for day in ("2026-08-26", "2026-08-27"):
                extra = store.ingest_partition(
                    dataset="trades", period_start=day, period_end=day,
                    contract_version="source.v1:trades", status="complete",
                    collected_at="2026-08-29T08:00:00+08:00",
                    payload=[{**trade_rows()[0], "market_date": day, "side": "buy"}],
                )
                bundle["dependencies"].append({
                    **dependency, "period_start": day, "period_end": day,
                    "partition_revision": extra.revision, "payload_hash": extra.payload_hash,
                })
            result = store.ingest_weekly_review(bundle)
            unsupported = json.loads(json.dumps(bundle))
            unsupported["episode_assessments"][0]["market_date"] = "2026-08-25"
            with self.assertRaises(STATE.StateContractError):
                store.ingest_weekly_review(unsupported)
            review = store.get_weekly_review("weekly:2026-08-24:2026-08-28")
            self.assertEqual(result.action, "written")
            self.assertEqual(review["execution_metrics"]["coverage_rate"], "1.000000")
            self.assertEqual(review["execution_metrics"]["execution_rate"], "0.666667")
            self.assertEqual(review["execution_metrics"]["plan_win_rate"], "0.666667")
            self.assertEqual(review["execution_metrics"]["review_needed_count"], 2)
            self.assertEqual(len(review["episode_assessments"]), 3)
            self.assertEqual(store.table_count("weekly_performance"), 0)
            self.assertEqual(store.table_count("weekly_attributions"), 0)
            self.assertEqual(store.table_count("weekly_cash_flow_aggregates"), 0)

    def test_weekly_v2_blocked_metrics_do_not_fabricate_zero_rates(self) -> None:
        with self.open_store() as store:
            partition = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows(),
            )
            dependency = {
                "dataset": "trades",
                "period_start": "2026-08-28",
                "period_end": "2026-08-28",
                "contract_version": "source.v1:trades",
                "partition_revision": partition.revision,
                "payload_hash": partition.payload_hash,
            }
            store.start_run(
                run_id="weekly-run",
                mode="weekly",
                period_start="2026-08-24",
                period_end="2026-08-28",
                started_at="2026-08-30T08:00:00+08:00",
                data_status="partial",
                source_contract_version="source.v1",
            )
            store.ingest_weekly_review(weekly_v2_bundle(dependency, blocked=True))
            metrics = store.get_weekly_review(
                "weekly:2026-08-24:2026-08-28"
            )["execution_metrics"]
        self.assertEqual(metrics["data_status"], "blocked")
        self.assertIsNone(metrics["coverage_rate"])
        self.assertIsNone(metrics["execution_rate"])
        self.assertIsNone(metrics["plan_win_rate"])

    def test_weekly_review_is_idempotent_revisioned_and_confirmation_independent(self) -> None:
        with self.open_store() as store:
            partition = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows(),
            )
            dependency = {
                "dataset": "trades",
                "period_start": "2026-08-28",
                "period_end": "2026-08-28",
                "contract_version": "source.v1:trades",
                "partition_revision": partition.revision,
                "payload_hash": partition.payload_hash,
            }
            store.start_run(
                run_id="weekly-run",
                mode="weekly",
                period_start="2026-08-24",
                period_end="2026-08-28",
                started_at="2026-08-30T08:00:00+08:00",
                data_status="partial",
                source_contract_version="source.v1",
            )
            first = store.ingest_weekly_review(weekly_bundle(dependency))
            reused = store.ingest_weekly_review(weekly_bundle(dependency))
            revised = store.ingest_weekly_review(weekly_bundle(dependency, "计划仍未确认"))
            review = store.get_weekly_review("weekly:2026-08-24:2026-08-28")
            self.assertEqual((first.action, reused.action, revised.action), ("written", "reused", "written"))
            self.assertEqual((first.revision, reused.revision, revised.revision), (1, 1, 2))
            self.assertEqual(store.table_count("weekly_reviews"), 2)
            self.assertEqual(store.table_count("weekly_module_statuses"), 16)
            self.assertEqual(review["confirmation_status"], "pending")
            self.assertEqual(review["freshness"]["status"], "current")

    def test_daily_partition_revision_marks_weekly_review_stale_without_weekly_write(self) -> None:
        with self.open_store() as store:
            partition = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows("2"),
            )
            dependency = {
                "dataset": "trades",
                "period_start": "2026-08-28",
                "period_end": "2026-08-28",
                "contract_version": "source.v1:trades",
                "partition_revision": partition.revision,
                "payload_hash": partition.payload_hash,
            }
            store.start_run(
                run_id="weekly-run",
                mode="weekly",
                period_start="2026-08-24",
                period_end="2026-08-28",
                started_at="2026-08-30T08:00:00+08:00",
                data_status="partial",
                source_contract_version="source.v1",
            )
            store.ingest_weekly_review(weekly_bundle(dependency))
            weekly_counts = {name: store.table_count(name) for name in STATE.SCHEMA_TABLES if name.startswith("weekly_")}
            store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T09:00:00+08:00",
                payload=trade_rows("3"),
            )
            freshness = store.weekly_review_freshness("weekly:2026-08-24:2026-08-28", 1)
            after_counts = {name: store.table_count(name) for name in STATE.SCHEMA_TABLES if name.startswith("weekly_")}
        self.assertEqual(freshness["status"], "stale")
        self.assertEqual(freshness["changed_dependencies"][0]["period_start"], "2026-08-28")
        self.assertEqual(after_counts, weekly_counts)

    def test_weekly_review_rejects_non_latest_dependency_and_non_whitelisted_cash(self) -> None:
        with self.open_store() as store:
            first = store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T08:00:00+08:00",
                payload=trade_rows("2"),
            )
            store.ingest_partition(
                dataset="trades",
                period_start="2026-08-28",
                period_end="2026-08-28",
                contract_version="source.v1:trades",
                status="complete",
                collected_at="2026-08-29T09:00:00+08:00",
                payload=trade_rows("3"),
            )
            dependency = {
                "dataset": "trades",
                "period_start": "2026-08-28",
                "period_end": "2026-08-28",
                "contract_version": "source.v1:trades",
                "partition_revision": first.revision,
                "payload_hash": first.payload_hash,
            }
            store.start_run(
                run_id="weekly-run",
                mode="weekly",
                period_start="2026-08-24",
                period_end="2026-08-28",
                started_at="2026-08-30T08:00:00+08:00",
                data_status="partial",
                source_contract_version="source.v1",
            )
            with self.assertRaises(STATE.StateContractError):
                store.ingest_weekly_review(weekly_bundle(dependency))
            invalid_cash = weekly_bundle(dependency)
            invalid_cash["cash_flow_aggregates"][0]["category"] = "commission"
            with self.assertRaises(STATE.StateContractError):
                STATE.normalize_weekly_review_bundle(invalid_cash)
            self.assertEqual(store.table_count("weekly_reviews"), 0)


if __name__ == "__main__":
    unittest.main()
