#!/usr/bin/env python3
"""Owner-only SQLite state for incremental trading reviews.

The module accepts only fixed, sanitized projections. It never stores broker
identifiers, raw responses, credentials, costs, commissions, or generic
metadata payloads.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, NamedTuple, Optional, Sequence
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 3
DEFAULT_STATE_DB = (
    Path.home()
    / "Library"
    / "Application Support"
    / "MarsTradingCenter"
    / "trading-review.sqlite3"
)
DEFAULT_VAULT_ROOT = Path.home() / "Documents" / "ChatGPT" / "个人知识中心"
DATA_STATUSES = frozenset({"complete", "partial", "empty", "stale", "blocked"})
CONFIRMATION_STATUSES = frozenset({"pending", "confirmed"})
REUSABLE_STATUSES = frozenset({"complete", "empty"})
WEEKLY_MODULES = frozenset(
    {"trades", "performance", "attribution", "cash_flow", "positions", "market", "events", "plan"}
)
WEEKLY_CASH_CATEGORIES = frozenset(
    {
        "stock_buy",
        "stock_sell",
        "option_buy",
        "option_sell",
        "fx_debit",
        "fx_credit",
        "ipo_financing",
        "ipo_subscription",
        "ipo_allotment",
    }
)
WEEKLY_ITEM_KINDS = frozenset(
    {"plan_actual", "discipline", "retain", "delete", "rewrite", "add", "risk", "gap"}
)
WEEKLY_EVIDENCE_KINDS = frozenset({"fact", "interpretation", "draft", "gap"})
PLAN_STAGES = frozenset({"pre_entry", "position_management"})
PLAN_STATUSES = frozenset({"draft", "confirmed", "expired"})
PLAN_SETUPS = frozenset({"pullback", "breakout", "range", "bottom_reversal", "position_management"})
PLAN_ZONE_KINDS = frozenset({"observation", "entry", "add", "reduce", "exit", "invalidation"})
EPISODE_COVERAGE = frozenset({"covered", "uncovered"})
EPISODE_COMPLIANCE = frozenset({"compliant", "non_compliant", "unassessable"})
EPISODE_OUTCOMES = frozenset({"success", "failure", "open", "flat", "unverifiable"})
SCHEMA_TABLES = (
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
)
EXPECTED_COLUMNS = {
    "schema_meta": ("singleton", "schema_version", "created_at", "migrated_at"),
    "runs": (
        "run_id", "mode", "period_start", "period_end", "started_at",
        "finished_at", "data_status", "confirmation_status", "source_contract_version",
    ),
    "partitions": (
        "dataset", "period_start", "period_end", "contract_version", "revision",
        "status", "collected_at", "payload_hash", "error_category", "supersedes_revision",
    ),
    "account_snapshots": (
        "snapshot_at", "revision", "currency", "net_assets", "cash", "buying_power", "data_status",
    ),
    "position_snapshots": (
        "snapshot_at", "symbol", "revision", "underlying", "instrument_type", "quantity", "data_status",
    ),
    "trade_aggregates": (
        "market_date", "symbol", "side", "revision", "order_count",
        "execution_count", "executed_quantity", "data_status",
    ),
    "market_snapshots": (
        "as_of", "symbol", "revision", "value", "previous_close",
        "change_pct", "session", "proxy_for", "data_status",
    ),
    "relevant_events": (
        "derived_event_key", "revision", "et_at", "shanghai_at", "title",
        "status", "source_category", "impact_channel", "data_status",
    ),
    "analysis_snapshots": (
        "facts_hash", "plan_hash", "contract_version", "output_json",
        "model", "generated_at", "data_status",
    ),
    "confirmations": (
        "review_key", "confirmation_version", "confirmation_status",
        "confirmed_at", "facts_hash", "supersedes_version",
    ),
    "weekly_reviews": (
        "review_key", "revision", "run_id", "period_start", "period_end",
        "generated_at", "source_contract_version", "facts_hash", "plan_hash",
        "dependency_hash", "data_status", "supersedes_revision",
    ),
    "weekly_review_dependencies": (
        "review_key", "review_revision", "dataset", "period_start", "period_end",
        "contract_version", "partition_revision", "payload_hash",
    ),
    "weekly_module_statuses": (
        "review_key", "review_revision", "module_name", "data_status",
        "requested_start", "requested_end", "returned_start", "returned_end",
        "error_category",
    ),
    "weekly_performance": (
        "review_key", "review_revision", "currency", "initial_asset_value",
        "ending_asset_value", "profit", "profit_rate", "time_weighted_return",
        "invest_amount", "mechanical_asset_change", "reconciliation_residual",
        "requested_utc_start", "requested_utc_end", "returned_utc_start",
        "returned_utc_end", "data_status",
    ),
    "weekly_attributions": (
        "review_key", "review_revision", "underlying", "instrument_group",
        "display_name", "profit", "underlying_profit", "derivatives_profit",
        "currency", "data_status",
    ),
    "weekly_cash_flow_aggregates": (
        "review_key", "review_revision", "category", "currency", "amount",
        "row_count", "data_status",
    ),
    "weekly_review_items": (
        "review_key", "review_revision", "item_index", "item_kind", "subject",
        "summary", "evidence_boundary", "evidence_kind", "data_status",
    ),
    "plan_versions": (
        "plan_id", "version", "plan_stage", "underlying", "direction",
        "setup_type", "plan_status", "generated_at", "effective_at",
        "confirmed_at", "expires_at", "evidence_id", "evidence_source",
        "evidence_as_of", "evidence_timezone", "adjustment", "bars_used",
        "atr14", "minimum_reward_risk", "max_invalidation_pct", "content_hash",
        "supersedes_version", "parent_plan_id", "parent_plan_version",
        "initial_buy_episode_key", "data_status",
    ),
    "plan_zones": (
        "plan_id", "plan_version", "zone_order", "zone_kind", "low", "high",
        "currency", "condition", "derived_from", "data_status",
    ),
    "trade_episode_assessments": (
        "review_key", "review_revision", "episode_index", "market_date",
        "underlying", "side", "plan_id", "plan_version", "coverage_status",
        "compliance_status", "outcome_status", "deviation_type", "reason",
        "next_rule", "data_status",
    ),
    "weekly_execution_metrics": (
        "review_key", "review_revision", "eligible_episode_count",
        "covered_episode_count", "assessable_episode_count",
        "compliant_episode_count", "resolved_episode_count",
        "successful_episode_count", "open_episode_count", "flat_episode_count",
        "unverifiable_episode_count", "review_needed_count", "coverage_rate",
        "execution_rate", "plan_win_rate", "data_status", "gap",
    ),
}

SENSITIVE_KEY_RE = re.compile(
    r"(?:account[_ -]?(?:id|no|number|identifier)|"
    r"order[_ -]?(?:id|no|number)|execution[_ -]?(?:id|no|number)|"
    r"trade[_ -]?(?:id|no|number)|request[_ -]?(?:id|no|number)|"
    r"client[_ -]?(?:id|no|number)|api[_ -]?key|cookie|password|"
    r"secret|credential|commission|cost|raw[_ -]?(?:response|json|payload)|"
    r"(?:access|refresh)[_ -]?token|strike|expiry|expiration)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?:authorization\s*[:=：＝]\s*\S+|bearer\s+\S+|"
    r"(?<![A-Za-z0-9_])(?:access|refresh)[_ -]?token(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])client[_ -]?secret(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])api[_ -]?key(?![A-Za-z0-9_])|"
    r"(?:账户编号|账户标识|订单\s*(?:id|号|编号)|成交\s*(?:id|号|编号)|凭据)"
    r"\s*[:=：＝]\s*\S+|sk-[A-Za-z0-9]{12,})",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OPTION_IDENTITY_VALUE_RE = re.compile(
    r"(?::OPTION\b|\b(?:strike|expiry|expiration)\b|行权价|到期日|"
    r"\b\d{4}-\d{2}-\d{2}\s+(?:call|put)\b)",
    re.IGNORECASE,
)


class StateStoreError(RuntimeError):
    """Base class for fail-closed state errors."""


class UnsafeStatePathError(StateStoreError):
    """The database path or permissions violate the owner-only contract."""


class StateContractError(StateStoreError):
    """A sanitized projection does not match its fixed contract."""


class StateBusyError(StateStoreError):
    """The single-writer database remained locked past the bounded timeout."""


class StateMigrationError(StateStoreError):
    """A schema migration failed and was rolled back."""


class PartitionResult(NamedTuple):
    action: str
    revision: int
    payload_hash: str
    status: str


class WeeklyReviewResult(NamedTuple):
    action: str
    revision: int
    facts_hash: str
    dependency_hash: str
    status: str


class PlanVersionResult(NamedTuple):
    action: str
    plan_id: str
    version: int
    content_hash: str
    status: str


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StateContractError("value is not canonical JSON") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(131072), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _existing_components(path: Path) -> Iterator[Path]:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() or current.is_symlink():
            yield current


def _inside_git_worktree(path: Path) -> bool:
    probe = path if path.is_dir() else path.parent
    for parent in (probe, *probe.parents):
        if (parent / ".git").exists():
            return True
    return False


def _inside_obsidian_vault(path: Path) -> bool:
    if _is_relative_to(path, DEFAULT_VAULT_ROOT.resolve()):
        return True
    probe = path if path.is_dir() else path.parent
    return any((parent / ".obsidian").exists() for parent in (probe, *probe.parents))


def _require_owner_mode(path: Path, expected_mode: int, label: str) -> None:
    info = path.stat()
    if info.st_uid != os.getuid():
        raise UnsafeStatePathError(f"{label} must be owned by the current user")
    mode = stat.S_IMODE(info.st_mode)
    if mode != expected_mode:
        raise UnsafeStatePathError(f"{label} must use mode {expected_mode:04o}, got {mode:04o}")


def validate_state_db_path(
    path: Path,
    *,
    test_root: Optional[Path] = None,
) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise UnsafeStatePathError("state database path must be absolute")
    resolved = expanded.resolve(strict=False)

    if test_root is not None:
        lexical_test_root = test_root.expanduser().absolute()
        try:
            relative_test_path = expanded.absolute().relative_to(lexical_test_root)
        except ValueError as exc:
            raise UnsafeStatePathError("test database must remain below the explicit test root") from exc
        lexical_probe = lexical_test_root
        for part in relative_test_path.parts:
            lexical_probe = lexical_probe / part
            if (lexical_probe.exists() or lexical_probe.is_symlink()) and lexical_probe.is_symlink():
                raise UnsafeStatePathError("state database path must not traverse a symbolic link")
        resolved_test_root = lexical_test_root.resolve(strict=True)
        if not _is_relative_to(resolved, resolved_test_root) or resolved == resolved_test_root:
            raise UnsafeStatePathError("test database must remain below the explicit test root")
        _require_owner_mode(resolved_test_root, 0o700, "test root")
    else:
        for component in _existing_components(expanded):
            if component.is_symlink():
                raise UnsafeStatePathError("state database path must not traverse a symbolic link")
        for temporary_root in (Path("/private/tmp"), Path("/tmp")):
            if _is_relative_to(resolved, temporary_root):
                raise UnsafeStatePathError("persistent state database must be outside temporary directories")
        if _inside_git_worktree(resolved):
            raise UnsafeStatePathError("state database must be outside every Git worktree")
        if _inside_obsidian_vault(resolved):
            raise UnsafeStatePathError("state database must be outside every Obsidian Vault")

    if resolved.exists():
        if not resolved.is_file():
            raise UnsafeStatePathError("state database path must be a regular file")
        _require_owner_mode(resolved, 0o600, "state database")
    if resolved.parent.exists():
        if not resolved.parent.is_dir():
            raise UnsafeStatePathError("state database parent must be a directory")
        _require_owner_mode(resolved.parent, 0o700, "state database parent")
    for suffix in ("-wal", "-shm"):
        companion = Path(str(resolved) + suffix)
        if companion.exists():
            if companion.is_symlink() or not companion.is_file():
                raise UnsafeStatePathError(f"SQLite companion {suffix} must be a regular file")
            _require_owner_mode(companion, 0o600, f"SQLite companion {suffix}")
    return resolved


def _ensure_state_file(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.exists():
        return False
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    os.chmod(path, 0o600)
    return True


def _backup_database(connection: sqlite3.Connection, path: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(f"{path.name}.backup-{stamp}.sqlite3")
    descriptor = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    backup = sqlite3.connect(str(backup_path))
    try:
        connection.backup(backup)
    finally:
        backup.close()
    os.chmod(backup_path, 0o600)
    return backup_path


SCHEMA_V1_SQL = """
CREATE TABLE schema_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    migrated_at TEXT NOT NULL
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('daily', 'weekly')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    confirmation_status TEXT NOT NULL CHECK (confirmation_status IN ('pending','confirmed')),
    source_contract_version TEXT NOT NULL
);
CREATE TABLE partitions (
    dataset TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    status TEXT NOT NULL CHECK (status IN ('complete','partial','empty','stale','blocked')),
    collected_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    error_category TEXT,
    supersedes_revision INTEGER,
    PRIMARY KEY (dataset, period_start, period_end, contract_version, revision)
);
CREATE TABLE account_snapshots (
    snapshot_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    currency TEXT NOT NULL,
    net_assets TEXT NOT NULL,
    cash TEXT NOT NULL,
    buying_power TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (snapshot_at, revision)
);
CREATE TABLE position_snapshots (
    snapshot_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    revision INTEGER NOT NULL,
    underlying TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (snapshot_at, symbol, revision)
);
CREATE TABLE trade_aggregates (
    market_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    revision INTEGER NOT NULL,
    order_count INTEGER NOT NULL CHECK (order_count >= 0),
    execution_count INTEGER NOT NULL CHECK (execution_count >= 0),
    executed_quantity TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (market_date, symbol, side, revision)
);
CREATE TABLE market_snapshots (
    as_of TEXT NOT NULL,
    symbol TEXT NOT NULL,
    revision INTEGER NOT NULL,
    value TEXT NOT NULL,
    previous_close TEXT NOT NULL,
    change_pct TEXT NOT NULL,
    session TEXT NOT NULL,
    proxy_for TEXT,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (as_of, symbol, revision)
);
CREATE TABLE relevant_events (
    derived_event_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    et_at TEXT NOT NULL,
    shanghai_at TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('已发生','预期','未公布','未验证')),
    source_category TEXT NOT NULL,
    impact_channel TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (derived_event_key, revision)
);
CREATE TABLE analysis_snapshots (
    facts_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    output_json TEXT NOT NULL,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (facts_hash, plan_hash, contract_version)
);
CREATE TABLE confirmations (
    review_key TEXT NOT NULL,
    confirmation_version INTEGER NOT NULL CHECK (confirmation_version > 0),
    confirmation_status TEXT NOT NULL CHECK (confirmation_status IN ('pending','confirmed')),
    confirmed_at TEXT,
    facts_hash TEXT NOT NULL,
    supersedes_version INTEGER,
    PRIMARY KEY (review_key, confirmation_version)
);
"""

SCHEMA_V2_SQL = """
CREATE TABLE weekly_reviews (
    review_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    run_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source_contract_version TEXT NOT NULL,
    facts_hash TEXT NOT NULL,
    plan_hash TEXT,
    dependency_hash TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    supersedes_revision INTEGER,
    PRIMARY KEY (review_key, revision),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE weekly_review_dependencies (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    dataset TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    partition_revision INTEGER NOT NULL CHECK (partition_revision > 0),
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (
        review_key, review_revision, dataset, period_start, period_end, contract_version
    ),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
CREATE TABLE weekly_module_statuses (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    module_name TEXT NOT NULL CHECK (
        module_name IN ('trades','performance','attribution','cash_flow','positions','market','events','plan')
    ),
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    requested_start TEXT,
    requested_end TEXT,
    returned_start TEXT,
    returned_end TEXT,
    error_category TEXT,
    PRIMARY KEY (review_key, review_revision, module_name),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
CREATE TABLE weekly_performance (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    currency TEXT NOT NULL,
    initial_asset_value TEXT NOT NULL,
    ending_asset_value TEXT NOT NULL,
    profit TEXT NOT NULL,
    profit_rate TEXT NOT NULL,
    time_weighted_return TEXT NOT NULL,
    invest_amount TEXT NOT NULL,
    mechanical_asset_change TEXT NOT NULL,
    reconciliation_residual TEXT NOT NULL,
    requested_utc_start TEXT NOT NULL,
    requested_utc_end TEXT NOT NULL,
    returned_utc_start TEXT NOT NULL,
    returned_utc_end TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','stale')),
    PRIMARY KEY (review_key, review_revision),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
CREATE TABLE weekly_attributions (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    underlying TEXT NOT NULL,
    instrument_group TEXT NOT NULL CHECK (instrument_group IN ('equity','derivatives','combined')),
    display_name TEXT NOT NULL,
    profit TEXT NOT NULL,
    underlying_profit TEXT NOT NULL,
    derivatives_profit TEXT NOT NULL,
    currency TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','stale')),
    PRIMARY KEY (review_key, review_revision, underlying, instrument_group),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
CREATE TABLE weekly_cash_flow_aggregates (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    category TEXT NOT NULL CHECK (
        category IN (
            'stock_buy','stock_sell','option_buy','option_sell','fx_debit','fx_credit',
            'ipo_financing','ipo_subscription','ipo_allotment'
        )
    ),
    currency TEXT NOT NULL,
    amount TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','stale')),
    PRIMARY KEY (review_key, review_revision, category, currency),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
CREATE TABLE weekly_review_items (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    item_index INTEGER NOT NULL CHECK (item_index >= 0),
    item_kind TEXT NOT NULL CHECK (
        item_kind IN ('plan_actual','discipline','retain','delete','rewrite','add','risk','gap')
    ),
    subject TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_boundary TEXT NOT NULL,
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('fact','interpretation','draft','gap')),
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','stale','blocked')),
    PRIMARY KEY (review_key, review_revision, item_index),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
"""

SCHEMA_V3_SQL = """
CREATE TABLE plan_versions (
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    plan_stage TEXT NOT NULL CHECK (plan_stage IN ('pre_entry','position_management')),
    underlying TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long','short','hedge')),
    setup_type TEXT NOT NULL CHECK (
        setup_type IN ('pullback','breakout','range','bottom_reversal','position_management')
    ),
    plan_status TEXT NOT NULL CHECK (plan_status IN ('draft','confirmed','expired')),
    generated_at TEXT NOT NULL,
    effective_at TEXT,
    confirmed_at TEXT,
    expires_at TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    evidence_as_of TEXT NOT NULL,
    evidence_timezone TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    bars_used INTEGER NOT NULL CHECK (bars_used >= 319),
    atr14 TEXT NOT NULL,
    minimum_reward_risk TEXT NOT NULL,
    max_invalidation_pct TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_version INTEGER,
    parent_plan_id TEXT,
    parent_plan_version INTEGER,
    initial_buy_episode_key TEXT,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    PRIMARY KEY (plan_id, version),
    FOREIGN KEY (parent_plan_id, parent_plan_version)
        REFERENCES plan_versions(plan_id, version)
);
CREATE TABLE plan_zones (
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL CHECK (plan_version > 0),
    zone_order INTEGER NOT NULL CHECK (zone_order >= 0),
    zone_kind TEXT NOT NULL CHECK (
        zone_kind IN ('observation','entry','add','reduce','exit','invalidation')
    ),
    low TEXT NOT NULL,
    high TEXT NOT NULL,
    currency TEXT NOT NULL,
    condition TEXT NOT NULL,
    derived_from TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','stale')),
    PRIMARY KEY (plan_id, plan_version, zone_order),
    FOREIGN KEY (plan_id, plan_version)
        REFERENCES plan_versions(plan_id, version)
);
CREATE TABLE trade_episode_assessments (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    episode_index INTEGER NOT NULL CHECK (episode_index >= 0),
    market_date TEXT NOT NULL,
    underlying TEXT NOT NULL,
    side TEXT NOT NULL,
    plan_id TEXT,
    plan_version INTEGER,
    coverage_status TEXT NOT NULL CHECK (coverage_status IN ('covered','uncovered')),
    compliance_status TEXT NOT NULL CHECK (
        compliance_status IN ('compliant','non_compliant','unassessable')
    ),
    outcome_status TEXT NOT NULL CHECK (
        outcome_status IN ('success','failure','open','flat','unverifiable')
    ),
    deviation_type TEXT,
    reason TEXT NOT NULL,
    next_rule TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','stale','blocked')),
    PRIMARY KEY (review_key, review_revision, episode_index),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision),
    FOREIGN KEY (plan_id, plan_version)
        REFERENCES plan_versions(plan_id, version)
);
CREATE TABLE weekly_execution_metrics (
    review_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL CHECK (review_revision > 0),
    eligible_episode_count INTEGER NOT NULL CHECK (eligible_episode_count >= 0),
    covered_episode_count INTEGER NOT NULL CHECK (covered_episode_count >= 0),
    assessable_episode_count INTEGER NOT NULL CHECK (assessable_episode_count >= 0),
    compliant_episode_count INTEGER NOT NULL CHECK (compliant_episode_count >= 0),
    resolved_episode_count INTEGER NOT NULL CHECK (resolved_episode_count >= 0),
    successful_episode_count INTEGER NOT NULL CHECK (successful_episode_count >= 0),
    open_episode_count INTEGER NOT NULL CHECK (open_episode_count >= 0),
    flat_episode_count INTEGER NOT NULL CHECK (flat_episode_count >= 0),
    unverifiable_episode_count INTEGER NOT NULL CHECK (unverifiable_episode_count >= 0),
    review_needed_count INTEGER NOT NULL CHECK (review_needed_count >= 0),
    coverage_rate TEXT,
    execution_rate TEXT,
    plan_win_rate TEXT,
    data_status TEXT NOT NULL CHECK (data_status IN ('complete','partial','empty','stale','blocked')),
    gap TEXT,
    PRIMARY KEY (review_key, review_revision),
    FOREIGN KEY (review_key, review_revision)
        REFERENCES weekly_reviews(review_key, revision)
);
"""


def _apply_migration_v1(connection: sqlite3.Connection) -> None:
    # ``executescript`` performs an implicit COMMIT before running its script,
    # which would let a later DDL error leave a half-created schema behind.
    # Execute each fixed statement inside the caller's BEGIN IMMEDIATE instead.
    for statement in SCHEMA_V1_SQL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)
    timestamp = utc_now()
    connection.execute(
        "INSERT INTO schema_meta(singleton, schema_version, created_at, migrated_at) VALUES (1, ?, ?, ?)",
        (1, timestamp, timestamp),
    )


def _apply_migration_v2(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_V2_SQL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)
    timestamp = utc_now()
    connection.execute(
        "UPDATE schema_meta SET schema_version=2, migrated_at=? WHERE singleton=1",
        (timestamp,),
    )


def _apply_migration_v3(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_V3_SQL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)
    timestamp = utc_now()
    connection.execute(
        "UPDATE schema_meta SET schema_version=3, migrated_at=? WHERE singleton=1",
        (timestamp,),
    )


def _chmod_sqlite_files(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            os.chmod(candidate, 0o600)


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != set(SCHEMA_TABLES):
        raise StateMigrationError(f"state database tables do not match schema v{SCHEMA_VERSION}")
    for table, expected in EXPECTED_COLUMNS.items():
        columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if columns != expected:
            raise StateMigrationError(
                f"state database table {table} does not match schema v{SCHEMA_VERSION}"
            )
    meta = connection.execute(
        "SELECT singleton, schema_version FROM schema_meta"
    ).fetchall()
    if len(meta) != 1 or tuple(meta[0]) != (1, SCHEMA_VERSION):
        raise StateMigrationError(f"schema_meta does not match schema v{SCHEMA_VERSION}")
    quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
    if quick_check != ["ok"]:
        raise StateMigrationError("SQLite quick_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StateMigrationError("SQLite foreign_key_check failed")


@contextlib.contextmanager
def read_state_store(
    path: Path = DEFAULT_STATE_DB, *, test_root: Optional[Path] = None,
) -> Iterator["StateStore"]:
    """Read one consistent current-schema snapshot without creating or migrating it."""
    resolved = validate_state_db_path(path, test_root=test_root)
    if not resolved.is_file():
        raise UnsafeStatePathError("read-only state database does not exist")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise StateMigrationError("read-only state requires the current schema; migrate separately")
        _validate_schema(connection)
        yield StateStore(connection, resolved)
    finally:
        # Do not call StateStore.close(): its writable lifecycle chmods companions.
        connection.close()


def open_state_store(
    path: Path = DEFAULT_STATE_DB,
    *,
    test_root: Optional[Path] = None,
    busy_timeout_ms: int = 5000,
) -> "StateStore":
    original_path = path
    resolved = validate_state_db_path(original_path, test_root=test_root)
    created = _ensure_state_file(resolved)
    resolved = validate_state_db_path(original_path, test_root=test_root)
    old_umask = os.umask(0o077)
    try:
        connection = sqlite3.connect(
            str(resolved),
            timeout=max(busy_timeout_ms, 1) / 1000,
            isolation_level=None,
        )
    finally:
        os.umask(old_umask)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        connection.close()
        raise StateMigrationError(f"database schema {version} is newer than supported {SCHEMA_VERSION}")
    if version == SCHEMA_VERSION:
        try:
            _validate_schema(connection)
        except Exception:
            connection.close()
            _chmod_sqlite_files(resolved)
            raise
        connection.execute("PRAGMA journal_mode=WAL")
    else:
        if not created and resolved.stat().st_size > 0:
            try:
                _backup_database(connection, resolved)
            except Exception as exc:
                connection.close()
                _chmod_sqlite_files(resolved)
                raise StateMigrationError("state database backup failed; migration was not started") from exc
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if version == 0:
                _apply_migration_v1(connection)
                version = 1
            if version == 1:
                _apply_migration_v2(connection)
                version = 2
            if version == 2:
                _apply_migration_v3(connection)
                version = 3
            if version != SCHEMA_VERSION:
                raise StateMigrationError(
                    f"database schema {version} cannot migrate to supported {SCHEMA_VERSION}"
                )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            _validate_schema(connection)
            connection.commit()
        except Exception as exc:
            connection.rollback()
            connection.close()
            _chmod_sqlite_files(resolved)
            raise StateMigrationError("state database migration failed; original database was preserved") from exc
    _chmod_sqlite_files(resolved)
    return StateStore(connection, resolved)


def _date(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise StateContractError(f"{path} must be YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise StateContractError(f"{path} must be a real YYYY-MM-DD date") from exc
    return parsed.isoformat()


def _timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise StateContractError(f"{path} must be an RFC3339 timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateContractError(f"{path} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise StateContractError(f"{path} must include a timezone")
    return value


def _text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise StateContractError(f"{path} must be a non-empty string")
    if SENSITIVE_VALUE_RE.search(value):
        raise StateContractError(f"{path} contains a forbidden sensitive value")
    return value.strip()


def _optional_text(value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    return _text(value, path)


def _optional_timestamp(value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    return _timestamp(value, path)


def _sha256(value: Any, path: str) -> str:
    text = _text(value, path)
    if not SHA256_RE.fullmatch(text):
        raise StateContractError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _decimal(value: Any, path: str) -> str:
    if isinstance(value, bool):
        raise StateContractError(f"{path} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise StateContractError(f"{path} must be a finite decimal") from exc
    if not result.is_finite():
        raise StateContractError(f"{path} must be a finite decimal")
    return format(result, "f")


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StateContractError(f"{path} must be a non-negative integer")
    return value


def _strict_object(value: Any, allowed: Iterable[str], required: Iterable[str], path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise StateContractError(f"{path} must be an object")
    for key in value:
        if not isinstance(key, str) or SENSITIVE_KEY_RE.search(key):
            raise StateContractError(f"{path} contains a forbidden field")
    unknown = sorted(set(value) - set(allowed))
    missing = sorted(set(required) - set(value))
    if unknown:
        raise StateContractError(f"{path} has unsupported fields: {', '.join(unknown)}")
    if missing:
        raise StateContractError(f"{path} is missing fields: {', '.join(missing)}")
    return value


def _rows(value: Any, path: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise StateContractError(f"{path} must be an array")
    return [
        _strict_object(row, row.keys() if isinstance(row, dict) else (), (), f"{path}[{index}]")
        for index, row in enumerate(value)
    ]


def _normalize_payload(dataset: str, payload: Any, status: str) -> Any:
    if dataset == "account_snapshot":
        if status in {"empty", "blocked"} and payload in ({}, []):
            return {}
        item = _strict_object(
            payload,
            {"snapshot_at", "currency", "net_assets", "cash", "buying_power", "data_status"},
            {"snapshot_at", "currency", "net_assets", "cash", "buying_power", "data_status"},
            "$.account_snapshot",
        )
        normalized: Any = {
            "snapshot_at": _timestamp(item["snapshot_at"], "$.account_snapshot.snapshot_at"),
            "currency": _text(item["currency"], "$.account_snapshot.currency"),
            "net_assets": _decimal(item["net_assets"], "$.account_snapshot.net_assets"),
            "cash": _decimal(item["cash"], "$.account_snapshot.cash"),
            "buying_power": _decimal(item["buying_power"], "$.account_snapshot.buying_power"),
            "data_status": _status(item["data_status"], "$.account_snapshot.data_status"),
        }
    else:
        if not isinstance(payload, list):
            raise StateContractError(f"$.{dataset} must be an array")
        normalized = []
        validators = {
            "positions_snapshot": _normalize_position,
            "trades": _normalize_trade,
            "market_snapshots": _normalize_market,
            "relevant_events": _normalize_event,
        }
        validator = validators.get(dataset)
        if validator is None:
            raise StateContractError(f"unsupported dataset: {dataset}")
        for index, row in enumerate(payload):
            normalized.append(validator(row, f"$.{dataset}[{index}]"))
        natural_keys = {
            "positions_snapshot": ("snapshot_at", "symbol"),
            "trades": ("market_date", "symbol", "side"),
            "market_snapshots": ("as_of", "symbol"),
            "relevant_events": ("derived_event_key",),
        }
        key_fields = natural_keys[dataset]
        seen = set()
        for row in normalized:
            key = tuple(row[field] for field in key_fields)
            if key in seen:
                raise StateContractError(f"$.{dataset} contains a duplicate natural key")
            seen.add(key)
    if status == "empty" and normalized not in ([], {}):
        raise StateContractError("empty partition cannot contain facts")
    if status == "blocked" and normalized not in ([], {}):
        raise StateContractError("blocked partition cannot contain facts")
    if status == "complete" and normalized in ([], {}):
        raise StateContractError("complete partition must contain verified facts; use empty for a successful empty result")
    child_statuses: Sequence[str]
    if isinstance(normalized, list):
        child_statuses = [row["data_status"] for row in normalized]
    elif normalized:
        child_statuses = [normalized["data_status"]]
    else:
        child_statuses = []
    if any(value in {"empty", "blocked"} for value in child_statuses):
        raise StateContractError("a factual row cannot have empty or blocked data_status")
    if status == "complete" and any(value != "complete" for value in child_statuses):
        raise StateContractError("complete partition cannot contain non-complete facts")
    return normalized


def validate_partition_payload(dataset: str, payload: Any, status: str) -> Any:
    """Validate and normalize one fixed sanitized partition without writing it."""
    return _normalize_payload(_text(dataset, "dataset"), payload, _status(status, "status"))


def _status(value: Any, path: str) -> str:
    result = _text(value, path)
    if result not in DATA_STATUSES:
        raise StateContractError(f"{path} has unsupported data status")
    return result


def _normalize_position(value: Any, path: str) -> Dict[str, Any]:
    keys = {"snapshot_at", "symbol", "underlying", "instrument_type", "quantity", "data_status"}
    item = _strict_object(value, keys, keys, path)
    return {
        "snapshot_at": _timestamp(item["snapshot_at"], f"{path}.snapshot_at"),
        "symbol": _text(item["symbol"], f"{path}.symbol"),
        "underlying": _text(item["underlying"], f"{path}.underlying"),
        "instrument_type": _text(item["instrument_type"], f"{path}.instrument_type"),
        "quantity": _decimal(item["quantity"], f"{path}.quantity"),
        "data_status": _status(item["data_status"], f"{path}.data_status"),
    }


def _normalize_trade(value: Any, path: str) -> Dict[str, Any]:
    keys = {"market_date", "symbol", "side", "order_count", "execution_count", "executed_quantity", "data_status"}
    item = _strict_object(value, keys, keys, path)
    return {
        "market_date": _date(item["market_date"], f"{path}.market_date"),
        "symbol": _text(item["symbol"], f"{path}.symbol"),
        "side": _text(item["side"], f"{path}.side"),
        "order_count": _integer(item["order_count"], f"{path}.order_count"),
        "execution_count": _integer(item["execution_count"], f"{path}.execution_count"),
        "executed_quantity": _decimal(item["executed_quantity"], f"{path}.executed_quantity"),
        "data_status": _status(item["data_status"], f"{path}.data_status"),
    }


def _normalize_market(value: Any, path: str) -> Dict[str, Any]:
    keys = {"as_of", "symbol", "value", "previous_close", "change_pct", "session", "proxy_for", "data_status"}
    item = _strict_object(value, keys, keys, path)
    proxy = item["proxy_for"]
    if proxy is not None:
        proxy = _text(proxy, f"{path}.proxy_for")
    return {
        "as_of": _timestamp(item["as_of"], f"{path}.as_of"),
        "symbol": _text(item["symbol"], f"{path}.symbol"),
        "value": _decimal(item["value"], f"{path}.value"),
        "previous_close": _decimal(item["previous_close"], f"{path}.previous_close"),
        "change_pct": _decimal(item["change_pct"], f"{path}.change_pct"),
        "session": _text(item["session"], f"{path}.session"),
        "proxy_for": proxy,
        "data_status": _status(item["data_status"], f"{path}.data_status"),
    }


def _normalize_event(value: Any, path: str) -> Dict[str, Any]:
    keys = {"derived_event_key", "et_at", "shanghai_at", "title", "status", "source_category", "impact_channel", "data_status"}
    item = _strict_object(value, keys, keys, path)
    event_status = _text(item["status"], f"{path}.status")
    if event_status not in {"已发生", "预期", "未公布", "未验证"}:
        raise StateContractError(f"{path}.status is unsupported")
    et_at = _timestamp(item["et_at"], f"{path}.et_at")
    shanghai_at = _timestamp(item["shanghai_at"], f"{path}.shanghai_at")
    et_instant = dt.datetime.fromisoformat(et_at.replace("Z", "+00:00"))
    shanghai_instant = dt.datetime.fromisoformat(shanghai_at.replace("Z", "+00:00"))
    if et_instant.astimezone(dt.timezone.utc) != shanghai_instant.astimezone(dt.timezone.utc):
        raise StateContractError(f"{path} ET and Shanghai timestamps must identify one instant")
    return {
        "derived_event_key": _text(item["derived_event_key"], f"{path}.derived_event_key"),
        "et_at": et_at,
        "shanghai_at": shanghai_at,
        "title": _text(item["title"], f"{path}.title"),
        "status": event_status,
        "source_category": _text(item["source_category"], f"{path}.source_category"),
        "impact_channel": _text(item["impact_channel"], f"{path}.impact_channel"),
        "data_status": _status(item["data_status"], f"{path}.data_status"),
    }


def _normalize_weekly_dependency(value: Any, path: str) -> Dict[str, Any]:
    keys = {
        "dataset",
        "period_start",
        "period_end",
        "contract_version",
        "partition_revision",
        "payload_hash",
    }
    item = _strict_object(value, keys, keys, path)
    start = _date(item["period_start"], f"{path}.period_start")
    end = _date(item["period_end"], f"{path}.period_end")
    if start > end:
        raise StateContractError(f"{path}.period_start must not be after period_end")
    revision = item["partition_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise StateContractError(f"{path}.partition_revision must be a positive integer")
    return {
        "dataset": _text(item["dataset"], f"{path}.dataset"),
        "period_start": start,
        "period_end": end,
        "contract_version": _text(item["contract_version"], f"{path}.contract_version"),
        "partition_revision": revision,
        "payload_hash": _sha256(item["payload_hash"], f"{path}.payload_hash"),
    }


def _normalize_weekly_module(value: Any, path: str) -> Dict[str, Any]:
    keys = {
        "name",
        "status",
        "requested_start",
        "requested_end",
        "returned_start",
        "returned_end",
        "error_category",
    }
    item = _strict_object(value, keys, keys, path)
    name = _text(item["name"], f"{path}.name")
    if name not in WEEKLY_MODULES:
        raise StateContractError(f"{path}.name is unsupported")
    status = _status(item["status"], f"{path}.status")
    requested_start = _optional_timestamp(item["requested_start"], f"{path}.requested_start")
    requested_end = _optional_timestamp(item["requested_end"], f"{path}.requested_end")
    returned_start = _optional_timestamp(item["returned_start"], f"{path}.returned_start")
    returned_end = _optional_timestamp(item["returned_end"], f"{path}.returned_end")
    if (requested_start is None) != (requested_end is None):
        raise StateContractError(f"{path} requested window must provide both endpoints")
    if (returned_start is None) != (returned_end is None):
        raise StateContractError(f"{path} returned window must provide both endpoints")
    if returned_start is not None and requested_start is None:
        raise StateContractError(f"{path} returned window requires a requested window")
    for start, end, label in (
        (requested_start, requested_end, "requested"),
        (returned_start, returned_end, "returned"),
    ):
        if start is not None and end is not None:
            start_at = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_at = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
            if start_at >= end_at:
                raise StateContractError(f"{path} {label} window must be increasing")
    error_category = _optional_text(item["error_category"], f"{path}.error_category")
    if status in {"partial", "stale", "blocked"} and error_category is None:
        raise StateContractError(f"{path} non-success status requires error_category")
    if status in {"complete", "empty"} and error_category is not None:
        raise StateContractError(f"{path} success status cannot include error_category")
    return {
        "name": name,
        "status": status,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "returned_start": returned_start,
        "returned_end": returned_end,
        "error_category": error_category,
    }


def _normalize_weekly_performance(value: Any, path: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    keys = {
        "currency",
        "initial_asset_value",
        "ending_asset_value",
        "profit",
        "profit_rate",
        "time_weighted_return",
        "invest_amount",
        "mechanical_asset_change",
        "reconciliation_residual",
        "requested_utc_start",
        "requested_utc_end",
        "returned_utc_start",
        "returned_utc_end",
        "data_status",
    }
    item = _strict_object(value, keys, keys, path)
    status = _status(item["data_status"], f"{path}.data_status")
    if status in {"empty", "blocked"}:
        raise StateContractError(f"{path} factual performance cannot be empty or blocked")
    requested_start = _timestamp(item["requested_utc_start"], f"{path}.requested_utc_start")
    requested_end = _timestamp(item["requested_utc_end"], f"{path}.requested_utc_end")
    returned_start = _timestamp(item["returned_utc_start"], f"{path}.returned_utc_start")
    returned_end = _timestamp(item["returned_utc_end"], f"{path}.returned_utc_end")
    for start, end, label in (
        (requested_start, requested_end, "requested"),
        (returned_start, returned_end, "returned"),
    ):
        if dt.datetime.fromisoformat(start.replace("Z", "+00:00")) >= dt.datetime.fromisoformat(
            end.replace("Z", "+00:00")
        ):
            raise StateContractError(f"{path} {label} window must be increasing")
    return {
        "currency": _text(item["currency"], f"{path}.currency"),
        "initial_asset_value": _decimal(item["initial_asset_value"], f"{path}.initial_asset_value"),
        "ending_asset_value": _decimal(item["ending_asset_value"], f"{path}.ending_asset_value"),
        "profit": _decimal(item["profit"], f"{path}.profit"),
        "profit_rate": _decimal(item["profit_rate"], f"{path}.profit_rate"),
        "time_weighted_return": _decimal(item["time_weighted_return"], f"{path}.time_weighted_return"),
        "invest_amount": _decimal(item["invest_amount"], f"{path}.invest_amount"),
        "mechanical_asset_change": _decimal(
            item["mechanical_asset_change"], f"{path}.mechanical_asset_change"
        ),
        "reconciliation_residual": _decimal(
            item["reconciliation_residual"], f"{path}.reconciliation_residual"
        ),
        "requested_utc_start": requested_start,
        "requested_utc_end": requested_end,
        "returned_utc_start": returned_start,
        "returned_utc_end": returned_end,
        "data_status": status,
    }


def _normalize_weekly_attribution(value: Any, path: str) -> Dict[str, Any]:
    keys = {
        "underlying",
        "instrument_group",
        "display_name",
        "profit",
        "underlying_profit",
        "derivatives_profit",
        "currency",
        "data_status",
    }
    item = _strict_object(value, keys, keys, path)
    instrument_group = _text(item["instrument_group"], f"{path}.instrument_group")
    if instrument_group not in {"equity", "derivatives", "combined"}:
        raise StateContractError(f"{path}.instrument_group is unsupported")
    status = _status(item["data_status"], f"{path}.data_status")
    if status in {"empty", "blocked"}:
        raise StateContractError(f"{path} factual attribution cannot be empty or blocked")
    underlying = _text(item["underlying"], f"{path}.underlying")
    if OPTION_IDENTITY_VALUE_RE.search(underlying):
        raise StateContractError(f"{path}.underlying contains option contract identity")
    return {
        "underlying": underlying,
        "instrument_group": instrument_group,
        "display_name": _text(item["display_name"], f"{path}.display_name"),
        "profit": _decimal(item["profit"], f"{path}.profit"),
        "underlying_profit": _decimal(item["underlying_profit"], f"{path}.underlying_profit"),
        "derivatives_profit": _decimal(item["derivatives_profit"], f"{path}.derivatives_profit"),
        "currency": _text(item["currency"], f"{path}.currency"),
        "data_status": status,
    }


def _normalize_weekly_cash_flow(value: Any, path: str) -> Dict[str, Any]:
    keys = {"category", "currency", "amount", "row_count", "data_status"}
    item = _strict_object(value, keys, keys, path)
    category = _text(item["category"], f"{path}.category")
    if category not in WEEKLY_CASH_CATEGORIES:
        raise StateContractError(f"{path}.category is unsupported")
    status = _status(item["data_status"], f"{path}.data_status")
    if status in {"empty", "blocked"}:
        raise StateContractError(f"{path} factual cash flow cannot be empty or blocked")
    return {
        "category": category,
        "currency": _text(item["currency"], f"{path}.currency"),
        "amount": _decimal(item["amount"], f"{path}.amount"),
        "row_count": _integer(item["row_count"], f"{path}.row_count"),
        "data_status": status,
    }


def _normalize_weekly_review_item(value: Any, path: str) -> Dict[str, Any]:
    keys = {
        "item_kind",
        "subject",
        "summary",
        "evidence_boundary",
        "evidence_kind",
        "data_status",
    }
    item = _strict_object(value, keys, keys, path)
    item_kind = _text(item["item_kind"], f"{path}.item_kind")
    if item_kind not in WEEKLY_ITEM_KINDS:
        raise StateContractError(f"{path}.item_kind is unsupported")
    evidence_kind = _text(item["evidence_kind"], f"{path}.evidence_kind")
    if evidence_kind not in WEEKLY_EVIDENCE_KINDS:
        raise StateContractError(f"{path}.evidence_kind is unsupported")
    subject = _text(item["subject"], f"{path}.subject")
    summary = _text(item["summary"], f"{path}.summary")
    boundary = _text(item["evidence_boundary"], f"{path}.evidence_boundary")
    if any(OPTION_IDENTITY_VALUE_RE.search(text) for text in (subject, summary, boundary)):
        raise StateContractError(f"{path} contains option contract identity")
    return {
        "item_kind": item_kind,
        "subject": subject,
        "summary": summary,
        "evidence_boundary": boundary,
        "evidence_kind": evidence_kind,
        "data_status": _status(item["data_status"], f"{path}.data_status"),
    }


def normalize_plan_version(value: Any) -> Dict[str, Any]:
    """Validate one immutable plan projection before persistence."""

    keys = {
        "schema_version",
        "plan_id",
        "version",
        "plan_stage",
        "underlying",
        "direction",
        "setup_type",
        "plan_status",
        "generated_at",
        "effective_at",
        "confirmed_at",
        "expires_at",
        "evidence",
        "constraints",
        "content_hash",
        "supersedes_version",
        "parent_plan_id",
        "parent_plan_version",
        "initial_buy_episode_key",
        "data_status",
        "zones",
    }
    root = _strict_object(value, keys, keys, "$plan")
    if root["schema_version"] != "trading-plan-state.v1":
        raise StateContractError("$plan.schema_version is unsupported")
    stage = _text(root["plan_stage"], "$plan.plan_stage")
    setup = _text(root["setup_type"], "$plan.setup_type")
    status = _text(root["plan_status"], "$plan.plan_status")
    if stage not in PLAN_STAGES:
        raise StateContractError("$plan.plan_stage is unsupported")
    if setup not in PLAN_SETUPS:
        raise StateContractError("$plan.setup_type is unsupported")
    if status not in PLAN_STATUSES:
        raise StateContractError("$plan.plan_status is unsupported")
    if (stage == "position_management") != (setup == "position_management"):
        raise StateContractError("position_management stage and setup must match")
    direction = _text(root["direction"], "$plan.direction")
    if direction not in {"long", "short", "hedge"}:
        raise StateContractError("$plan.direction is unsupported")
    version = _integer(root["version"], "$plan.version")
    if version < 1:
        raise StateContractError("$plan.version must be a positive integer")
    generated_at = _timestamp(root["generated_at"], "$plan.generated_at")
    expires_at = _timestamp(root["expires_at"], "$plan.expires_at")
    if dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= dt.datetime.fromisoformat(
        generated_at.replace("Z", "+00:00")
    ):
        raise StateContractError("$plan.expires_at must be after generated_at")
    effective_at = _optional_timestamp(root["effective_at"], "$plan.effective_at")
    confirmed_at = _optional_timestamp(root["confirmed_at"], "$plan.confirmed_at")
    if status in {"confirmed", "expired"}:
        if effective_at is None or confirmed_at is None:
            raise StateContractError("confirmed plan requires effective_at and confirmed_at")
        if dt.datetime.fromisoformat(confirmed_at.replace("Z", "+00:00")) > dt.datetime.fromisoformat(
            effective_at.replace("Z", "+00:00")
        ):
            raise StateContractError("confirmed_at must not be after effective_at")
        if not (
            dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            <= dt.datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
            <= dt.datetime.fromisoformat(effective_at.replace("Z", "+00:00"))
            < dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        ):
            raise StateContractError("confirmation and effectiveness must be inside the plan lifetime")
    elif effective_at is not None or confirmed_at is not None:
        raise StateContractError("non-confirmed plan cannot include effective_at or confirmed_at")

    supersedes = root["supersedes_version"]
    if supersedes is not None:
        supersedes = _integer(supersedes, "$plan.supersedes_version")
        if supersedes < 1 or supersedes >= version:
            raise StateContractError("supersedes_version must be an earlier version")

    parent_plan_id = root["parent_plan_id"]
    parent_plan_version = root["parent_plan_version"]
    episode_key = root["initial_buy_episode_key"]
    if stage == "pre_entry":
        if any(value is not None for value in (parent_plan_id, parent_plan_version, episode_key)):
            raise StateContractError("pre_entry plan cannot reference a parent or buy episode")
    else:
        parent_plan_id = _text(parent_plan_id, "$plan.parent_plan_id")
        parent_plan_version = _integer(parent_plan_version, "$plan.parent_plan_version")
        if parent_plan_version < 1:
            raise StateContractError("$plan.parent_plan_version must be positive")
        episode_key = _text(episode_key, "$plan.initial_buy_episode_key")

    evidence_keys = {
        "evidence_id",
        "source",
        "as_of",
        "timezone",
        "adjustment",
        "bars_used",
        "atr14",
    }
    evidence = _strict_object(root["evidence"], evidence_keys, evidence_keys, "$plan.evidence")
    evidence_id = _sha256(evidence["evidence_id"], "$plan.evidence.evidence_id")
    if _text(evidence["source"], "$plan.evidence.source") != "Longbridge":
        raise StateContractError("plan evidence must be Longbridge")
    evidence_timezone = _text(evidence["timezone"], "$plan.evidence.timezone")
    if evidence_timezone != "America/New_York":
        raise StateContractError("plan evidence timezone must be America/New_York")
    adjustment = _text(evidence["adjustment"], "$plan.evidence.adjustment")
    if adjustment not in {"forward", "backward"}:
        raise StateContractError("plan evidence adjustment must be forward or backward")
    bars_used = _integer(evidence["bars_used"], "$plan.evidence.bars_used")
    if bars_used < 319:
        raise StateContractError("plan evidence requires at least 319 completed bars")
    atr14 = _decimal(evidence["atr14"], "$plan.evidence.atr14")
    if Decimal(atr14) <= 0:
        raise StateContractError("plan evidence ATR14 must be positive")
    constraints_keys = {"minimum_reward_risk", "max_invalidation_pct"}
    constraints = _strict_object(
        root["constraints"], constraints_keys, constraints_keys, "$plan.constraints"
    )
    minimum_reward_risk = _decimal(
        constraints["minimum_reward_risk"], "$plan.constraints.minimum_reward_risk"
    )
    max_invalidation_pct = _decimal(
        constraints["max_invalidation_pct"], "$plan.constraints.max_invalidation_pct"
    )
    if Decimal(minimum_reward_risk) <= 0 or Decimal(max_invalidation_pct) <= 0:
        raise StateContractError("plan risk constraints must be positive")

    if not isinstance(root["zones"], list):
        raise StateContractError("$plan.zones must be an array")
    zones = []
    for index, raw in enumerate(root["zones"]):
        path = f"$plan.zones[{index}]"
        zone_keys = {"kind", "low", "high", "currency", "condition", "derived_from", "data_status"}
        row = _strict_object(raw, zone_keys, zone_keys, path)
        kind = _text(row["kind"], f"{path}.kind")
        if kind not in PLAN_ZONE_KINDS:
            raise StateContractError(f"{path}.kind is unsupported")
        if stage == "pre_entry" and kind == "add":
            raise StateContractError("pre_entry plan cannot contain an add zone")
        low = _decimal(row["low"], f"{path}.low")
        high = _decimal(row["high"], f"{path}.high")
        if Decimal(low) <= 0 or Decimal(high) <= 0 or Decimal(low) > Decimal(high):
            raise StateContractError(f"{path} price range is invalid")
        zone_status = _status(row["data_status"], f"{path}.data_status")
        if zone_status in {"empty", "blocked"}:
            raise StateContractError(f"{path} factual zone cannot be empty or blocked")
        zones.append(
            {
                "kind": kind,
                "low": low,
                "high": high,
                "currency": _text(row["currency"], f"{path}.currency"),
                "condition": _text(row["condition"], f"{path}.condition"),
                "derived_from": _text(row["derived_from"], f"{path}.derived_from"),
                "data_status": zone_status,
            }
        )
    if len([row for row in zones if row["kind"] == "invalidation"]) != 1:
        raise StateContractError("plan must contain exactly one invalidation zone")
    if status == "confirmed" and not any(row["kind"] in {"entry", "add"} for row in zones):
        raise StateContractError("confirmed plan requires an entry or add zone")
    if status == "confirmed" and stage == "pre_entry" and not any(
        row["kind"] == "entry" for row in zones
    ):
        raise StateContractError("confirmed pre_entry plan requires an entry zone")
    if status == "confirmed" and stage == "position_management" and not any(
        row["kind"] == "add" for row in zones
    ):
        raise StateContractError("confirmed position_management plan requires an add zone")
    plan_data_status = _status(root["data_status"], "$plan.data_status")
    if status == "confirmed" and (
        plan_data_status != "complete"
        or any(row["data_status"] != "complete" for row in zones)
    ):
        raise StateContractError("confirmed plan and zones must be complete")
    if status == "confirmed":
        targets = [row for row in zones if row["kind"] in {"reduce", "exit"}]
        if not targets:
            raise StateContractError("confirmed plan requires a reduce or exit boundary")
        if direction == "long":
            action_kind = "add" if stage == "position_management" else "entry"
            entry_high = max(Decimal(row["high"]) for row in zones if row["kind"] == action_kind)
            stop_low = next(Decimal(row["low"]) for row in zones if row["kind"] == "invalidation")
            target_low = min(Decimal(row["low"]) for row in targets)
            risk = entry_high - stop_low
            reward = target_low - entry_high
            if risk <= 0 or reward <= 0 or reward / risk < Decimal(minimum_reward_risk):
                raise StateContractError("confirmed price zones violate minimum reward/risk")
            if risk / entry_high * Decimal(100) > Decimal(max_invalidation_pct):
                raise StateContractError("confirmed price zones exceed maximum invalidation")

    underlying = _text(root["underlying"], "$plan.underlying")
    if OPTION_IDENTITY_VALUE_RE.search(underlying):
        raise StateContractError("$plan.underlying contains option contract identity")
    return {
        "schema_version": "trading-plan-state.v1",
        "plan_id": _text(root["plan_id"], "$plan.plan_id"),
        "version": version,
        "plan_stage": stage,
        "underlying": underlying,
        "direction": direction,
        "setup_type": setup,
        "plan_status": status,
        "generated_at": generated_at,
        "effective_at": effective_at,
        "confirmed_at": confirmed_at,
        "expires_at": expires_at,
        "evidence_id": evidence_id,
        "evidence_source": "Longbridge",
        "evidence_as_of": _date(evidence["as_of"], "$plan.evidence.as_of"),
        "evidence_timezone": evidence_timezone,
        "adjustment": adjustment,
        "bars_used": bars_used,
        "atr14": atr14,
        "minimum_reward_risk": minimum_reward_risk,
        "max_invalidation_pct": max_invalidation_pct,
        "content_hash": _sha256(root["content_hash"], "$plan.content_hash"),
        "supersedes_version": supersedes,
        "parent_plan_id": parent_plan_id,
        "parent_plan_version": parent_plan_version,
        "initial_buy_episode_key": episode_key,
        "data_status": plan_data_status,
        "zones": zones,
    }


def _normalize_episode_assessment(value: Any, path: str) -> Dict[str, Any]:
    keys = {
        "market_date",
        "underlying",
        "side",
        "plan_id",
        "plan_version",
        "coverage_status",
        "compliance_status",
        "outcome_status",
        "deviation_type",
        "reason",
        "next_rule",
        "data_status",
    }
    item = _strict_object(value, keys, keys, path)
    coverage = _text(item["coverage_status"], f"{path}.coverage_status")
    compliance = _text(item["compliance_status"], f"{path}.compliance_status")
    outcome = _text(item["outcome_status"], f"{path}.outcome_status")
    if coverage not in EPISODE_COVERAGE or compliance not in EPISODE_COMPLIANCE:
        raise StateContractError(f"{path} contains an unsupported assessment status")
    if outcome not in EPISODE_OUTCOMES:
        raise StateContractError(f"{path}.outcome_status is unsupported")
    plan_id = item["plan_id"]
    plan_version = item["plan_version"]
    if coverage == "covered":
        plan_id = _text(plan_id, f"{path}.plan_id")
        plan_version = _integer(plan_version, f"{path}.plan_version")
        if plan_version < 1:
            raise StateContractError(f"{path}.plan_version must be positive")
    else:
        if plan_id is not None or plan_version is not None:
            raise StateContractError(f"{path} uncovered episode cannot reference a plan")
        if compliance != "unassessable" or outcome != "unverifiable":
            raise StateContractError(f"{path} uncovered episode must remain unassessable/unverifiable")
    if compliance == "unassessable" and outcome != "unverifiable":
        raise StateContractError(f"{path} unassessable episode must remain unverifiable")
    deviation = _optional_text(item["deviation_type"], f"{path}.deviation_type")
    if compliance == "non_compliant" and deviation is None:
        raise StateContractError(f"{path} non_compliant episode requires deviation_type")
    side = _text(item["side"], f"{path}.side")
    if side not in {"buy", "sell"}:
        raise StateContractError(f"{path}.side must be buy or sell")
    underlying = _text(item["underlying"], f"{path}.underlying")
    if OPTION_IDENTITY_VALUE_RE.search(underlying):
        raise StateContractError(f"{path}.underlying contains option contract identity")
    return {
        "market_date": _date(item["market_date"], f"{path}.market_date"),
        "underlying": underlying,
        "side": side,
        "plan_id": plan_id,
        "plan_version": plan_version,
        "coverage_status": coverage,
        "compliance_status": compliance,
        "outcome_status": outcome,
        "deviation_type": deviation,
        "reason": _text(item["reason"], f"{path}.reason"),
        "next_rule": _text(item["next_rule"], f"{path}.next_rule"),
        "data_status": _status(item["data_status"], f"{path}.data_status"),
    }


def _rate(numerator: int, denominator: int) -> Optional[str]:
    if denominator == 0:
        return None
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.000001")))


def _normalize_execution_metrics(
    value: Any,
    assessments: Sequence[Mapping[str, Any]],
    path: str,
) -> Optional[Dict[str, Any]]:
    if value is None:
        if assessments:
            raise StateContractError(f"{path} is required when assessments exist")
        return None
    keys = {"data_status", "gap"}
    item = _strict_object(value, keys, keys, path)
    status = _status(item["data_status"], f"{path}.data_status")
    gap = _optional_text(item["gap"], f"{path}.gap")
    if status in {"partial", "stale", "blocked"} and gap is None:
        raise StateContractError(f"{path} non-success metrics require a gap")
    eligible = len(assessments)
    covered = sum(row["coverage_status"] == "covered" for row in assessments)
    assessable = sum(
        row["coverage_status"] == "covered" and row["compliance_status"] != "unassessable"
        for row in assessments
    )
    compliant = sum(row["compliance_status"] == "compliant" for row in assessments)
    resolved = sum(row["outcome_status"] in {"success", "failure"} for row in assessments)
    successful = sum(row["outcome_status"] == "success" for row in assessments)
    open_count = sum(row["outcome_status"] == "open" for row in assessments)
    flat = sum(row["outcome_status"] == "flat" for row in assessments)
    unverifiable = sum(row["outcome_status"] == "unverifiable" for row in assessments)
    review_needed = sum(
        row["compliance_status"] != "compliant"
        or row["outcome_status"] == "failure"
        or row["deviation_type"] is not None
        for row in assessments
    )
    if status == "empty" and eligible != 0:
        raise StateContractError(f"{path} empty metrics cannot contain assessments")
    if status == "blocked" and eligible != 0:
        raise StateContractError(f"{path} blocked metrics cannot contain factual assessments")
    if status == "complete" and eligible == 0:
        raise StateContractError(f"{path} complete metrics require at least one assessment")
    if status in {"complete", "empty"} and gap is not None:
        raise StateContractError(f"{path} success metrics cannot include a gap")
    return {
        "eligible_episode_count": eligible,
        "covered_episode_count": covered,
        "assessable_episode_count": assessable,
        "compliant_episode_count": compliant,
        "resolved_episode_count": resolved,
        "successful_episode_count": successful,
        "open_episode_count": open_count,
        "flat_episode_count": flat,
        "unverifiable_episode_count": unverifiable,
        "review_needed_count": review_needed,
        "coverage_rate": _rate(covered, eligible),
        "execution_rate": _rate(compliant, assessable),
        "plan_win_rate": _rate(successful, resolved),
        "data_status": status,
        "gap": gap,
    }


def normalize_weekly_review_bundle(value: Any) -> Dict[str, Any]:
    """Validate one fixed weekly state projection before any database write."""

    keys = {
        "schema_version",
        "run_id",
        "review_key",
        "period_start",
        "period_end",
        "generated_at",
        "source_contract_version",
        "data_status",
        "plan_hash",
        "dependencies",
        "modules",
        "performance",
        "attributions",
        "cash_flow_aggregates",
        "review_items",
        "episode_assessments",
        "execution_metrics",
    }
    legacy_keys = keys - {"episode_assessments", "execution_metrics"}
    root = _strict_object(value, keys, legacy_keys, "$weekly")
    schema_version = root["schema_version"]
    if schema_version not in {
        "trading-review-weekly-state.v1",
        "trading-review-weekly-state.v2",
    }:
        raise StateContractError("$weekly.schema_version is unsupported")
    if schema_version == "trading-review-weekly-state.v2":
        missing = {"episode_assessments", "execution_metrics"} - set(root)
        if missing:
            raise StateContractError(
                "$weekly is missing fields: " + ", ".join(sorted(missing))
            )
    review_key = _text(root["review_key"], "$weekly.review_key")
    if not review_key.startswith("weekly:"):
        raise StateContractError("$weekly.review_key must start with weekly:")
    period_start = _date(root["period_start"], "$weekly.period_start")
    period_end = _date(root["period_end"], "$weekly.period_end")
    if period_start > period_end:
        raise StateContractError("$weekly.period_start must not be after period_end")
    data_status = _status(root["data_status"], "$weekly.data_status")
    if data_status not in {"complete", "partial", "blocked"}:
        raise StateContractError("$weekly.data_status must be complete, partial, or blocked")
    plan_hash = None if root["plan_hash"] is None else _sha256(root["plan_hash"], "$weekly.plan_hash")

    if not isinstance(root["dependencies"], list):
        raise StateContractError("$weekly.dependencies must be an array")
    dependencies = [
        _normalize_weekly_dependency(row, f"$weekly.dependencies[{index}]")
        for index, row in enumerate(root["dependencies"])
    ]
    dependency_keys = [
        (
            row["dataset"],
            row["period_start"],
            row["period_end"],
            row["contract_version"],
        )
        for row in dependencies
    ]
    if len(dependency_keys) != len(set(dependency_keys)):
        raise StateContractError("$weekly.dependencies contains a duplicate partition identity")
    dependencies.sort(
        key=lambda row: (
            row["dataset"],
            row["period_start"],
            row["period_end"],
            row["contract_version"],
        )
    )

    if not isinstance(root["modules"], list):
        raise StateContractError("$weekly.modules must be an array")
    modules = [
        _normalize_weekly_module(row, f"$weekly.modules[{index}]")
        for index, row in enumerate(root["modules"])
    ]
    module_names = [row["name"] for row in modules]
    if set(module_names) != set(WEEKLY_MODULES) or len(module_names) != len(WEEKLY_MODULES):
        raise StateContractError("$weekly.modules must contain every weekly module exactly once")
    modules.sort(key=lambda row: row["name"])
    module_statuses = {row["name"]: row["status"] for row in modules}

    performance = _normalize_weekly_performance(root["performance"], "$weekly.performance")
    if module_statuses["performance"] in {"empty", "blocked"}:
        if performance is not None:
            raise StateContractError("$weekly.performance must be null when its module is empty or blocked")
    elif performance is None:
        raise StateContractError("$weekly.performance is required for a factual performance module")
    elif module_statuses["performance"] == "complete" and performance["data_status"] != "complete":
        raise StateContractError("complete performance module requires complete facts")

    for field, normalizer, module_name in (
        ("attributions", _normalize_weekly_attribution, "attribution"),
        ("cash_flow_aggregates", _normalize_weekly_cash_flow, "cash_flow"),
        ("review_items", _normalize_weekly_review_item, "plan"),
    ):
        if not isinstance(root[field], list):
            raise StateContractError(f"$weekly.{field} must be an array")
        normalized_rows = [
            normalizer(row, f"$weekly.{field}[{index}]")
            for index, row in enumerate(root[field])
        ]
        if field == "attributions":
            attributions = normalized_rows
        elif field == "cash_flow_aggregates":
            cash_flow_aggregates = normalized_rows
        else:
            review_items = normalized_rows
        if module_statuses[module_name] in {"empty", "blocked"} and normalized_rows and field != "review_items":
            raise StateContractError(
                f"$weekly.{field} must be empty when {module_name} is empty or blocked"
            )
        if module_statuses[module_name] == "complete" and not normalized_rows:
            raise StateContractError(f"$weekly.{field} must contain facts for a complete module")

    attribution_keys = [(row["underlying"], row["instrument_group"]) for row in attributions]
    if len(attribution_keys) != len(set(attribution_keys)):
        raise StateContractError("$weekly.attributions contains a duplicate natural key")
    cash_keys = [(row["category"], row["currency"]) for row in cash_flow_aggregates]
    if len(cash_keys) != len(set(cash_keys)):
        raise StateContractError("$weekly.cash_flow_aggregates contains a duplicate natural key")

    if schema_version == "trading-review-weekly-state.v2":
        if performance is not None or attributions or cash_flow_aggregates:
            raise StateContractError(
                "weekly state v2 cannot contain performance, attribution, or cash-flow facts"
            )
        if not isinstance(root["episode_assessments"], list):
            raise StateContractError("$weekly.episode_assessments must be an array")
        episode_assessments = [
            _normalize_episode_assessment(row, f"$weekly.episode_assessments[{index}]")
            for index, row in enumerate(root["episode_assessments"])
        ]
        episode_keys = [
            (row["market_date"], row["underlying"], row["side"])
            for row in episode_assessments
        ]
        if len(episode_keys) != len(set(episode_keys)):
            raise StateContractError("$weekly.episode_assessments contains a duplicate natural key")
        execution_metrics = _normalize_execution_metrics(
            root["execution_metrics"], episode_assessments, "$weekly.execution_metrics"
        )
    else:
        if "episode_assessments" in root or "execution_metrics" in root:
            raise StateContractError("weekly state v1 cannot contain v2 execution fields")
        episode_assessments = []
        execution_metrics = None

    if data_status == "complete" and any(
        status not in {"complete", "empty"} for status in module_statuses.values()
    ):
        raise StateContractError("complete weekly review cannot contain non-success modules")
    if data_status == "partial" and all(
        status in {"complete", "empty"} for status in module_statuses.values()
    ):
        raise StateContractError("partial weekly review requires at least one non-success module")

    normalized = {
        "schema_version": schema_version,
        "run_id": _text(root["run_id"], "$weekly.run_id"),
        "review_key": review_key,
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": _timestamp(root["generated_at"], "$weekly.generated_at"),
        "source_contract_version": _text(
            root["source_contract_version"], "$weekly.source_contract_version"
        ),
        "data_status": data_status,
        "plan_hash": plan_hash,
        "dependencies": dependencies,
        "modules": modules,
        "performance": performance,
        "attributions": attributions,
        "cash_flow_aggregates": cash_flow_aggregates,
        "review_items": review_items,
        "episode_assessments": episode_assessments,
        "execution_metrics": execution_metrics,
    }
    digest_payload = {
        key: normalized[key]
        for key in (
            "review_key",
            "period_start",
            "period_end",
            "source_contract_version",
            "data_status",
            "plan_hash",
            "dependencies",
            "modules",
            "performance",
            "attributions",
            "cash_flow_aggregates",
            "review_items",
            "episode_assessments",
            "execution_metrics",
        )
    }
    if schema_version == "trading-review-weekly-state.v1":
        # Preserve historical v2 idempotency; no execution fields existed in
        # the original digest. New public runs accept only the v2 input.
        digest_payload.pop("episode_assessments")
        digest_payload.pop("execution_metrics")
    normalized["facts_hash"] = content_hash(digest_payload)
    normalized["dependency_hash"] = content_hash(dependencies)
    return normalized


class StateStore:
    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self.connection = connection
        self.path = path

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            _chmod_sqlite_files(self.path)

    @contextlib.contextmanager
    def _write(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except sqlite3.OperationalError as exc:
            self.connection.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise StateBusyError("state database writer lock timed out") from exc
            raise
        except Exception:
            self.connection.rollback()
            raise
        finally:
            _chmod_sqlite_files(self.path)

    def table_count(self, table: str) -> int:
        if table not in SCHEMA_TABLES:
            raise StateContractError("unsupported table")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _latest_partition(self, dataset: str, start: str, end: str, version: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM partitions
            WHERE dataset=? AND period_start=? AND period_end=? AND contract_version=?
            ORDER BY revision DESC LIMIT 1
            """,
            (dataset, start, end, version),
        ).fetchone()

    def partition_decision(self, dataset: str, start: str, end: str, version: str) -> str:
        row = self._latest_partition(dataset, _date(start, "period_start"), _date(end, "period_end"), _text(version, "contract_version"))
        return "cache_hit" if row is not None and row["status"] in REUSABLE_STATUSES else "retry"

    def latest_partition_identity(
        self,
        dataset: str,
        start: str,
        end: str,
        contract_version: str,
    ) -> Optional[Dict[str, Any]]:
        """Return lineage only; never expose the partition's fact rows."""

        dataset_name = _text(dataset, "dataset")
        period_start = _date(start, "period_start")
        period_end = _date(end, "period_end")
        version = _text(contract_version, "contract_version")
        row = self._latest_partition(dataset_name, period_start, period_end, version)
        if row is None:
            return None
        return {
            "dataset": dataset_name,
            "period_start": period_start,
            "period_end": period_end,
            "contract_version": version,
            "partition_revision": int(row["revision"]),
            "payload_hash": row["payload_hash"],
            "status": row["status"],
        }

    def get_trade_partition_snapshot(
        self,
        start: str,
        end: str,
        contract_version: str,
    ) -> Optional[Dict[str, Any]]:
        period_start = _date(start, "period_start")
        period_end = _date(end, "period_end")
        version = _text(contract_version, "contract_version")
        partition = self._latest_partition("trades", period_start, period_end, version)
        if partition is None:
            return None
        payload = [
            {
                "market_date": row["market_date"],
                "symbol": row["symbol"],
                "side": row["side"],
                "order_count": int(row["order_count"]),
                "execution_count": int(row["execution_count"]),
                "executed_quantity": row["executed_quantity"],
                "data_status": row["data_status"],
            }
            for row in self.connection.execute(
                """
                SELECT market_date, symbol, side, order_count, execution_count,
                       executed_quantity, data_status
                FROM trade_aggregates
                WHERE market_date>=? AND market_date<=? AND revision=?
                ORDER BY market_date, symbol, side
                """,
                (period_start, period_end, partition["revision"]),
            )
        ]
        normalized = _normalize_payload("trades", payload, partition["status"])
        if content_hash(normalized) != partition["payload_hash"]:
            raise StateContractError("cached trade partition facts do not match payload_hash")
        return {
            "status": partition["status"],
            "collected_at": partition["collected_at"],
            "payload": normalized,
            "error_category": partition["error_category"],
            "revision": int(partition["revision"]),
            "payload_hash": partition["payload_hash"],
        }

    def ingest_partition(
        self,
        *,
        dataset: str,
        period_start: str,
        period_end: str,
        contract_version: str,
        status: str,
        collected_at: str,
        payload: Any,
        error_category: Optional[str] = None,
    ) -> PartitionResult:
        dataset = _text(dataset, "dataset")
        start = _date(period_start, "period_start")
        end = _date(period_end, "period_end")
        if start > end:
            raise StateContractError("period_start must not be after period_end")
        version = _text(contract_version, "contract_version")
        normalized_status = _status(status, "status")
        collected = _timestamp(collected_at, "collected_at")
        if error_category is not None:
            error_category = _text(error_category, "error_category")
        if normalized_status in {"partial", "stale", "blocked"} and not error_category:
            raise StateContractError("non-success partition requires a sanitized error_category")
        normalized = _normalize_payload(dataset, payload, normalized_status)
        if dataset == "trades" and any(
            row["market_date"] < start or row["market_date"] > end
            for row in normalized
        ):
            raise StateContractError("trade market_date must remain inside the partition period")
        digest = content_hash(normalized)

        with self._write():
            latest = self._latest_partition(dataset, start, end, version)
            if (
                latest is not None
                and latest["payload_hash"] == digest
                and latest["status"] == normalized_status
                and latest["error_category"] == error_category
            ):
                return PartitionResult("reused", int(latest["revision"]), digest, normalized_status)
            # Fact tables use their natural key plus ``revision``. Allocate a
            # dataset-global revision so a repeated event on another review
            # date, or a contract-version change for the same trade date,
            # cannot collide with an earlier fact row.
            revision = int(
                self.connection.execute(
                    "SELECT COALESCE(MAX(revision), 0) + 1 FROM partitions WHERE dataset=?",
                    (dataset,),
                ).fetchone()[0]
            )
            supersedes = None if latest is None else int(latest["revision"])
            self.connection.execute(
                """
                INSERT INTO partitions(
                    dataset, period_start, period_end, contract_version, revision,
                    status, collected_at, payload_hash, error_category, supersedes_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (dataset, start, end, version, revision, normalized_status, collected, digest, error_category, supersedes),
            )
            self._write_facts(dataset, normalized, revision)
        return PartitionResult("written", revision, digest, normalized_status)

    def _write_facts(self, dataset: str, payload: Any, revision: int) -> None:
        if not payload:
            return
        if dataset == "account_snapshot":
            self.connection.execute(
                "INSERT INTO account_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["snapshot_at"], revision, payload["currency"], payload["net_assets"],
                    payload["cash"], payload["buying_power"], payload["data_status"],
                ),
            )
            return
        statements = {
            "positions_snapshot": (
                "INSERT INTO position_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                lambda row: (row["snapshot_at"], row["symbol"], revision, row["underlying"], row["instrument_type"], row["quantity"], row["data_status"]),
            ),
            "trades": (
                "INSERT INTO trade_aggregates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                lambda row: (row["market_date"], row["symbol"], row["side"], revision, row["order_count"], row["execution_count"], row["executed_quantity"], row["data_status"]),
            ),
            "market_snapshots": (
                "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                lambda row: (row["as_of"], row["symbol"], revision, row["value"], row["previous_close"], row["change_pct"], row["session"], row["proxy_for"], row["data_status"]),
            ),
            "relevant_events": (
                "INSERT INTO relevant_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                lambda row: (row["derived_event_key"], revision, row["et_at"], row["shanghai_at"], row["title"], row["status"], row["source_category"], row["impact_channel"], row["data_status"]),
            ),
        }
        statement = statements.get(dataset)
        if statement is None:
            raise StateContractError(f"unsupported dataset: {dataset}")
        sql, projector = statement
        self.connection.executemany(sql, [projector(row) for row in payload])

    def start_run(
        self,
        *,
        run_id: str,
        mode: str,
        period_start: str,
        period_end: str,
        started_at: str,
        data_status: str,
        source_contract_version: str,
    ) -> None:
        run_id = _text(run_id, "run_id")
        if mode not in {"daily", "weekly"}:
            raise StateContractError("mode must be daily or weekly")
        with self._write():
            self.connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL, ?, 'pending', ?)",
                (run_id, mode, _date(period_start, "period_start"), _date(period_end, "period_end"), _timestamp(started_at, "started_at"), _status(data_status, "data_status"), _text(source_contract_version, "source_contract_version")),
            )

    def finish_run(self, run_id: str, finished_at: str, data_status: str) -> None:
        with self._write():
            cursor = self.connection.execute(
                "UPDATE runs SET finished_at=?, data_status=? WHERE run_id=?",
                (_timestamp(finished_at, "finished_at"), _status(data_status, "data_status"), _text(run_id, "run_id")),
            )
            if cursor.rowcount != 1:
                raise StateContractError("run_id does not exist")

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (_text(run_id, "run_id"),),
        ).fetchone()
        return None if row is None else dict(row)

    def get_plan_version(
        self,
        plan_id: str,
        version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        key = _text(plan_id, "plan_id")
        if version is None:
            row = self.connection.execute(
                "SELECT * FROM plan_versions WHERE plan_id=? ORDER BY version DESC LIMIT 1",
                (key,),
            ).fetchone()
        else:
            normalized_version = _integer(version, "version")
            if normalized_version < 1:
                raise StateContractError("version must be positive")
            row = self.connection.execute(
                "SELECT * FROM plan_versions WHERE plan_id=? AND version=?",
                (key, normalized_version),
            ).fetchone()
        if row is None:
            return None
        zones = [
            {
                "kind": zone["zone_kind"],
                "low": zone["low"],
                "high": zone["high"],
                "currency": zone["currency"],
                "condition": zone["condition"],
                "derived_from": zone["derived_from"],
                "data_status": zone["data_status"],
            }
            for zone in self.connection.execute(
                """
                SELECT * FROM plan_zones
                WHERE plan_id=? AND plan_version=? ORDER BY zone_order
                """,
                (key, row["version"]),
            )
        ]
        return {
            "schema_version": "trading-plan-state.v1",
            "plan_id": row["plan_id"],
            "version": int(row["version"]),
            "plan_stage": row["plan_stage"],
            "underlying": row["underlying"],
            "direction": row["direction"],
            "setup_type": row["setup_type"],
            "plan_status": row["plan_status"],
            "generated_at": row["generated_at"],
            "effective_at": row["effective_at"],
            "confirmed_at": row["confirmed_at"],
            "expires_at": row["expires_at"],
            "evidence": {
                "evidence_id": row["evidence_id"],
                "source": row["evidence_source"],
                "as_of": row["evidence_as_of"],
                "timezone": row["evidence_timezone"],
                "adjustment": row["adjustment"],
                "bars_used": int(row["bars_used"]),
                "atr14": row["atr14"],
            },
            "constraints": {
                "minimum_reward_risk": row["minimum_reward_risk"],
                "max_invalidation_pct": row["max_invalidation_pct"],
            },
            "content_hash": row["content_hash"],
            "supersedes_version": row["supersedes_version"],
            "parent_plan_id": row["parent_plan_id"],
            "parent_plan_version": row["parent_plan_version"],
            "initial_buy_episode_key": row["initial_buy_episode_key"],
            "data_status": row["data_status"],
            "zones": zones,
        }

    def _verify_initial_buy(self, plan: Mapping[str, Any], parent: Mapping[str, Any]) -> None:
        """Verify the derived day/underlying/buy key against hash-checked facts.

        A boolean supplied by a caller is not execution evidence. No raw order
        identifiers or execution prices enter the plan projection.
        """
        parts = plan["initial_buy_episode_key"].split("|")
        if len(parts) != 3 or parts[1] != plan["underlying"] or parts[2] != "buy":
            raise StateContractError("initial buy key must be market_date|underlying|buy")
        market_date = _date(parts[0], "initial_buy_episode_key.market_date")
        partition = self.connection.execute(
            """SELECT * FROM partitions WHERE dataset='trades'
               AND period_start=? AND period_end=?
               ORDER BY collected_at DESC, revision DESC LIMIT 1""",
            (market_date, market_date),
        ).fetchone()
        if partition is None or partition["status"] != "complete":
            raise StateContractError("position_management requires a complete verified buy partition")
        snapshot = self.get_trade_partition_snapshot(
            market_date, market_date, partition["contract_version"]
        )
        if not any(
            row["symbol"] == plan["underlying"] and row["side"].lower() == "buy"
            and row["execution_count"] > 0 and Decimal(row["executed_quantity"]) > 0
            and row["data_status"] == "complete"
            for row in snapshot["payload"]
        ):
            raise StateContractError("position_management has no verified actual underlying buy")
        generated = dt.datetime.fromisoformat(plan["generated_at"].replace("Z", "+00:00"))
        collected = dt.datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00"))
        effective = dt.datetime.fromisoformat(parent["effective_at"].replace("Z", "+00:00"))
        day_start = dt.datetime.combine(dt.date.fromisoformat(market_date), dt.time.min,
                                        tzinfo=ZoneInfo("America/New_York"))
        if collected > generated or day_start > generated or effective > day_start:
            raise StateContractError("buy evidence must follow the parent plan and precede the management draft")

    def put_plan_version(self, plan: Any) -> PlanVersionResult:
        normalized = normalize_plan_version(plan)
        with self._write():
            existing = self.get_plan_version(normalized["plan_id"], normalized["version"])
            if existing is not None:
                if canonical_json(normalize_plan_version(existing)) != canonical_json(normalized):
                    raise StateContractError("plan version is immutable and already contains different facts")
                return PlanVersionResult(
                    "reused",
                    normalized["plan_id"],
                    normalized["version"],
                    normalized["content_hash"],
                    normalized["plan_status"],
                )

            latest = self.connection.execute(
                "SELECT * FROM plan_versions WHERE plan_id=? ORDER BY version DESC LIMIT 1",
                (normalized["plan_id"],),
            ).fetchone()
            if latest is None:
                if normalized["plan_status"] != "draft":
                    raise StateContractError("a new plan must first be persisted as a draft")
                if normalized["version"] != 1 or normalized["supersedes_version"] is not None:
                    raise StateContractError("the first persisted plan version must be version 1")
            else:
                latest_version = int(latest["version"])
                if (
                    normalized["version"] != latest_version + 1
                    or normalized["supersedes_version"] != latest_version
                ):
                    raise StateContractError("new plan version must append to the latest version")
                if normalized["plan_status"] in {"confirmed", "expired"}:
                    expected_prior_status = (
                        "draft" if normalized["plan_status"] == "confirmed" else "confirmed"
                    )
                    latest_projection = self.get_plan_version(
                        normalized["plan_id"], latest_version
                    )
                    latest_normalized = normalize_plan_version(latest_projection)
                    immutable_fields = {
                        "plan_id",
                        "plan_stage",
                        "underlying",
                        "direction",
                        "setup_type",
                        "generated_at",
                        "expires_at",
                        "evidence_id",
                        "evidence_source",
                        "evidence_as_of",
                        "evidence_timezone",
                        "adjustment",
                        "bars_used",
                        "atr14",
                        "minimum_reward_risk",
                        "max_invalidation_pct",
                        "content_hash",
                        "parent_plan_id",
                        "parent_plan_version",
                        "initial_buy_episode_key",
                        "data_status",
                        "zones",
                    }
                    if latest["plan_status"] != expected_prior_status or any(
                        latest_normalized[field] != normalized[field]
                        for field in immutable_fields
                    ):
                        raise StateContractError(
                            f"{normalized['plan_status']} transition must preserve exact plan content"
                        )

            if normalized["plan_stage"] == "position_management":
                parent = self.connection.execute(
                    "SELECT * FROM plan_versions WHERE plan_id=? AND version=?",
                    (normalized["parent_plan_id"], normalized["parent_plan_version"]),
                ).fetchone()
                if parent is None:
                    raise StateContractError("position_management parent plan does not exist")
                if parent["plan_status"] != "confirmed" or parent["plan_stage"] != "pre_entry":
                    raise StateContractError("position_management parent must be a confirmed pre_entry plan")
                if (
                    parent["underlying"] != normalized["underlying"]
                    or parent["direction"] != normalized["direction"]
                ):
                    raise StateContractError("position_management parent must match underlying and direction")
                self._verify_initial_buy(normalized, parent)

            self.connection.execute(
                """
                INSERT INTO plan_versions(
                    plan_id, version, plan_stage, underlying, direction, setup_type,
                    plan_status, generated_at, effective_at, confirmed_at, expires_at,
                    evidence_id, evidence_source, evidence_as_of, evidence_timezone,
                    adjustment, bars_used, atr14, minimum_reward_risk,
                    max_invalidation_pct, content_hash, supersedes_version,
                    parent_plan_id, parent_plan_version, initial_buy_episode_key,
                    data_status
                ) VALUES (
                    :plan_id, :version, :plan_stage, :underlying, :direction,
                    :setup_type, :plan_status, :generated_at, :effective_at,
                    :confirmed_at, :expires_at, :evidence_id, :evidence_source,
                    :evidence_as_of, :evidence_timezone, :adjustment, :bars_used,
                    :atr14, :minimum_reward_risk, :max_invalidation_pct,
                    :content_hash, :supersedes_version, :parent_plan_id,
                    :parent_plan_version, :initial_buy_episode_key, :data_status
                )
                """,
                normalized,
            )
            self.connection.executemany(
                """
                INSERT INTO plan_zones(
                    plan_id, plan_version, zone_order, zone_kind, low, high,
                    currency, condition, derived_from, data_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        normalized["plan_id"],
                        normalized["version"],
                        index,
                        zone["kind"],
                        zone["low"],
                        zone["high"],
                        zone["currency"],
                        zone["condition"],
                        zone["derived_from"],
                        zone["data_status"],
                    )
                    for index, zone in enumerate(normalized["zones"])
                ],
            )
        return PlanVersionResult(
            "written",
            normalized["plan_id"],
            normalized["version"],
            normalized["content_hash"],
            normalized["plan_status"],
        )

    def ingest_weekly_review(self, bundle: Any) -> WeeklyReviewResult:
        normalized = normalize_weekly_review_bundle(bundle)
        run = self.connection.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (normalized["run_id"],),
        ).fetchone()
        if run is None:
            raise StateContractError("weekly review run_id does not exist")
        if run["mode"] != "weekly":
            raise StateContractError("weekly review run must use mode=weekly")
        if (
            run["period_start"] != normalized["period_start"]
            or run["period_end"] != normalized["period_end"]
        ):
            raise StateContractError("weekly review period does not match its run")
        if run["source_contract_version"] != normalized["source_contract_version"]:
            raise StateContractError("weekly review source contract does not match its run")

        for assessment in normalized["episode_assessments"]:
            if not (
                normalized["period_start"]
                <= assessment["market_date"]
                <= normalized["period_end"]
            ):
                raise StateContractError("weekly episode market_date must remain inside the review period")
            if assessment["coverage_status"] != "covered":
                continue
            plan = self.connection.execute(
                "SELECT * FROM plan_versions WHERE plan_id=? AND version=?",
                (assessment["plan_id"], assessment["plan_version"]),
            ).fetchone()
            if plan is None:
                raise StateContractError("covered weekly episode plan does not exist")
            if plan["plan_status"] != "confirmed" or plan["effective_at"] is None:
                raise StateContractError("covered weekly episode requires a confirmed effective plan")
            effective_at = dt.datetime.fromisoformat(
                plan["effective_at"].replace("Z", "+00:00")
            )
            expiry = dt.datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00"))
            day_start = dt.datetime.combine(
                dt.date.fromisoformat(assessment["market_date"]), dt.time.min,
                tzinfo=ZoneInfo("America/New_York"),
            )
            day_end = day_start + dt.timedelta(days=1)
            if effective_at > day_start or expiry < day_end:
                raise StateContractError(
                    "date-only episode requires a plan effective throughout its market date"
                )
            if plan["underlying"] != assessment["underlying"]:
                raise StateContractError("covered weekly episode underlying does not match its plan")

        for dependency in normalized["dependencies"]:
            partition = self.connection.execute(
                """
                SELECT payload_hash FROM partitions
                WHERE dataset=? AND period_start=? AND period_end=?
                  AND contract_version=? AND revision=?
                """,
                (
                    dependency["dataset"],
                    dependency["period_start"],
                    dependency["period_end"],
                    dependency["contract_version"],
                    dependency["partition_revision"],
                ),
            ).fetchone()
            if partition is None:
                raise StateContractError("weekly review dependency partition does not exist")
            if partition["payload_hash"] != dependency["payload_hash"]:
                raise StateContractError("weekly review dependency payload_hash does not match the partition")
            latest = self._latest_partition(
                dependency["dataset"],
                dependency["period_start"],
                dependency["period_end"],
                dependency["contract_version"],
            )
            if latest is None or int(latest["revision"]) != dependency["partition_revision"]:
                raise StateContractError("weekly review dependency must use the latest partition revision")

        metrics = normalized["execution_metrics"]
        if metrics is not None and metrics["data_status"] != "blocked":
            eligible_keys = set()
            for dependency in normalized["dependencies"]:
                if dependency["dataset"] != "trades":
                    continue
                snapshot = self.get_trade_partition_snapshot(
                    dependency["period_start"], dependency["period_end"],
                    dependency["contract_version"],
                )
                if metrics["data_status"] in {"complete", "empty"} and snapshot["status"] not in REUSABLE_STATUSES:
                    raise StateContractError("complete execution metrics require complete trade coverage")
                for row in snapshot["payload"]:
                    if row["execution_count"] <= 0 or Decimal(row["executed_quantity"]) <= 0:
                        continue
                    side = row["side"].lower()
                    if side not in {"buy", "sell"}:
                        continue
                    eligible_keys.add((row["market_date"], row["symbol"].removesuffix(":OPTION"), side))
            assessment_keys = {
                (row["market_date"], row["underlying"], row["side"])
                for row in normalized["episode_assessments"]
            }
            if eligible_keys != assessment_keys:
                raise StateContractError("execution assessments must match every verified eligible trade episode")

        with self._write():
            latest = self.connection.execute(
                """
                SELECT * FROM weekly_reviews
                WHERE review_key=?
                ORDER BY revision DESC LIMIT 1
                """,
                (normalized["review_key"],),
            ).fetchone()
            if (
                latest is not None
                and latest["facts_hash"] == normalized["facts_hash"]
                and latest["plan_hash"] == normalized["plan_hash"]
                and latest["dependency_hash"] == normalized["dependency_hash"]
                and latest["data_status"] == normalized["data_status"]
                and latest["source_contract_version"] == normalized["source_contract_version"]
            ):
                return WeeklyReviewResult(
                    "reused",
                    int(latest["revision"]),
                    normalized["facts_hash"],
                    normalized["dependency_hash"],
                    normalized["data_status"],
                )

            revision = 1 if latest is None else int(latest["revision"]) + 1
            supersedes = None if latest is None else int(latest["revision"])
            self.connection.execute(
                """
                INSERT INTO weekly_reviews(
                    review_key, revision, run_id, period_start, period_end,
                    generated_at, source_contract_version, facts_hash, plan_hash,
                    dependency_hash, data_status, supersedes_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["review_key"],
                    revision,
                    normalized["run_id"],
                    normalized["period_start"],
                    normalized["period_end"],
                    normalized["generated_at"],
                    normalized["source_contract_version"],
                    normalized["facts_hash"],
                    normalized["plan_hash"],
                    normalized["dependency_hash"],
                    normalized["data_status"],
                    supersedes,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO weekly_review_dependencies VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        normalized["review_key"],
                        revision,
                        row["dataset"],
                        row["period_start"],
                        row["period_end"],
                        row["contract_version"],
                        row["partition_revision"],
                        row["payload_hash"],
                    )
                    for row in normalized["dependencies"]
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO weekly_module_statuses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        normalized["review_key"],
                        revision,
                        row["name"],
                        row["status"],
                        row["requested_start"],
                        row["requested_end"],
                        row["returned_start"],
                        row["returned_end"],
                        row["error_category"],
                    )
                    for row in normalized["modules"]
                ],
            )
            performance = normalized["performance"]
            if performance is not None:
                self.connection.execute(
                    "INSERT INTO weekly_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        normalized["review_key"],
                        revision,
                        performance["currency"],
                        performance["initial_asset_value"],
                        performance["ending_asset_value"],
                        performance["profit"],
                        performance["profit_rate"],
                        performance["time_weighted_return"],
                        performance["invest_amount"],
                        performance["mechanical_asset_change"],
                        performance["reconciliation_residual"],
                        performance["requested_utc_start"],
                        performance["requested_utc_end"],
                        performance["returned_utc_start"],
                        performance["returned_utc_end"],
                        performance["data_status"],
                    ),
                )
            self.connection.executemany(
                "INSERT INTO weekly_attributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        normalized["review_key"],
                        revision,
                        row["underlying"],
                        row["instrument_group"],
                        row["display_name"],
                        row["profit"],
                        row["underlying_profit"],
                        row["derivatives_profit"],
                        row["currency"],
                        row["data_status"],
                    )
                    for row in normalized["attributions"]
                ],
            )
            self.connection.executemany(
                "INSERT INTO weekly_cash_flow_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        normalized["review_key"],
                        revision,
                        row["category"],
                        row["currency"],
                        row["amount"],
                        row["row_count"],
                        row["data_status"],
                    )
                    for row in normalized["cash_flow_aggregates"]
                ],
            )
            self.connection.executemany(
                "INSERT INTO weekly_review_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        normalized["review_key"],
                        revision,
                        index,
                        row["item_kind"],
                        row["subject"],
                        row["summary"],
                        row["evidence_boundary"],
                        row["evidence_kind"],
                        row["data_status"],
                    )
                    for index, row in enumerate(normalized["review_items"])
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO trade_episode_assessments VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    (
                        normalized["review_key"],
                        revision,
                        index,
                        row["market_date"],
                        row["underlying"],
                        row["side"],
                        row["plan_id"],
                        row["plan_version"],
                        row["coverage_status"],
                        row["compliance_status"],
                        row["outcome_status"],
                        row["deviation_type"],
                        row["reason"],
                        row["next_rule"],
                        row["data_status"],
                    )
                    for index, row in enumerate(normalized["episode_assessments"])
                ],
            )
            execution_metrics = normalized["execution_metrics"]
            if execution_metrics is not None:
                self.connection.execute(
                    """
                    INSERT INTO weekly_execution_metrics VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        normalized["review_key"],
                        revision,
                        execution_metrics["eligible_episode_count"],
                        execution_metrics["covered_episode_count"],
                        execution_metrics["assessable_episode_count"],
                        execution_metrics["compliant_episode_count"],
                        execution_metrics["resolved_episode_count"],
                        execution_metrics["successful_episode_count"],
                        execution_metrics["open_episode_count"],
                        execution_metrics["flat_episode_count"],
                        execution_metrics["unverifiable_episode_count"],
                        execution_metrics["review_needed_count"],
                        execution_metrics["coverage_rate"],
                        execution_metrics["execution_rate"],
                        execution_metrics["plan_win_rate"],
                        execution_metrics["data_status"],
                        execution_metrics["gap"],
                    ),
                )
        return WeeklyReviewResult(
            "written",
            revision,
            normalized["facts_hash"],
            normalized["dependency_hash"],
            normalized["data_status"],
        )

    def weekly_review_freshness(self, review_key: str, revision: int) -> Dict[str, Any]:
        key = _text(review_key, "review_key")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise StateContractError("weekly review revision must be positive")
        exists = self.connection.execute(
            "SELECT 1 FROM weekly_reviews WHERE review_key=? AND revision=?",
            (key, revision),
        ).fetchone()
        if exists is None:
            raise StateContractError("weekly review does not exist")
        changed: List[Dict[str, Any]] = []
        dependencies = self.connection.execute(
            """
            SELECT * FROM weekly_review_dependencies
            WHERE review_key=? AND review_revision=?
            ORDER BY dataset, period_start, period_end, contract_version
            """,
            (key, revision),
        ).fetchall()
        for dependency in dependencies:
            latest = self._latest_partition(
                dependency["dataset"],
                dependency["period_start"],
                dependency["period_end"],
                dependency["contract_version"],
            )
            if (
                latest is None
                or int(latest["revision"]) != int(dependency["partition_revision"])
                or latest["payload_hash"] != dependency["payload_hash"]
            ):
                changed.append(
                    {
                        "dataset": dependency["dataset"],
                        "period_start": dependency["period_start"],
                        "period_end": dependency["period_end"],
                    }
                )
        return {
            "status": "stale" if changed else "current",
            "changed_dependencies": changed,
        }

    def get_weekly_review(
        self,
        review_key: str,
        revision: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        key = _text(review_key, "review_key")
        if revision is None:
            review = self.connection.execute(
                "SELECT * FROM weekly_reviews WHERE review_key=? ORDER BY revision DESC LIMIT 1",
                (key,),
            ).fetchone()
        else:
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise StateContractError("weekly review revision must be positive")
            review = self.connection.execute(
                "SELECT * FROM weekly_reviews WHERE review_key=? AND revision=?",
                (key, revision),
            ).fetchone()
        if review is None:
            return None
        review_revision = int(review["revision"])
        performance = self.connection.execute(
            "SELECT * FROM weekly_performance WHERE review_key=? AND review_revision=?",
            (key, review_revision),
        ).fetchone()
        execution_metrics = self.connection.execute(
            """
            SELECT * FROM weekly_execution_metrics
            WHERE review_key=? AND review_revision=?
            """,
            (key, review_revision),
        ).fetchone()
        confirmation = self.connection.execute(
            """
            SELECT * FROM confirmations
            WHERE review_key=? ORDER BY confirmation_version DESC LIMIT 1
            """,
            (key,),
        ).fetchone()
        confirmation_status = "pending"
        confirmation_version = None
        confirmed_at = None
        if confirmation is not None and confirmation["facts_hash"] == review["facts_hash"]:
            confirmation_status = confirmation["confirmation_status"]
            confirmation_version = int(confirmation["confirmation_version"])
            confirmed_at = confirmation["confirmed_at"]
        freshness = self.weekly_review_freshness(key, review_revision)
        return {
            **dict(review),
            "revision": review_revision,
            "freshness": freshness,
            "confirmation_status": confirmation_status,
            "confirmation_version": confirmation_version,
            "confirmed_at": confirmed_at,
            "dependencies": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT * FROM weekly_review_dependencies
                    WHERE review_key=? AND review_revision=?
                    ORDER BY dataset, period_start, period_end, contract_version
                    """,
                    (key, review_revision),
                )
            ],
            "modules": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT * FROM weekly_module_statuses
                    WHERE review_key=? AND review_revision=? ORDER BY module_name
                    """,
                    (key, review_revision),
                )
            ],
            "performance": None if performance is None else dict(performance),
            "attributions": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT * FROM weekly_attributions
                    WHERE review_key=? AND review_revision=?
                    ORDER BY CAST(profit AS REAL) DESC, underlying, instrument_group
                    """,
                    (key, review_revision),
                )
            ],
            "cash_flow_aggregates": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT * FROM weekly_cash_flow_aggregates
                    WHERE review_key=? AND review_revision=? ORDER BY category, currency
                    """,
                    (key, review_revision),
                )
            ],
            "review_items": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT * FROM weekly_review_items
                    WHERE review_key=? AND review_revision=? ORDER BY item_index
                    """,
                    (key, review_revision),
                )
            ],
            "episode_assessments": [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT * FROM trade_episode_assessments
                    WHERE review_key=? AND review_revision=? ORDER BY episode_index
                    """,
                    (key, review_revision),
                )
            ],
            "execution_metrics": (
                None if execution_metrics is None else dict(execution_metrics)
            ),
        }

    def confirm(
        self,
        review_key: str,
        confirmation_version: int,
        confirmation_status: str,
        confirmed_at: Optional[str],
        facts_hash: str,
        supersedes_version: Optional[int] = None,
    ) -> None:
        if confirmation_status not in CONFIRMATION_STATUSES:
            raise StateContractError("unsupported confirmation status")
        if (
            isinstance(confirmation_version, bool)
            or not isinstance(confirmation_version, int)
            or confirmation_version < 1
        ):
            raise StateContractError("confirmation_version must be positive")
        if confirmation_status == "confirmed" and confirmed_at is None:
            raise StateContractError("confirmed status requires confirmed_at")
        if confirmation_status == "pending" and confirmed_at is not None:
            raise StateContractError("pending status cannot include confirmed_at")
        if confirmed_at is not None:
            confirmed_at = _timestamp(confirmed_at, "confirmed_at")
        if supersedes_version is not None:
            if (
                isinstance(supersedes_version, bool)
                or not isinstance(supersedes_version, int)
                or supersedes_version < 1
                or supersedes_version >= confirmation_version
            ):
                raise StateContractError(
                    "supersedes_version must be a positive earlier confirmation version"
                )
        with self._write():
            self.connection.execute(
                "INSERT INTO confirmations VALUES (?, ?, ?, ?, ?, ?)",
                (_text(review_key, "review_key"), confirmation_version, confirmation_status, confirmed_at, _text(facts_hash, "facts_hash"), supersedes_version),
            )

    def put_analysis(
        self,
        facts_hash: str,
        plan_hash: str,
        contract_version: str,
        output: Any,
        model: str,
        generated_at: str,
        data_status: str,
    ) -> str:
        normalized = _normalize_analysis(output)
        encoded = canonical_json(normalized)
        key = (_text(facts_hash, "facts_hash"), _text(plan_hash, "plan_hash"), _text(contract_version, "contract_version"))
        normalized_model = _text(model, "model")
        normalized_generated_at = _timestamp(generated_at, "generated_at")
        normalized_status = _status(data_status, "data_status")
        existing = self.connection.execute(
            """
            SELECT output_json, model, generated_at, data_status
            FROM analysis_snapshots
            WHERE facts_hash=? AND plan_hash=? AND contract_version=?
            """,
            key,
        ).fetchone()
        if existing is not None:
            if (
                existing["output_json"] != encoded
                or existing["model"] != normalized_model
                or existing["generated_at"] != normalized_generated_at
                or existing["data_status"] != normalized_status
            ):
                raise StateContractError(
                    "analysis cache key already exists with a different snapshot; reuse the original snapshot"
                )
            return "reused"
        with self._write():
            self.connection.execute(
                "INSERT INTO analysis_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, encoded, normalized_model, normalized_generated_at, normalized_status),
            )
        return "written"

    def get_analysis(self, facts_hash: str, plan_hash: str, contract_version: str) -> Optional[Dict[str, Any]]:
        snapshot = self.get_analysis_snapshot(facts_hash, plan_hash, contract_version)
        return None if snapshot is None else snapshot["output"]

    def get_analysis_snapshot(
        self,
        facts_hash: str,
        plan_hash: str,
        contract_version: str,
    ) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            """
            SELECT output_json, model, generated_at, data_status
            FROM analysis_snapshots
            WHERE facts_hash=? AND plan_hash=? AND contract_version=?
            """,
            (
                _text(facts_hash, "facts_hash"),
                _text(plan_hash, "plan_hash"),
                _text(contract_version, "contract_version"),
            ),
        ).fetchone()
        if row is None:
            return None
        return {
            "status": row["data_status"],
            "model": row["model"],
            "generated_at": row["generated_at"],
            "output": json.loads(row["output_json"]),
        }

    def aggregate_weekly_trades(self, expected_dates: Sequence[str], contract_version: str) -> Dict[str, Any]:
        if not expected_dates:
            raise StateContractError("expected trade dates must not be empty")
        dates = [_date(value, "expected_trade_date") for value in expected_dates]
        if len(set(dates)) != len(dates):
            raise StateContractError("expected trade dates must be unique")
        version = _text(contract_version, "contract_version")
        missing: List[str] = []
        totals: Dict[tuple[str, str], Dict[str, Any]] = {}
        for market_date in dates:
            snapshot = self.get_trade_partition_snapshot(
                market_date,
                market_date,
                version,
            )
            if snapshot is None or snapshot["status"] not in REUSABLE_STATUSES:
                missing.append(market_date)
                continue
            if snapshot["status"] == "empty":
                continue
            for row in snapshot["payload"]:
                key = (row["symbol"], row["side"])
                target = totals.setdefault(
                    key,
                    {"symbol": row["symbol"], "side": row["side"], "order_count": 0, "execution_count": 0, "executed_quantity": Decimal("0")},
                )
                target["order_count"] += int(row["order_count"])
                target["execution_count"] += int(row["execution_count"])
                target["executed_quantity"] += Decimal(row["executed_quantity"])
        rows = [
            {**value, "executed_quantity": format(value["executed_quantity"], "f")}
            for _, value in sorted(totals.items())
        ]
        return {
            "status": "complete" if not missing else "partial",
            "expected_dates": dates,
            "missing_dates": missing,
            "rows": rows,
        }


def _normalize_analysis(value: Any) -> Dict[str, Any]:
    keys = {"headline", "facts", "interpretation", "risks", "checks", "gaps"}
    item = _strict_object(value, keys, keys, "$.analysis")

    def text_list(raw: Any, path: str) -> List[str]:
        if not isinstance(raw, list):
            raise StateContractError(f"{path} must be an array")
        return [_text(entry, f"{path}[{index}]") for index, entry in enumerate(raw)]

    checks: List[Dict[str, Any]] = []
    check_keys = {"if", "then", "else", "evidence_refs", "boundary"}
    if not isinstance(item["checks"], list):
        raise StateContractError("$.analysis.checks must be an array")
    for index, raw in enumerate(item["checks"]):
        check = _strict_object(raw, check_keys, check_keys, f"$.analysis.checks[{index}]")
        checks.append(
            {
                "if": _text(check["if"], f"$.analysis.checks[{index}].if"),
                "then": _text(check["then"], f"$.analysis.checks[{index}].then"),
                "else": _text(check["else"], f"$.analysis.checks[{index}].else"),
                "evidence_refs": text_list(check["evidence_refs"], f"$.analysis.checks[{index}].evidence_refs"),
                "boundary": _text(check["boundary"], f"$.analysis.checks[{index}].boundary"),
            }
        )
    return {
        "headline": _text(item["headline"], "$.analysis.headline"),
        "facts": text_list(item["facts"], "$.analysis.facts"),
        "interpretation": text_list(item["interpretation"], "$.analysis.interpretation"),
        "risks": text_list(item["risks"], "$.analysis.risks"),
        "checks": checks,
        "gaps": text_list(item["gaps"], "$.analysis.gaps"),
    }
