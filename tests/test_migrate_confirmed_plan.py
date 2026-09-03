from __future__ import annotations

import hashlib
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
SCRIPT = ROOT / "skills" / "daily-trade-journal" / "scripts" / "migrate_confirmed_plan.py"
SPEC = importlib.util.spec_from_file_location("migrate_confirmed_plan", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load confirmed plan migration helper")
MIGRATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATE
SPEC.loader.exec_module(MIGRATE)


CONFIRMED_AT = "2026-09-01T19:50:05+08:00"
PRIVATE_MARKER = "SYNTH-PRIVATE-CONTENT-DO-NOT-PRINT"


def authority(*, explicit_plan: bool = False) -> dict[str, object]:
    candidate: dict[str, object] = {
        "display_symbol": "SYNTH.US",
        "action": "观察",
        "tool_kind": "stock",
        "stage": "candidate",
        "note": "private candidate note",
    }
    if explicit_plan:
        candidate.update({"action": "buy"})
    interview = {
        "schema_version": "trading-review-confirmed-interview.v1",
        "status": "draft_not_final_confirmed",
        "review_date": "2026-09-01",
        "generated_at": "2026-09-01T19:00:00+08:00",
        "source": "synthetic",
        "candidates": [candidate],
        "holdings": [
            {
                "display_symbol": "HOLD.US",
                "actual_trade_symbol": "HOLD.US",
                "action": "持有",
                "tool_kind": "stock",
                "status": "confirmed",
            }
        ],
        "global_rules": {"rule": "private rule"},
        "open_questions": ["private question"],
        "daily_review_workflow": {"checks": ["private"]},
        "strategy_categories": ["private"],
        "valuation_summary": {"available": False},
    }
    source = {
        "schema_version": "trading-review-confirmed-authority.v1",
        "approved_draft_schema_version": "trading-review-confirmed-interview.v1",
        "approved_draft_hash": hashlib.sha256(
            json.dumps(interview, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "confirmation_status": "confirmed",
        "confirmed_at": CONFIRMED_AT,
        "review_date": "2026-09-01",
        "review_type": "daily",
        "scope": {
            "alternative_budget": "private",
            "broker_access": "private",
            "external_writes": "private",
            "timeframe_policy": "private",
            "unformed_candidates": "private",
            "write_boundary": "private",
        },
        "source": "synthetic authority",
        "source_contract_version": "synthetic.v1",
        "approved_interview": interview,
    }
    return source


def write_private(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def authority_with_private_marker(*, explicit_plan: bool = False) -> dict[str, object]:
    value = authority(explicit_plan=explicit_plan)
    interview = value["approved_interview"]
    if not isinstance(interview, dict) or not isinstance(interview["candidates"], list):
        raise AssertionError("synthetic authority fixture is malformed")
    if not isinstance(interview["candidates"][0], dict):
        raise AssertionError("synthetic authority candidate is malformed")
    interview["candidates"][0]["note"] = PRIVATE_MARKER
    value["approved_draft_hash"] = hashlib.sha256(
        json.dumps(interview, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return value


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class ConfirmedPlanMigrationTests(unittest.TestCase):
    def assert_cli_does_not_leak_private_content(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotIn(PRIVATE_MARKER, completed.stdout)
        self.assertNotIn(PRIVATE_MARKER, completed.stderr)

    def test_migrate_validates_hash_preserves_snapshot_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-migration-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            output = root / "2026-09-01-195005.md"
            write_private(source, authority())
            result = MIGRATE.migrate(str(source), str(output))
            self.assertEqual(result, {"status": "complete", "version": "2026-09-01-195005", "plan_rows": 0})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            payload = MIGRATE._extract_markdown(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "confirmed")
            self.assertEqual(payload["plans"], [])
            self.assertTrue(payload["context_available"])
            markdown = output.read_text(encoding="utf-8")
            body = markdown.split(MIGRATE.MARKER_START, 1)[1].split(MIGRATE.MARKER_END, 1)[0].strip()
            migrated = MIGRATE._parse_json(body.encode("utf-8"))
            self.assertEqual(set(migrated["source_snapshot"]), {"candidates", "holdings", "global_rules", "open_questions"})
            with self.assertRaises(MIGRATE.PlanError):
                MIGRATE.migrate(str(source), str(output))

    def test_explicit_plan_is_extracted_but_unconfirmed_description_is_not(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-extraction-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            version_dir = root / "versions"
            version_dir.mkdir()
            output = root / "plans.json"
            write_private(source, authority(explicit_plan=True))
            MIGRATE.migrate(str(source), str(version_dir / "2026-09-01-195005.md"))
            result = MIGRATE.extract(str(version_dir), str(output))
            self.assertEqual(result, {"status": "complete", "versions": 1, "plan_rows": 1})
            extracted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(extracted["schema_version"], MIGRATE.PLAN_INPUT_SCHEMA)
            self.assertEqual(len(extracted["versions"]), 1)
            self.assertEqual(extracted["versions"][0]["plans"][0]["underlying"], "SYNTH.US")
            self.assertEqual(extracted["versions"][0]["plans"][0]["action"], "买入")
            self.assertEqual(extracted["versions"][0]["plans"][0]["tool"], "正股")
            self.assertTrue(extracted["versions"][0]["context_available"])
            self.assertNotIn("private candidate note", output.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_explicit_false_or_pending_markers_are_not_promoted(self) -> None:
        for marker in ({"confirmed": False}, {"confirmation_status": "pending"}, {"plan_status": "draft"}):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(prefix="daily-plan-conflict-") as directory:
                root = Path(directory)
                source = root / "authority.json"
                version_dir = root / "versions"
                version_dir.mkdir()
                output = root / "plans.json"
                value = authority(explicit_plan=True)
                value["approved_interview"]["candidates"][0].update(marker)  # type: ignore[index]
                value["approved_draft_hash"] = hashlib.sha256(
                    json.dumps(
                        value["approved_interview"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                write_private(source, value)
                MIGRATE.migrate(str(source), str(version_dir / "2026-09-01-195005.md"))
                MIGRATE.extract(str(version_dir), str(output))
                extracted = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(extracted["versions"][0]["plans"], [])

    def test_same_key_conflicting_promotion_markers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-duplicate-conflict-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            version_dir = root / "versions"
            version_dir.mkdir()
            value = authority(explicit_plan=True)
            value["approved_interview"]["holdings"].append({  # type: ignore[index]
                "display_symbol": "SYNTH.US",
                "action": "buy",
                "tool_kind": "stock",
                "confirmed": False,
            })
            value["approved_draft_hash"] = hashlib.sha256(
                json.dumps(
                    value["approved_interview"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            write_private(source, value)
            with self.assertRaises(MIGRATE.PlanError):
                MIGRATE.migrate(str(source), str(version_dir / "2026-09-01-195005.md"))

    def test_tampered_hash_and_duplicate_block_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-fail-closed-") as directory:
            root = Path(directory)
            source = authority()
            source["approved_draft_hash"] = "0" * 64
            source_path = root / "authority.json"
            output = root / "bad.md"
            write_private(source_path, source)
            with self.assertRaises(MIGRATE.PlanError):
                MIGRATE.migrate(str(source_path), str(output))
            valid_source = root / "valid.json"
            write_private(valid_source, authority())
            MIGRATE.migrate(str(valid_source), str(output))
            duplicate = output.read_text(encoding="utf-8") + MIGRATE.MARKER_START + "\n{}\n" + MIGRATE.MARKER_END + "\n"
            output.write_text(duplicate, encoding="utf-8")
            os.chmod(output, 0o600)
            with self.assertRaises(MIGRATE.PlanError):
                MIGRATE._extract_markdown(output.read_text(encoding="utf-8"))

    def test_cli_failure_writes_blocked_envelope_without_plan_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-cli-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            output = root / "plans.json"
            write_private(source, {"not": "an authority"})
            completed = run_cli(
                "--mode",
                "migrate",
                "--source-authority",
                str(source),
                "--output",
                str(output),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "blocked")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_cli_repeated_migrate_preserves_existing_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-cli-repeat-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            output = root / "2026-09-01-195005.md"
            write_private(source, authority_with_private_marker())

            first = run_cli(
                "--mode",
                "migrate",
                "--source-authority",
                str(source),
                "--output",
                str(output),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assert_cli_does_not_leak_private_content(first)
            before = hashlib.sha256(output.read_bytes()).digest()

            second = run_cli(
                "--mode",
                "migrate",
                "--source-authority",
                str(source),
                "--output",
                str(output),
            )
            self.assertEqual(second.returncode, 2)
            self.assertEqual(second.stdout, "")
            self.assertEqual(second.stderr, "")
            self.assert_cli_does_not_leak_private_content(second)
            self.assertEqual(hashlib.sha256(output.read_bytes()).digest(), before)

    def test_cli_migrate_same_source_output_preserves_valid_and_invalid_source(self) -> None:
        cases = (authority_with_private_marker(), {"not": "an authority", "secret": PRIVATE_MARKER})
        for value in cases:
            with self.subTest(valid=value is cases[0]), tempfile.TemporaryDirectory(prefix="daily-plan-cli-same-") as directory:
                source = Path(directory) / "authority.json"
                write_private(source, value)
                before = source.read_bytes()

                completed = run_cli(
                    "--mode",
                    "migrate",
                    "--source-authority",
                    str(source),
                    "--output",
                    str(source),
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "")
                self.assert_cli_does_not_leak_private_content(completed)
                self.assertEqual(source.read_bytes(), before)

    def test_cli_extract_output_same_as_valid_or_corrupt_input_preserves_input(self) -> None:
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory(prefix="daily-plan-cli-extract-conflict-") as directory:
                root = Path(directory)
                source = root / "authority.json"
                versions = root / "versions"
                versions.mkdir()
                input_path = versions / "2026-09-01-195005.md"
                write_private(source, authority_with_private_marker(explicit_plan=True))
                migrated = run_cli(
                    "--mode",
                    "migrate",
                    "--source-authority",
                    str(source),
                    "--output",
                    str(input_path),
                )
                self.assertEqual(migrated.returncode, 0, migrated.stderr)
                if corrupt:
                    input_path.write_bytes(("corrupt " + PRIVATE_MARKER).encode("utf-8"))
                    os.chmod(input_path, 0o600)
                before = input_path.read_bytes()

                completed = run_cli(
                    "--mode",
                    "extract",
                    "--plans-dir",
                    str(versions),
                    "--output",
                    str(input_path),
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "")
                self.assert_cli_does_not_leak_private_content(completed)
                self.assertEqual(input_path.read_bytes(), before)

    def test_cli_extract_alias_conflict_preserves_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-cli-extract-alias-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            versions = root / "versions"
            alias_dir = versions / "alias"
            versions.mkdir()
            alias_dir.mkdir()
            input_path = versions / "2026-09-01-195005.md"
            alias_path = Path(str(alias_dir) + "/../" + input_path.name)
            write_private(source, authority_with_private_marker())
            migrated = run_cli(
                "--mode",
                "migrate",
                "--source-authority",
                str(source),
                "--output",
                str(input_path),
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            before = input_path.read_bytes()

            completed = run_cli(
                "--mode",
                "extract",
                "--plans-dir",
                str(versions),
                "--output",
                str(alias_path),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            self.assert_cli_does_not_leak_private_content(completed)
            self.assertEqual(input_path.read_bytes(), before)

    def test_cli_extract_rejects_existing_markdown_output_outside_input_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-cli-extract-markdown-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            versions = root / "versions"
            outside = root / "outside"
            versions.mkdir()
            outside.mkdir()
            input_path = versions / "2026-09-01-195005.md"
            output = outside / "existing.md"
            write_private(source, authority_with_private_marker())
            migrated = run_cli(
                "--mode",
                "migrate",
                "--source-authority",
                str(source),
                "--output",
                str(input_path),
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            output.write_bytes(input_path.read_bytes())
            os.chmod(output, 0o600)
            before = hashlib.sha256(output.read_bytes()).digest()

            completed = run_cli(
                "--mode",
                "extract",
                "--plans-dir",
                str(versions),
                "--output",
                str(output),
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            self.assert_cli_does_not_leak_private_content(completed)
            self.assertEqual(hashlib.sha256(output.read_bytes()).digest(), before)

    def test_cli_migrate_then_extract_with_independent_output_succeeds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daily-plan-cli-normal-") as directory:
            root = Path(directory)
            source = root / "authority.json"
            versions = root / "versions"
            versions.mkdir()
            input_path = versions / "2026-09-01-195005.md"
            output = root / "plans.json"
            write_private(source, authority_with_private_marker(explicit_plan=True))

            migrated = run_cli(
                "--mode",
                "migrate",
                "--source-authority",
                str(source),
                "--output",
                str(input_path),
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assert_cli_does_not_leak_private_content(migrated)

            extracted = run_cli(
                "--mode",
                "extract",
                "--plans-dir",
                str(versions),
                "--output",
                str(output),
            )
            self.assertEqual(extracted.returncode, 0, extracted.stderr)
            self.assert_cli_does_not_leak_private_content(extracted)
            self.assertEqual(json.loads(extracted.stdout)["status"], "complete")
            extracted_payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(extracted_payload["versions"]), 1)
            self.assertEqual(len(extracted_payload["versions"][0]["plans"]), 1)
            self.assertNotIn(PRIVATE_MARKER, output.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

            output.write_bytes(b'{"stale": true}\n')
            os.chmod(output, 0o600)
            refreshed = run_cli(
                "--mode",
                "extract",
                "--plans-dir",
                str(versions),
                "--output",
                str(output),
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assert_cli_does_not_leak_private_content(refreshed)
            refreshed_payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(refreshed_payload["schema_version"], MIGRATE.PLAN_INPUT_SCHEMA)
            self.assertNotIn("stale", output.read_text(encoding="utf-8"))
            before_failed_extract = output.read_bytes()

            input_path.write_bytes(("corrupt " + PRIVATE_MARKER).encode("utf-8"))
            os.chmod(input_path, 0o600)
            failed_extract = run_cli(
                "--mode",
                "extract",
                "--plans-dir",
                str(versions),
                "--output",
                str(output),
            )
            self.assertEqual(failed_extract.returncode, 2)
            self.assertEqual(failed_extract.stdout, "")
            self.assertEqual(failed_extract.stderr, "")
            self.assert_cli_does_not_leak_private_content(failed_extract)
            self.assertEqual(output.read_bytes(), before_failed_extract)


if __name__ == "__main__":
    unittest.main()
