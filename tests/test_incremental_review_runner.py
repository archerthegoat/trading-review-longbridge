from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "trading-center-review" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

STATE_SPEC = importlib.util.spec_from_file_location("trading_review_state", SCRIPTS / "trading_review_state.py")
if STATE_SPEC is None or STATE_SPEC.loader is None:
    raise RuntimeError("could not load state module")
STATE = importlib.util.module_from_spec(STATE_SPEC)
sys.modules["trading_review_state"] = STATE
STATE_SPEC.loader.exec_module(STATE)

RUNNER_SPEC = importlib.util.spec_from_file_location("run_incremental_review", SCRIPTS / "run_incremental_review.py")
if RUNNER_SPEC is None or RUNNER_SPEC.loader is None:
    raise RuntimeError("could not load runner")
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(RUNNER)

from review_journal_contract import JournalError


GENERATED_AT = "2026-08-29T08:00:00+08:00"


def analysis_output() -> dict[str, object]:
    return {
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


def daily_bundle(run_id: str = "run-1") -> dict[str, object]:
    return {
        "schema_version": RUNNER.INPUT_SCHEMA,
        "run_id": run_id,
        "mode": "daily",
        "review_date": "2026-08-28",
        "generated_at": GENERATED_AT,
        "source_contract_version": "source.v1",
        "plan_hash": "a" * 64,
        "analysis_contract_version": "analysis.v1",
        "modules": {
            "account_snapshot": {
                "status": "complete",
                "collected_at": GENERATED_AT,
                "payload": {
                    "snapshot_at": GENERATED_AT,
                    "currency": "USD",
                    "net_assets": "1000.25",
                    "cash": "400.25",
                    "buying_power": "800.50",
                    "data_status": "complete",
                },
            },
            "positions_snapshot": {
                "status": "complete",
                "collected_at": GENERATED_AT,
                "payload": [
                    {
                        "snapshot_at": GENERATED_AT,
                        "symbol": "DEMO.US",
                        "underlying": "DEMO.US",
                        "instrument_type": "equity",
                        "quantity": "7",
                        "data_status": "complete",
                    }
                ],
            },
            "trades": {
                "status": "complete",
                "collected_at": GENERATED_AT,
                "payload": [
                    {
                        "market_date": "2026-08-28",
                        "symbol": "DEMO.US",
                        "side": "buy",
                        "order_count": 1,
                        "execution_count": 1,
                        "executed_quantity": "2",
                        "data_status": "complete",
                    }
                ],
            },
            "market_snapshots": {
                "status": "complete",
                "collected_at": GENERATED_AT,
                "payload": [
                    {
                        "as_of": GENERATED_AT,
                        "symbol": "DEMO.MKT",
                        "value": "100",
                        "previous_close": "99",
                        "change_pct": "1.01",
                        "session": "regular",
                        "proxy_for": None,
                        "data_status": "complete",
                    }
                ],
            },
            "relevant_events": {
                "status": "complete",
                "collected_at": GENERATED_AT,
                "payload": [
                    {
                        "derived_event_key": "event-hash-1",
                        "et_at": "2026-08-28T08:30:00-04:00",
                        "shanghai_at": "2026-08-28T20:30:00+08:00",
                        "title": "合成事件",
                        "status": "已发生",
                        "source_category": "calendar",
                        "impact_channel": "组合波动",
                        "data_status": "complete",
                    }
                ],
            },
        },
        "analysis": {
            "status": "complete",
            "model": "codex",
            "generated_at": GENERATED_AT,
            "output": analysis_output(),
        },
    }


def daily_facts_bundle(run_id: str = "run-facts") -> dict[str, object]:
    bundle = daily_bundle(run_id)
    bundle.pop("analysis")
    bundle["schema_version"] = RUNNER.FACTS_INPUT_SCHEMA
    return bundle


def retime_daily_bundle(bundle: dict[str, object], value: str) -> None:
    bundle["generated_at"] = value
    bundle["analysis"]["generated_at"] = value
    for module in bundle["modules"].values():
        module["collected_at"] = value
    bundle["modules"]["account_snapshot"]["payload"]["snapshot_at"] = value
    bundle["modules"]["positions_snapshot"]["payload"][0]["snapshot_at"] = value
    bundle["modules"]["market_snapshots"]["payload"][0]["as_of"] = value


def weekly_state_bundle(dependency: dict[str, object]) -> dict[str, object]:
    modules = []
    for name in sorted(STATE.WEEKLY_MODULES):
        status = "blocked" if name == "plan" else "empty" if name in {"performance", "attribution", "cash_flow"} else "complete"
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
        "schema_version": RUNNER.WEEKLY_STATE_SCHEMA,
        "run_id": "weekly-run",
        "review_key": "weekly:2026-08-24:2026-08-28",
        "period_start": "2026-08-24",
        "period_end": "2026-08-28",
        "generated_at": GENERATED_AT,
        "source_contract_version": "source.v1",
        "data_status": "partial",
        "plan_hash": None,
        "dependencies": [dependency],
        "modules": modules,
        "performance": None,
        "attributions": [],
        "cash_flow_aggregates": [],
        "episode_assessments": [],
        "execution_metrics": {"data_status": "blocked", "gap": "计划与执行证据缺失"},
        "review_items": [
            {
                "item_kind": "gap",
                "subject": "计划复核｜计划与实际",
                "summary": "计划权威缺失",
                "evidence_boundary": "不从持仓或成交反推计划",
                "evidence_kind": "gap",
                "data_status": "blocked",
            }
        ],
    }


class IncrementalRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="incremental-runner-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.db_path = self.root / "state" / "review.sqlite3"
        self.store = STATE.open_state_store(self.db_path, test_root=self.root, busy_timeout_ms=100)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_daily_plan_refreshes_current_facts_and_reuses_complete_trade_partition(self) -> None:
        before = RUNNER.build_daily_plan(
            self.store,
            review_date="2026-08-28",
            plan_bytes=b"plan-v1",
            source_contract_version="source.v1",
        )
        self.assertEqual(before["modules"]["trades"]["action"], "read")
        self.assertTrue(
            all(
                before["modules"][name]["action"] == "refresh"
                for name in ("account_snapshot", "positions_snapshot", "market_snapshots", "relevant_events")
            )
        )
        self.store.ingest_partition(
            dataset="trades",
            period_start="2026-08-28",
            period_end="2026-08-28",
            contract_version="source.v1:trades",
            status="complete",
            collected_at=GENERATED_AT,
            payload=daily_bundle()["modules"]["trades"]["payload"],
        )
        after = RUNNER.build_daily_plan(
            self.store,
            review_date="2026-08-28",
            plan_bytes=b"plan-v1",
            source_contract_version="source.v1",
        )
        changed = RUNNER.build_daily_plan(
            self.store,
            review_date="2026-08-28",
            plan_bytes=b"plan-v2",
            source_contract_version="source.v1",
        )
        self.assertEqual(after["modules"]["trades"]["action"], "cache_hit")
        self.assertEqual(
            after["modules"]["trades"]["cached_partition"]["payload"],
            daily_bundle()["modules"]["trades"]["payload"],
        )
        self.assertEqual(after["modules"]["trades"]["cached_partition"]["revision"], 1)
        self.assertNotEqual(after["plan_hash"], changed["plan_hash"])

    def test_daily_bundle_is_idempotent_and_analysis_cache_reuses_exact_key(self) -> None:
        first = RUNNER.process_daily_bundle(self.store, daily_bundle("run-1"))
        second = RUNNER.process_daily_bundle(self.store, daily_bundle("run-2"))
        self.assertEqual(first["data_status"], "complete")
        self.assertTrue(all(value["action"] == "written" for value in first["modules"].values()))
        self.assertTrue(all(value["action"] == "reused" for value in second["modules"].values()))
        self.assertEqual(first["facts_hash"], second["facts_hash"])
        self.assertEqual(first["analysis_cache"], "written")
        self.assertEqual(second["analysis_cache"], "reused")
        self.assertEqual(self.store.table_count("trade_aggregates"), 1)
        self.assertTrue(
            all(
                self.store.table_count(name) == 0
                for name in STATE.SCHEMA_TABLES
                if name.startswith("weekly_")
            )
        )

    def test_analysis_plan_checks_cache_before_codex_and_returns_original_snapshot(self) -> None:
        before = RUNNER.build_daily_analysis_plan(
            self.store,
            daily_facts_bundle("run-before-analysis"),
        )
        self.assertEqual(before["action"], "run_codex")
        self.assertIsNone(before["cached_analysis"])

        RUNNER.process_daily_bundle(self.store, daily_bundle("run-cache-seed"))
        after = RUNNER.build_daily_analysis_plan(
            self.store,
            daily_facts_bundle("run-after-analysis"),
        )
        self.assertEqual(after["action"], "reuse")
        self.assertEqual(after["facts_hash"], before["facts_hash"])
        self.assertEqual(after["cached_analysis"]["generated_at"], GENERATED_AT)
        self.assertEqual(after["cached_analysis"]["model"], "codex")
        self.assertEqual(after["cached_analysis"]["output"], analysis_output())

        changed_plan = daily_facts_bundle("run-changed-plan")
        changed_plan["plan_hash"] = "b" * 64
        changed = RUNNER.build_daily_analysis_plan(self.store, changed_plan)
        self.assertEqual(changed["action"], "run_codex")
        self.assertIsNone(changed["cached_analysis"])

    def test_cache_metadata_mismatch_fails_before_a_new_run_is_written(self) -> None:
        RUNNER.process_daily_bundle(self.store, daily_bundle("run-cache-original"))
        mismatch = daily_bundle("run-cache-mismatch")
        mismatch["analysis"]["generated_at"] = "2026-08-29T08:01:00+08:00"
        with self.assertRaises(RUNNER.RunnerContractError):
            RUNNER.process_daily_bundle(self.store, mismatch)
        self.assertEqual(self.store.table_count("runs"), 1)
        self.assertEqual(self.store.table_count("partitions"), len(RUNNER.DAILY_MODULES))

    def test_manifest_contains_lineage_and_counts_but_no_private_fact_values(self) -> None:
        result = RUNNER.process_daily_bundle(self.store, daily_bundle("run-manifest"))
        private_root = RUNNER.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        private_root.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="manifest-test-", dir=str(private_root)) as directory:
            directory_path = Path(directory)
            directory_path.chmod(0o700)
            manifest_path = directory_path / "run-manifest.json"
            RUNNER.write_manifest(manifest_path, result)
            text = manifest_path.read_text(encoding="utf-8")
            parsed = json.loads(text)
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
            self.assertEqual(parsed["schema_version"], RUNNER.MANIFEST_SCHEMA)
            for forbidden in ("1000.25", "400.25", "800.50", "DEMO.US", '"quantity"'):
                self.assertNotIn(forbidden, text)

            injected = dict(result)
            injected["account_value"] = "1000.25"
            with self.assertRaises(RUNNER.RunnerContractError):
                RUNNER.write_manifest(directory_path / "injected.json", injected)

    def test_private_output_rejects_broad_parent_permissions(self) -> None:
        result = RUNNER.process_daily_bundle(self.store, daily_bundle("run-broad-parent"))
        private_root = RUNNER.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        private_root.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="broad-parent-", dir=str(private_root)) as directory:
            directory_path = Path(directory)
            directory_path.chmod(0o755)
            with self.assertRaises(RUNNER.RunnerContractError):
                RUNNER.write_manifest(directory_path / "manifest.json", result)

    def test_invalid_cli_output_fails_before_state_database_creation(self) -> None:
        not_created = self.root / "must-not-exist.sqlite3"
        result = RUNNER.main(
            [
                "daily-plan",
                "--review-date",
                "2026-08-28",
                "--plan-file",
                str(ROOT / "README.md"),
                "--source-contract-version",
                "source.v1",
                "--state-db",
                str(not_created),
                "--output",
                str(ROOT / "forbidden-output.json"),
            ]
        )
        self.assertEqual(result, 2)
        self.assertFalse(not_created.exists())

    def test_invalid_cli_bundle_fails_before_state_database_creation(self) -> None:
        not_created = self.root / "must-not-exist-invalid-bundle.sqlite3"
        private_root = RUNNER.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        private_root.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="invalid-bundle-", dir=str(private_root)) as directory:
            directory_path = Path(directory)
            directory_path.chmod(0o700)
            invalid = daily_bundle("run-invalid-cli")
            invalid["modules"]["trades"]["payload"][0]["order_id"] = "synthetic"
            input_path = directory_path / "input.json"
            input_path.write_text(json.dumps(invalid), encoding="utf-8")
            input_path.chmod(0o600)
            result = RUNNER.main(
                [
                    "ingest-daily",
                    "--input",
                    str(input_path),
                    "--state-db",
                    str(not_created),
                    "--manifest",
                    str(directory_path / "manifest.json"),
                ]
            )
        self.assertEqual(result, 2)
        self.assertFalse(not_created.exists())

    def test_sensitive_bundle_fails_closed_before_any_partition_write(self) -> None:
        bundle = daily_bundle("run-sensitive")
        bundle["modules"]["trades"]["payload"][0]["order_id"] = "synthetic"
        with self.assertRaises(STATE.StateContractError):
            RUNNER.process_daily_bundle(self.store, bundle)
        self.assertEqual(self.store.table_count("partitions"), 0)
        self.assertEqual(self.store.table_count("runs"), 0)

    def test_duplicate_fact_rows_fail_closed_before_run_creation(self) -> None:
        bundle = daily_bundle("run-duplicate")
        bundle["modules"]["trades"]["payload"] *= 2
        with self.assertRaises(STATE.StateContractError):
            RUNNER.process_daily_bundle(self.store, bundle)
        self.assertEqual(self.store.table_count("partitions"), 0)
        self.assertEqual(self.store.table_count("runs"), 0)

    def test_facts_hash_distinguishes_blocked_empty_from_success_empty(self) -> None:
        blocked = daily_bundle("run-blocked-empty")
        blocked["modules"]["trades"] = {
            "status": "blocked",
            "collected_at": GENERATED_AT,
            "payload": [],
            "error_category": "synthetic_provider_failure",
        }
        success_empty = daily_bundle("run-success-empty")
        success_empty["modules"]["trades"] = {
            "status": "empty",
            "collected_at": "2026-08-29T09:00:00+08:00",
            "payload": [],
        }
        retime_daily_bundle(success_empty, "2026-08-29T09:00:00+08:00")
        blocked_result = RUNNER.process_daily_bundle(self.store, blocked)
        empty_result = RUNNER.process_daily_bundle(self.store, success_empty)
        self.assertNotEqual(blocked_result["facts_hash"], empty_result["facts_hash"])
        self.assertEqual(blocked_result["analysis_cache"], "written")
        self.assertEqual(empty_result["analysis_cache"], "written")

    def test_daily_source_binding_failure_is_recoverable_by_exact_rerun(self) -> None:
        bundle = daily_bundle("run-source-recovery")
        with mock.patch(
            "review_journal_state.record_daily_source",
            side_effect=RuntimeError("synthetic binding failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic binding failure"):
                RUNNER.process_daily_bundle(self.store, bundle)
        run = self.store.get_run(bundle["run_id"])
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(self.store.table_count("daily_review_sources"), 0)

        manifest = RUNNER.process_daily_bundle(self.store, bundle)
        self.assertEqual(manifest["run_id"], bundle["run_id"])
        self.assertEqual(self.store.table_count("daily_review_sources"), 1)

    def test_older_orphan_run_cannot_supersede_a_newer_source(self) -> None:
        older = daily_bundle("run-source-older")
        with mock.patch(
            "review_journal_state.record_daily_source",
            side_effect=RuntimeError("synthetic binding failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic binding failure"):
                RUNNER.process_daily_bundle(self.store, older)
        newer = daily_bundle("run-source-newer")
        newer_at = "2026-08-29T09:00:00+08:00"
        retime_daily_bundle(newer, newer_at)
        newer["plan_hash"] = "b" * 64
        RUNNER.process_daily_bundle(self.store, newer)

        with self.assertRaisesRegex(JournalError, "superseded"):
            RUNNER.process_daily_bundle(self.store, older)
        row = self.store.connection.execute(
            "SELECT * FROM daily_review_sources ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["run_id"], "run-source-newer")
        self.assertEqual(self.store.table_count("daily_review_sources"), 1)

    def test_weekly_plan_uses_expected_dates_and_requires_explicit_weekly_source_reads(self) -> None:
        self.store.ingest_partition(
            dataset="trades",
            period_start="2026-08-27",
            period_end="2026-08-27",
            contract_version="source.v1:trades",
            status="empty",
            collected_at=GENERATED_AT,
            payload=[],
        )
        plan = RUNNER.build_weekly_plan(
            self.store,
            expected_trade_dates=["2026-08-27", "2026-08-28"],
            source_contract_version="source.v1",
        )
        self.assertEqual(plan["trade_dates"]["2026-08-27"], "cache_hit")
        self.assertEqual(plan["trade_dates"]["2026-08-28"], "read")
        self.assertNotIn("profit_analysis", plan["weekly_modules"])
        self.assertNotIn("cash_flow", plan["weekly_modules"])
        self.assertEqual(plan["weekly_modules"]["execution_rule_evidence"]["action"], "read_if_authorized")
        self.assertEqual(plan["weekly_modules"]["confirmed_plan_versions"]["action"], "read_local_confirmed_versions")

    def test_weekly_ingest_manifest_and_dashboard_export_use_persisted_readback(self) -> None:
        partition = self.store.ingest_partition(
            dataset="trades",
            period_start="2026-08-28",
            period_end="2026-08-28",
            contract_version="source.v1:trades",
            status="complete",
            collected_at=GENERATED_AT,
            payload=daily_bundle()["modules"]["trades"]["payload"],
        )
        dependency = {
            "dataset": "trades",
            "period_start": "2026-08-28",
            "period_end": "2026-08-28",
            "contract_version": "source.v1:trades",
            "partition_revision": partition.revision,
            "payload_hash": partition.payload_hash,
        }
        self.store.start_run(
            run_id="weekly-run",
            mode="weekly",
            period_start="2026-08-24",
            period_end="2026-08-28",
            started_at=GENERATED_AT,
            data_status="partial",
            source_contract_version="source.v1",
        )
        manifest = RUNNER.process_weekly_bundle(self.store, weekly_state_bundle(dependency))
        normalized_manifest = RUNNER.validate_weekly_manifest(manifest)
        packet = RUNNER.build_weekly_dashboard_packet(
            self.store, "weekly:2026-08-24:2026-08-28"
        )
        encoded = json.dumps(packet, ensure_ascii=False)
        self.assertEqual(normalized_manifest["action"], "written")
        self.assertEqual(normalized_manifest["freshness"]["status"], "current")
        self.assertEqual(packet["schema_version"], "trading-review-weekly-dashboard.v2")
        self.assertEqual(packet["execution_metrics"]["data_status"], "blocked")
        self.assertIsNone(packet["execution_metrics"]["plan_win_rate"])
        self.assertEqual(self.store.table_count("weekly_performance"), 0)
        self.assertEqual(packet["meta"]["confirmation_status"], "pending")
        self.assertNotIn("initial_asset_value", encoded)
        self.assertNotIn("ending_asset_value", encoded)
        self.assertNotIn("buying_power", encoded)

    def test_invalid_weekly_cli_bundle_fails_before_state_database_creation(self) -> None:
        not_created = self.root / "must-not-exist-invalid-weekly.sqlite3"
        private_root = RUNNER.PRIVATE_ROOT
        private_root.mkdir(parents=True, exist_ok=True)
        private_root.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="invalid-weekly-", dir=str(private_root)) as directory:
            directory_path = Path(directory)
            directory_path.chmod(0o700)
            input_path = directory_path / "weekly.json"
            input_path.write_text(json.dumps({"schema_version": RUNNER.WEEKLY_STATE_SCHEMA}), encoding="utf-8")
            input_path.chmod(0o600)
            result = RUNNER.main(
                [
                    "ingest-weekly",
                    "--input",
                    str(input_path),
                    "--state-db",
                    str(not_created),
                    "--manifest",
                    str(directory_path / "manifest.json"),
                ]
            )
        self.assertEqual(result, 2)
        self.assertFalse(not_created.exists())


if __name__ == "__main__":
    unittest.main()
