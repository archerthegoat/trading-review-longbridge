#!/usr/bin/env python3
"""Plan and ingest incremental daily/weekly trading-review facts.

This runner never invokes Longbridge itself. It first emits a collection plan,
then accepts only a fixed sanitized projection produced in the private runtime
directory. The separation makes cache hits inspectable before any broker read.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import trading_review_state as state


INPUT_SCHEMA = "trading-review-incremental-input.v1"
FACTS_INPUT_SCHEMA = "trading-review-incremental-facts.v1"
ANALYSIS_PLAN_SCHEMA = "trading-review-analysis-plan.v1"
MANIFEST_SCHEMA = "trading-review-run-manifest.v1"
WEEKLY_STATE_SCHEMA = "trading-review-weekly-state.v2"
WEEKLY_MANIFEST_SCHEMA = "trading-review-weekly-state-manifest.v1"
PLAN_SCHEMA = "trading-review-collection-plan.v1"
PRIVATE_ROOT = Path("/private/tmp/trading-center-review-runtime").resolve()
DAILY_MODULES = (
    "account_snapshot",
    "positions_snapshot",
    "trades",
    "market_snapshots",
    "relevant_events",
)
CURRENT_REFRESH_MODULES = DAILY_MODULES[:2] + DAILY_MODULES[3:]
MODULE_STATUSES = state.DATA_STATUSES
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RunnerContractError(state.StateContractError):
    """A bundle or private artifact violates the runner contract."""


def _strict_object(
    value: Any,
    *,
    allowed: Iterable[str],
    required: Iterable[str],
    path: str,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RunnerContractError(f"{path} must be an object")
    unknown = sorted(set(value) - set(allowed))
    missing = sorted(set(required) - set(value))
    if unknown:
        raise RunnerContractError(f"{path} has unsupported fields: {', '.join(unknown)}")
    if missing:
        raise RunnerContractError(f"{path} is missing fields: {', '.join(missing)}")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunnerContractError(f"{path} must be a non-empty string")
    if state.SENSITIVE_VALUE_RE.search(value):
        raise RunnerContractError(f"{path} contains a forbidden sensitive value")
    return value.strip()


def _date(value: Any, path: str) -> str:
    value = _text(value, path)
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise RunnerContractError(f"{path} must be a real YYYY-MM-DD date") from exc


def _timestamp(value: Any, path: str) -> str:
    value = _text(value, path)
    if "T" not in value:
        raise RunnerContractError(f"{path} must be RFC3339")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RunnerContractError(f"{path} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise RunnerContractError(f"{path} must include a timezone")
    return value


def _status(value: Any, path: str) -> str:
    value = _text(value, path)
    if value not in MODULE_STATUSES:
        raise RunnerContractError(f"{path} has unsupported status")
    return value


def _sha256(value: Any, path: str) -> str:
    value = _text(value, path)
    if not SHA256_RE.fullmatch(value):
        raise RunnerContractError(f"{path} must be a lowercase SHA-256 digest")
    return value


def _contract_for(source_contract_version: str, module: str) -> str:
    return f"{source_contract_version}:{module}"


def build_daily_plan(
    store: state.StateStore,
    *,
    review_date: str,
    plan_bytes: bytes,
    source_contract_version: str,
) -> Dict[str, Any]:
    market_date = _date(review_date, "review_date")
    if not plan_bytes.strip():
        raise RunnerContractError("plan file must not be empty")
    source_version = _text(source_contract_version, "source_contract_version")
    modules: Dict[str, Any] = {
        name: {"action": "refresh", "reason": "current_fact_changes_each_run"}
        for name in CURRENT_REFRESH_MODULES
    }
    trade_contract = _contract_for(source_version, "trades")
    decision = store.partition_decision(
        "trades",
        market_date,
        market_date,
        trade_contract,
    )
    modules["trades"] = {
        "action": "cache_hit" if decision == "cache_hit" else "read",
        "reason": "complete_or_success_empty_partition" if decision == "cache_hit" else "missing_or_non_success_partition",
    }
    if decision == "cache_hit":
        cached_partition = store.get_trade_partition_snapshot(
            market_date,
            market_date,
            trade_contract,
        )
        if cached_partition is None or cached_partition["status"] not in state.REUSABLE_STATUSES:
            raise RunnerContractError("trade cache decision changed while building the daily plan")
        modules["trades"]["cached_partition"] = cached_partition
    return {
        "schema_version": PLAN_SCHEMA,
        "mode": "daily",
        "review_date": market_date,
        "source_contract_version": source_version,
        "plan_hash": hashlib.sha256(plan_bytes).hexdigest(),
        "modules": {name: modules[name] for name in DAILY_MODULES},
    }


def build_weekly_plan(
    store: state.StateStore,
    *,
    expected_trade_dates: Sequence[str],
    source_contract_version: str,
) -> Dict[str, Any]:
    if not expected_trade_dates:
        raise RunnerContractError("expected_trade_dates must not be empty")
    dates = [_date(value, "expected_trade_dates") for value in expected_trade_dates]
    if len(dates) != len(set(dates)):
        raise RunnerContractError("expected_trade_dates must be unique")
    source_version = _text(source_contract_version, "source_contract_version")
    trade_dates = {
        market_date: (
            "cache_hit"
            if store.partition_decision(
                "trades",
                market_date,
                market_date,
                _contract_for(source_version, "trades"),
            )
            == "cache_hit"
            else "read"
        )
        for market_date in dates
    }
    # New weekly runs assess plan execution, not broker P&L. Raw executions
    # are needed only for rules that cannot be verified from cached aggregates.
    weekly_modules = {
        name: {
            "action": "read_if_authorized",
            "cache_policy": "ephemeral_privacy_boundary",
        }
        for name in ("execution_rule_evidence", "current_positions")
    }
    weekly_modules["confirmed_plan_versions"] = {
        "action": "read_local_confirmed_versions",
        "cache_policy": "immutable_version",
    }
    return {
        "schema_version": PLAN_SCHEMA,
        "mode": "weekly",
        "expected_trade_dates": dates,
        "source_contract_version": source_version,
        "trade_dates": trade_dates,
        "weekly_modules": weekly_modules,
    }


def _preflight_daily_facts(bundle: Any) -> Dict[str, Any]:
    top_keys = {
        "schema_version",
        "run_id",
        "mode",
        "review_date",
        "generated_at",
        "source_contract_version",
        "plan_hash",
        "analysis_contract_version",
        "modules",
    }
    root = _strict_object(bundle, allowed=top_keys, required=top_keys, path="$")
    if root["schema_version"] != FACTS_INPUT_SCHEMA:
        raise RunnerContractError("unsupported incremental facts schema")
    if root["mode"] != "daily":
        raise RunnerContractError("daily ingestion requires mode=daily")
    review_date = _date(root["review_date"], "$.review_date")
    generated_at = _timestamp(root["generated_at"], "$.generated_at")
    source_version = _text(root["source_contract_version"], "$.source_contract_version")
    plan_hash = _sha256(root["plan_hash"], "$.plan_hash")
    analysis_version = _text(root["analysis_contract_version"], "$.analysis_contract_version")
    run_id = _text(root["run_id"], "$.run_id")

    modules = _strict_object(
        root["modules"],
        allowed=set(DAILY_MODULES),
        required=set(DAILY_MODULES),
        path="$.modules",
    )
    normalized_modules: Dict[str, Any] = {}
    for name in DAILY_MODULES:
        module = _strict_object(
            modules[name],
            allowed={"status", "collected_at", "payload", "error_category"},
            required={"status", "collected_at", "payload"},
            path=f"$.modules.{name}",
        )
        module_status = _status(module["status"], f"$.modules.{name}.status")
        error_category = module.get("error_category")
        if error_category is not None:
            error_category = _text(error_category, f"$.modules.{name}.error_category")
        if module_status in {"partial", "stale", "blocked"} and error_category is None:
            raise RunnerContractError(
                f"$.modules.{name} non-success status requires error_category"
            )
        normalized_payload = state.validate_partition_payload(name, module["payload"], module_status)
        normalized_modules[name] = {
            "status": module_status,
            "collected_at": _timestamp(module["collected_at"], f"$.modules.{name}.collected_at"),
            "payload": normalized_payload,
            "error_category": error_category,
        }

    return {
        "run_id": run_id,
        "review_date": review_date,
        "generated_at": generated_at,
        "source_contract_version": source_version,
        "plan_hash": plan_hash,
        "analysis_contract_version": analysis_version,
        "modules": normalized_modules,
    }


def _preflight_daily_bundle(bundle: Any) -> Dict[str, Any]:
    top_keys = {
        "schema_version",
        "run_id",
        "mode",
        "review_date",
        "generated_at",
        "source_contract_version",
        "plan_hash",
        "analysis_contract_version",
        "modules",
        "analysis",
    }
    root = _strict_object(bundle, allowed=top_keys, required=top_keys, path="$")
    if root["schema_version"] != INPUT_SCHEMA:
        raise RunnerContractError("unsupported incremental input schema")
    facts_bundle = {key: value for key, value in root.items() if key != "analysis"}
    facts_bundle["schema_version"] = FACTS_INPUT_SCHEMA
    normalized = _preflight_daily_facts(facts_bundle)

    analysis = _strict_object(
        root["analysis"],
        allowed={"status", "model", "generated_at", "output"},
        required={"status", "model", "generated_at", "output"},
        path="$.analysis",
    )
    normalized["analysis"] = {
        "status": _status(analysis["status"], "$.analysis.status"),
        "model": _text(analysis["model"], "$.analysis.model"),
        "generated_at": _timestamp(analysis["generated_at"], "$.analysis.generated_at"),
        "output": state._normalize_analysis(analysis["output"]),
    }
    return normalized


def _overall_status(statuses: Sequence[str]) -> str:
    if any(value == "blocked" for value in statuses):
        return "blocked"
    if any(value in {"partial", "stale"} for value in statuses):
        return "partial"
    if statuses and all(value == "empty" for value in statuses):
        return "empty"
    return "complete"


def _facts_hash(validated_modules: Mapping[str, Any]) -> str:
    fingerprint = {
        name: {
            "payload_hash": state.content_hash(validated_modules[name]["payload"]),
            "status": validated_modules[name]["status"],
            "error_category": validated_modules[name]["error_category"],
        }
        for name in DAILY_MODULES
    }
    return state.content_hash(fingerprint)


def build_daily_analysis_plan(
    store: state.StateStore,
    facts_bundle: Any,
) -> Dict[str, Any]:
    validated = _preflight_daily_facts(facts_bundle)
    facts_hash = _facts_hash(validated["modules"])
    snapshot = store.get_analysis_snapshot(
        facts_hash,
        validated["plan_hash"],
        validated["analysis_contract_version"],
    )
    return {
        "schema_version": ANALYSIS_PLAN_SCHEMA,
        "action": "reuse" if snapshot is not None else "run_codex",
        "facts_hash": facts_hash,
        "plan_hash": validated["plan_hash"],
        "analysis_contract_version": validated["analysis_contract_version"],
        "cached_analysis": snapshot,
    }


def process_daily_bundle(store: state.StateStore, bundle: Any) -> Dict[str, Any]:
    validated = _preflight_daily_bundle(bundle)
    module_statuses = [validated["modules"][name]["status"] for name in DAILY_MODULES]
    data_status = _overall_status(module_statuses)
    facts_hash = _facts_hash(validated["modules"])
    analysis = validated["analysis"]
    cached_analysis = store.get_analysis_snapshot(
        facts_hash,
        validated["plan_hash"],
        validated["analysis_contract_version"],
    )
    if cached_analysis is not None and cached_analysis != analysis:
        raise RunnerContractError(
            "analysis cache hit must reuse the original model, status, generated_at, and output"
        )
    store.start_run(
        run_id=validated["run_id"],
        mode="daily",
        period_start=validated["review_date"],
        period_end=validated["review_date"],
        started_at=validated["generated_at"],
        data_status=data_status,
        source_contract_version=validated["source_contract_version"],
    )

    results: Dict[str, Any] = {}
    for name in DAILY_MODULES:
        module = validated["modules"][name]
        result = store.ingest_partition(
            dataset=name,
            period_start=validated["review_date"],
            period_end=validated["review_date"],
            contract_version=_contract_for(validated["source_contract_version"], name),
            status=module["status"],
            collected_at=module["collected_at"],
            payload=module["payload"],
            error_category=module["error_category"],
        )
        results[name] = {
            "action": result.action,
            "status": result.status,
            "revision": result.revision,
            "collected_at": module["collected_at"],
        }
    analysis_cache = store.put_analysis(
        facts_hash,
        validated["plan_hash"],
        validated["analysis_contract_version"],
        analysis["output"],
        analysis["model"],
        analysis["generated_at"],
        analysis["status"],
    )
    store.finish_run(validated["run_id"], validated["generated_at"], data_status)
    return {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": validated["run_id"],
        "mode": "daily",
        "review_period": {"start": validated["review_date"], "end": validated["review_date"]},
        "generated_at": validated["generated_at"],
        "data_status": data_status,
        "confirmation_status": "pending",
        "source_contract_version": validated["source_contract_version"],
        "db_schema_version": state.SCHEMA_VERSION,
        "modules": results,
        "facts_hash": facts_hash,
        "plan_hash": validated["plan_hash"],
        "analysis_contract_version": validated["analysis_contract_version"],
        "analysis_cache": analysis_cache,
        "partition_counts": {
            "written": sum(value["action"] == "written" for value in results.values()),
            "reused": sum(value["action"] == "reused" for value in results.values()),
        },
        "artifacts": [],
    }


def process_weekly_bundle(store: state.StateStore, bundle: Any) -> Dict[str, Any]:
    """Persist one already-sanitized weekly projection and emit lineage only."""

    validated = state.normalize_weekly_review_bundle(bundle)
    if validated["schema_version"] != WEEKLY_STATE_SCHEMA:
        raise RunnerContractError("unsupported weekly state schema")
    run = store.get_run(validated["run_id"])
    if run is None:
        raise RunnerContractError("weekly ingestion requires an existing collection run")
    result = store.ingest_weekly_review(bundle)
    store.finish_run(validated["run_id"], validated["generated_at"], validated["data_status"])
    freshness = store.weekly_review_freshness(validated["review_key"], result.revision)
    modules = {row["name"]: row["status"] for row in validated["modules"]}
    return {
        "schema_version": WEEKLY_MANIFEST_SCHEMA,
        "run_id": validated["run_id"],
        "mode": "weekly",
        "review_key": validated["review_key"],
        "review_revision": result.revision,
        "action": result.action,
        "review_period": {
            "start": validated["period_start"],
            "end": validated["period_end"],
        },
        "generated_at": validated["generated_at"],
        "data_status": result.status,
        "confirmation_status": "pending",
        "source_contract_version": validated["source_contract_version"],
        "db_schema_version": state.SCHEMA_VERSION,
        "modules": modules,
        "facts_hash": result.facts_hash,
        "dependency_hash": result.dependency_hash,
        "freshness": {
            "status": freshness["status"],
            "changed_dependency_count": len(freshness["changed_dependencies"]),
        },
        "artifacts": [],
    }


def build_weekly_dashboard_packet(store: state.StateStore, review_key: str) -> Dict[str, Any]:
    """Read the latest immutable weekly revision and export only UI-safe fields."""

    key = _text(review_key, "review_key")
    if not key.startswith("weekly:"):
        raise RunnerContractError("weekly dashboard review_key must start with weekly:")
    review = store.get_weekly_review(key)
    if review is None:
        raise RunnerContractError("weekly review does not exist")
    # Imported lazily so planning and ingestion do not depend on the renderer.
    import render_trade_review_dashboard_v2 as dashboard

    return dashboard.build_weekly_packet(review)


def _private_path(path: Path, label: str, *, require_existing: bool = False) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise RunnerContractError(f"{label} must be an absolute non-symlink path")
    lexical = expanded.absolute()
    try:
        relative = lexical.relative_to(PRIVATE_ROOT)
    except ValueError as exc:
        raise RunnerContractError(f"{label} must be below {PRIVATE_ROOT}") from exc
    if PRIVATE_ROOT.exists():
        root_info = PRIVATE_ROOT.stat()
        if (
            PRIVATE_ROOT.is_symlink()
            or not PRIVATE_ROOT.is_dir()
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise RunnerContractError("private runtime root must be current-user 0700")
    probe = PRIVATE_ROOT
    for index, part in enumerate(relative.parts):
        probe = probe / part
        if (probe.exists() or probe.is_symlink()) and probe.is_symlink():
            raise RunnerContractError(f"{label} must not traverse a symbolic link")
        if probe.exists() and index < len(relative.parts) - 1:
            if not probe.is_dir():
                raise RunnerContractError(f"{label} parent must be a directory")
            info = probe.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise RunnerContractError(f"{label} parent directories must be current-user 0700")
    resolved = expanded.resolve(strict=False)
    try:
        resolved.relative_to(PRIVATE_ROOT)
    except ValueError as exc:
        raise RunnerContractError(f"{label} must be below {PRIVATE_ROOT}") from exc
    if resolved == PRIVATE_ROOT:
        raise RunnerContractError(f"{label} must name a file")
    if resolved.exists():
        if not resolved.is_file():
            raise RunnerContractError(f"{label} must name a regular file")
        info = resolved.stat()
        if info.st_uid != os.getuid():
            raise RunnerContractError(f"{label} must be owned by the current user")
        if resolved.is_file() and stat.S_IMODE(info.st_mode) != 0o600:
            raise RunnerContractError(f"{label} must use mode 0600")
    if require_existing:
        if not resolved.is_file():
            raise RunnerContractError(f"{label} does not exist")
    return resolved


def _ensure_private_parent(destination: Path) -> None:
    relative_parent = destination.parent.relative_to(PRIVATE_ROOT)
    probe = PRIVATE_ROOT
    if not probe.exists():
        probe.mkdir(mode=0o700)
    if probe.is_symlink() or not probe.is_dir():
        raise RunnerContractError("private runtime root must be a real directory")
    root_info = probe.stat()
    if root_info.st_uid != os.getuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
        raise RunnerContractError("private runtime root must be current-user 0700")
    for part in relative_parent.parts:
        probe = probe / part
        if not probe.exists():
            probe.mkdir(mode=0o700)
        if probe.is_symlink() or not probe.is_dir():
            raise RunnerContractError("private output parent must be a real directory")
        info = probe.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RunnerContractError("private output directories must be current-user 0700")


def validate_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    top_keys = {
        "schema_version",
        "run_id",
        "mode",
        "review_period",
        "generated_at",
        "data_status",
        "confirmation_status",
        "source_contract_version",
        "db_schema_version",
        "modules",
        "facts_hash",
        "plan_hash",
        "analysis_contract_version",
        "analysis_cache",
        "partition_counts",
        "artifacts",
    }
    root = _strict_object(manifest, allowed=top_keys, required=top_keys, path="$manifest")
    if root["schema_version"] != MANIFEST_SCHEMA:
        raise RunnerContractError("manifest schema mismatch")
    if root["mode"] != "daily":
        raise RunnerContractError("manifest mode must be daily")

    review_period = _strict_object(
        root["review_period"],
        allowed={"start", "end"},
        required={"start", "end"},
        path="$manifest.review_period",
    )
    period_start = _date(review_period["start"], "$manifest.review_period.start")
    period_end = _date(review_period["end"], "$manifest.review_period.end")
    if period_start != period_end:
        raise RunnerContractError("daily manifest review period must cover exactly one date")

    modules = _strict_object(
        root["modules"],
        allowed=set(DAILY_MODULES),
        required=set(DAILY_MODULES),
        path="$manifest.modules",
    )
    normalized_modules: Dict[str, Any] = {}
    for name in DAILY_MODULES:
        module = _strict_object(
            modules[name],
            allowed={"action", "status", "revision", "collected_at"},
            required={"action", "status", "revision", "collected_at"},
            path=f"$manifest.modules.{name}",
        )
        action = _text(module["action"], f"$manifest.modules.{name}.action")
        if action not in {"written", "reused"}:
            raise RunnerContractError(f"$manifest.modules.{name}.action is unsupported")
        revision = module["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise RunnerContractError(f"$manifest.modules.{name}.revision must be a positive integer")
        normalized_modules[name] = {
            "action": action,
            "status": _status(module["status"], f"$manifest.modules.{name}.status"),
            "revision": revision,
            "collected_at": _timestamp(
                module["collected_at"], f"$manifest.modules.{name}.collected_at"
            ),
        }

    data_status = _status(root["data_status"], "$manifest.data_status")
    expected_status = _overall_status(
        [normalized_modules[name]["status"] for name in DAILY_MODULES]
    )
    if data_status != expected_status:
        raise RunnerContractError("manifest data_status does not match module statuses")
    confirmation_status = _text(
        root["confirmation_status"], "$manifest.confirmation_status"
    )
    if confirmation_status not in state.CONFIRMATION_STATUSES:
        raise RunnerContractError("manifest confirmation_status is unsupported")

    db_schema_version = root["db_schema_version"]
    if (
        isinstance(db_schema_version, bool)
        or not isinstance(db_schema_version, int)
        or db_schema_version != state.SCHEMA_VERSION
    ):
        raise RunnerContractError("manifest db_schema_version mismatch")
    analysis_cache = _text(root["analysis_cache"], "$manifest.analysis_cache")
    if analysis_cache not in {"written", "reused"}:
        raise RunnerContractError("manifest analysis_cache is unsupported")

    partition_counts = _strict_object(
        root["partition_counts"],
        allowed={"written", "reused"},
        required={"written", "reused"},
        path="$manifest.partition_counts",
    )
    normalized_counts: Dict[str, int] = {}
    for action in ("written", "reused"):
        count = partition_counts[action]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RunnerContractError(
                f"$manifest.partition_counts.{action} must be a non-negative integer"
            )
        expected_count = sum(
            module["action"] == action for module in normalized_modules.values()
        )
        if count != expected_count:
            raise RunnerContractError(
                f"$manifest.partition_counts.{action} does not match module actions"
            )
        normalized_counts[action] = count

    artifacts = root["artifacts"]
    if not isinstance(artifacts, list):
        raise RunnerContractError("$manifest.artifacts must be an array")
    normalized_artifacts = [
        _text(value, f"$manifest.artifacts[{index}]")
        for index, value in enumerate(artifacts)
    ]

    return {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": _text(root["run_id"], "$manifest.run_id"),
        "mode": "daily",
        "review_period": {"start": period_start, "end": period_end},
        "generated_at": _timestamp(root["generated_at"], "$manifest.generated_at"),
        "data_status": data_status,
        "confirmation_status": confirmation_status,
        "source_contract_version": _text(
            root["source_contract_version"], "$manifest.source_contract_version"
        ),
        "db_schema_version": db_schema_version,
        "modules": normalized_modules,
        "facts_hash": _sha256(root["facts_hash"], "$manifest.facts_hash"),
        "plan_hash": _sha256(root["plan_hash"], "$manifest.plan_hash"),
        "analysis_contract_version": _text(
            root["analysis_contract_version"], "$manifest.analysis_contract_version"
        ),
        "analysis_cache": analysis_cache,
        "partition_counts": normalized_counts,
        "artifacts": normalized_artifacts,
    }


def validate_weekly_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    top_keys = {
        "schema_version",
        "run_id",
        "mode",
        "review_key",
        "review_revision",
        "action",
        "review_period",
        "generated_at",
        "data_status",
        "confirmation_status",
        "source_contract_version",
        "db_schema_version",
        "modules",
        "facts_hash",
        "dependency_hash",
        "freshness",
        "artifacts",
    }
    root = _strict_object(manifest, allowed=top_keys, required=top_keys, path="$weekly_manifest")
    if root["schema_version"] != WEEKLY_MANIFEST_SCHEMA or root["mode"] != "weekly":
        raise RunnerContractError("weekly manifest schema or mode mismatch")
    review_key = _text(root["review_key"], "$weekly_manifest.review_key")
    if not review_key.startswith("weekly:"):
        raise RunnerContractError("weekly manifest review_key must start with weekly:")
    revision = root["review_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RunnerContractError("weekly manifest review_revision must be positive")
    action = _text(root["action"], "$weekly_manifest.action")
    if action not in {"written", "reused"}:
        raise RunnerContractError("weekly manifest action is unsupported")

    period = _strict_object(
        root["review_period"],
        allowed={"start", "end"},
        required={"start", "end"},
        path="$weekly_manifest.review_period",
    )
    period_start = _date(period["start"], "$weekly_manifest.review_period.start")
    period_end = _date(period["end"], "$weekly_manifest.review_period.end")
    if period_start > period_end:
        raise RunnerContractError("weekly manifest period is reversed")

    modules = _strict_object(
        root["modules"],
        allowed=set(state.WEEKLY_MODULES),
        required=set(state.WEEKLY_MODULES),
        path="$weekly_manifest.modules",
    )
    normalized_modules = {
        name: _status(modules[name], f"$weekly_manifest.modules.{name}")
        for name in sorted(state.WEEKLY_MODULES)
    }
    data_status = _status(root["data_status"], "$weekly_manifest.data_status")
    if data_status == "complete" and any(
        value not in {"complete", "empty"} for value in normalized_modules.values()
    ):
        raise RunnerContractError("complete weekly manifest conflicts with module status")
    if data_status == "partial" and all(
        value in {"complete", "empty"} for value in normalized_modules.values()
    ):
        raise RunnerContractError("partial weekly manifest requires a non-success module")
    if data_status == "blocked" and "blocked" not in normalized_modules.values():
        raise RunnerContractError("blocked weekly manifest requires a blocked module")
    if root["confirmation_status"] != "pending":
        raise RunnerContractError("new weekly manifest confirmation must remain pending")

    db_schema_version = root["db_schema_version"]
    if isinstance(db_schema_version, bool) or db_schema_version != state.SCHEMA_VERSION:
        raise RunnerContractError("weekly manifest db_schema_version mismatch")
    freshness = _strict_object(
        root["freshness"],
        allowed={"status", "changed_dependency_count"},
        required={"status", "changed_dependency_count"},
        path="$weekly_manifest.freshness",
    )
    freshness_status = _text(freshness["status"], "$weekly_manifest.freshness.status")
    if freshness_status not in {"current", "stale"}:
        raise RunnerContractError("weekly manifest freshness is unsupported")
    changed_count = freshness["changed_dependency_count"]
    if isinstance(changed_count, bool) or not isinstance(changed_count, int) or changed_count < 0:
        raise RunnerContractError("weekly manifest changed_dependency_count must be non-negative")
    if (freshness_status == "current") != (changed_count == 0):
        raise RunnerContractError("weekly manifest freshness count is inconsistent")
    artifacts = root["artifacts"]
    if not isinstance(artifacts, list):
        raise RunnerContractError("weekly manifest artifacts must be an array")
    return {
        "schema_version": WEEKLY_MANIFEST_SCHEMA,
        "run_id": _text(root["run_id"], "$weekly_manifest.run_id"),
        "mode": "weekly",
        "review_key": review_key,
        "review_revision": revision,
        "action": action,
        "review_period": {"start": period_start, "end": period_end},
        "generated_at": _timestamp(root["generated_at"], "$weekly_manifest.generated_at"),
        "data_status": data_status,
        "confirmation_status": "pending",
        "source_contract_version": _text(
            root["source_contract_version"], "$weekly_manifest.source_contract_version"
        ),
        "db_schema_version": state.SCHEMA_VERSION,
        "modules": normalized_modules,
        "facts_hash": _sha256(root["facts_hash"], "$weekly_manifest.facts_hash"),
        "dependency_hash": _sha256(
            root["dependency_hash"], "$weekly_manifest.dependency_hash"
        ),
        "freshness": {
            "status": freshness_status,
            "changed_dependency_count": changed_count,
        },
        "artifacts": [
            _text(value, f"$weekly_manifest.artifacts[{index}]")
            for index, value in enumerate(artifacts)
        ],
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    normalized = validate_manifest(manifest)
    destination = _private_path(path, "manifest")
    _ensure_private_parent(destination)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
        os.replace(temporary_name, destination)
        os.chmod(destination, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            Path(temporary_name).unlink()
        except OSError:
            pass
        raise


def write_weekly_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    normalized = validate_weekly_manifest(manifest)
    _write_private_json(path, normalized)


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    destination = _private_path(path, "output")
    _ensure_private_parent(destination)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary_name, destination)
        os.chmod(destination, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            Path(temporary_name).unlink()
        except OSError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("daily-plan", help="Plan current refreshes and trade cache usage")
    daily.add_argument("--review-date", required=True)
    daily.add_argument("--plan-file", required=True, type=Path)
    daily.add_argument("--source-contract-version", required=True)
    daily.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    daily.add_argument("--output", required=True, type=Path)

    ingest = subparsers.add_parser("ingest-daily", help="Ingest one sanitized daily bundle")
    ingest.add_argument("--input", required=True, type=Path)
    ingest.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    ingest.add_argument("--manifest", required=True, type=Path)

    weekly_ingest = subparsers.add_parser(
        "ingest-weekly",
        help="Ingest one fixed sanitized weekly state projection",
    )
    weekly_ingest.add_argument("--input", required=True, type=Path)
    weekly_ingest.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    weekly_ingest.add_argument("--manifest", required=True, type=Path)

    analysis = subparsers.add_parser(
        "daily-analysis-plan",
        help="Check the three-part analysis cache before invoking Codex",
    )
    analysis.add_argument("--input", required=True, type=Path)
    analysis.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    analysis.add_argument("--output", required=True, type=Path)

    weekly = subparsers.add_parser("weekly-plan", help="Plan per-day reuse and weekly private reads")
    weekly.add_argument("--expected-trade-dates", required=True)
    weekly.add_argument("--source-contract-version", required=True)
    weekly.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    weekly.add_argument("--output", required=True, type=Path)

    aggregate = subparsers.add_parser("weekly-aggregate", help="Aggregate cached verified daily trade facts")
    aggregate.add_argument("--expected-trade-dates", required=True)
    aggregate.add_argument("--source-contract-version", required=True)
    aggregate.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    aggregate.add_argument("--output", required=True, type=Path)

    weekly_dashboard = subparsers.add_parser(
        "weekly-dashboard-packet",
        help="Export the latest persisted weekly revision as a UI-safe packet",
    )
    weekly_dashboard.add_argument("--review-key", required=True)
    weekly_dashboard.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    weekly_dashboard.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan_bytes: Optional[bytes] = None
        bundle: Any = None
        dates: List[str] = []
        output_path: Optional[Path] = None
        manifest_path: Optional[Path] = None
        review_date: Optional[str] = None
        source_version: Optional[str] = None
        review_key: Optional[str] = None

        # Validate and read private inputs/outputs before opening or creating
        # the persistent database. A bad path or malformed JSON must not cause
        # a state transition.
        if args.command == "daily-plan":
            plan_bytes = args.plan_file.read_bytes()
            if not plan_bytes.strip():
                raise RunnerContractError("plan file must not be empty")
            review_date = _date(args.review_date, "review_date")
            source_version = _text(args.source_contract_version, "source_contract_version")
            output_path = _private_path(args.output, "output")
        elif args.command == "ingest-daily":
            input_path = _private_path(args.input, "input", require_existing=True)
            bundle = json.loads(input_path.read_text(encoding="utf-8"))
            _preflight_daily_bundle(bundle)
            manifest_path = _private_path(args.manifest, "manifest")
        elif args.command == "ingest-weekly":
            input_path = _private_path(args.input, "input", require_existing=True)
            bundle = json.loads(input_path.read_text(encoding="utf-8"))
            normalized_weekly = state.normalize_weekly_review_bundle(bundle)
            if normalized_weekly["schema_version"] != WEEKLY_STATE_SCHEMA:
                raise RunnerContractError("new weekly runs require the plan-execution v2 contract")
            manifest_path = _private_path(args.manifest, "manifest")
        elif args.command == "daily-analysis-plan":
            input_path = _private_path(args.input, "input", require_existing=True)
            bundle = json.loads(input_path.read_text(encoding="utf-8"))
            _preflight_daily_facts(bundle)
            output_path = _private_path(args.output, "output")
        elif args.command in {"weekly-plan", "weekly-aggregate"}:
            dates = [
                value.strip()
                for value in args.expected_trade_dates.split(",")
                if value.strip()
            ]
            if not dates:
                raise RunnerContractError("expected_trade_dates must not be empty")
            dates = [_date(value, "expected_trade_dates") for value in dates]
            if len(dates) != len(set(dates)):
                raise RunnerContractError("expected_trade_dates must be unique")
            source_version = _text(args.source_contract_version, "source_contract_version")
            output_path = _private_path(args.output, "output")
        elif args.command == "weekly-dashboard-packet":
            review_key = _text(args.review_key, "review_key")
            if not review_key.startswith("weekly:"):
                raise RunnerContractError("weekly dashboard review_key must start with weekly:")
            output_path = _private_path(args.output, "output")
        else:
            raise RunnerContractError("unsupported command")

        with state.open_state_store(args.state_db) as store:
            if args.command == "daily-plan":
                if (
                    plan_bytes is None
                    or output_path is None
                    or review_date is None
                    or source_version is None
                ):
                    raise RunnerContractError("daily plan preflight did not complete")
                plan = build_daily_plan(
                    store,
                    review_date=review_date,
                    plan_bytes=plan_bytes,
                    source_contract_version=source_version,
                )
                _write_private_json(output_path, plan)
            elif args.command == "ingest-daily":
                if manifest_path is None:
                    raise RunnerContractError("daily ingest preflight did not complete")
                manifest = process_daily_bundle(store, bundle)
                write_manifest(manifest_path, manifest)
            elif args.command == "ingest-weekly":
                if manifest_path is None:
                    raise RunnerContractError("weekly ingest preflight did not complete")
                manifest = process_weekly_bundle(store, bundle)
                write_weekly_manifest(manifest_path, manifest)
            elif args.command == "daily-analysis-plan":
                if output_path is None:
                    raise RunnerContractError("analysis plan preflight did not complete")
                plan = build_daily_analysis_plan(store, bundle)
                _write_private_json(output_path, plan)
            elif args.command == "weekly-plan":
                if output_path is None or source_version is None:
                    raise RunnerContractError("weekly plan preflight did not complete")
                plan = build_weekly_plan(
                    store,
                    expected_trade_dates=dates,
                    source_contract_version=source_version,
                )
                _write_private_json(output_path, plan)
            elif args.command == "weekly-aggregate":
                if output_path is None or source_version is None:
                    raise RunnerContractError("weekly aggregate preflight did not complete")
                aggregate = store.aggregate_weekly_trades(
                    dates,
                    _contract_for(source_version, "trades"),
                )
                _write_private_json(output_path, aggregate)
            elif args.command == "weekly-dashboard-packet":
                if output_path is None or review_key is None:
                    raise RunnerContractError("weekly dashboard preflight did not complete")
                packet = build_weekly_dashboard_packet(store, review_key)
                _write_private_json(output_path, packet)
            else:
                raise RunnerContractError("unsupported command")
    except (OSError, UnicodeError, json.JSONDecodeError, state.StateStoreError, RunnerContractError):
        print(json.dumps({"status": "blocked", "error_category": "state_or_contract_failure"}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", "command": args.command}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
