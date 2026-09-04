#!/usr/bin/env python3
"""Validate, migrate, and extract immutable confirmed-plan versions.

The migration mode accepts the private legacy authority JSON and emits one
owner-only Markdown version.  The extract mode reads only immutable Markdown
versions and emits a small owner-only JSON envelope for the deterministic
execution projector.  Neither mode prints plan content or calls a broker,
Vault API, database, or network service.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping


LEGACY_SCHEMA = "trading-review-confirmed-authority.v1"
PLAN_SCHEMA = "daily-trade-journal-confirmed-plan.v1"
PLAN_INPUT_SCHEMA = "daily-trade-journal-plan-input.v1"
MARKER_START = "<!-- daily-trade-journal:confirmed-plan:start -->"
MARKER_END = "<!-- daily-trade-journal:confirmed-plan:end -->"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
VERSION_RE = re.compile(r"\d{4}-\d{2}-\d{2}-\d{6}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")

LEGACY_KEYS = frozenset(
    {
        "approved_draft_hash",
        "approved_draft_schema_version",
        "approved_interview",
        "confirmation_status",
        "confirmed_at",
        "review_date",
        "review_type",
        "schema_version",
        "scope",
        "source",
        "source_contract_version",
    }
)
INTERVIEW_KEYS = frozenset(
    {
        "candidates",
        "daily_review_workflow",
        "generated_at",
        "global_rules",
        "holdings",
        "open_questions",
        "review_date",
        "schema_version",
        "source",
        "status",
        "strategy_categories",
        "valuation_summary",
    }
)
SCOPE_KEYS = frozenset(
    {
        "alternative_budget",
        "broker_access",
        "external_writes",
        "timeframe_policy",
        "unformed_candidates",
        "write_boundary",
    }
)
PLAN_KEYS = frozenset(
    {
        "underlying",
        "action",
        "actions",
        "tool",
        "status",
        "confirmed",
        "prohibited",
        "confirmed_at",
        "effective_at",
        "expires_at",
        "plan_stage",
    }
)
PLAN_VERSION_KEYS = frozenset(
    {
        "schema_version",
        "version",
        "review_date",
        "status",
        "confirmation_status",
        "confirmed_at",
        "effective_at",
        "source_schema",
        "source_content_hash",
        "approved_draft_schema_version",
        "approved_draft_hash",
        "plans",
        "source_snapshot",
    }
)
SOURCE_SNAPSHOT_KEYS = frozenset({"candidates", "holdings", "global_rules", "open_questions"})
RICH_PLAN_KEYS = frozenset(
    {
        "action",
        "confirmation_gap",
        "data_status",
        "display_symbol",
        "pa_reference",
        "status",
        "timeframe",
        "tool_kind",
        "trigger",
        "user_thought",
    }
)
RICH_PLAN_SOURCE_SCHEMA = "trading-review-human-confirmed-summary.v1"
ACTION_ALIASES = {"buy", "sell", "买入", "卖出"}
TOOL_ALIASES = {
    "stock",
    "equity",
    "正股",
    "single_stock_leveraged_etf",
    "leveraged_etf",
    "单股杠杆 ETF",
    "zero_dte_option",
    "0dte",
    "0DTE 期权",
    "other_option",
    "option",
    "其他期权",
    "unknown",
    "无法识别",
}


class PlanError(ValueError):
    """Input cannot be migrated or extracted safely."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanError("duplicate JSON key")
        result[key] = value
    return result


def _parse_json(content: bytes) -> Any:
    if len(content) > MAX_INPUT_BYTES:
        raise PlanError("input exceeds limit")

    def invalid_constant(_: str) -> None:
        raise PlanError("non-finite JSON value")

    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=invalid_constant,
        )
    except PlanError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise PlanError("invalid JSON input") from exc


def _owner_path(path_value: str | os.PathLike[str], *, directory: bool = False) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.abspath(path))
    if path.is_symlink():
        raise PlanError("symbolic links are not accepted")
    try:
        path = path.resolve(strict=False)
        info = path.lstat()
    except OSError as exc:
        raise PlanError("path cannot be read") from exc
    if directory:
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise PlanError("directory identity is not owner-safe")
        if info.st_mode & 0o022:
            raise PlanError("directory is group/other writable")
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PlanError("file identity is not owner-safe")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PlanError("file mode must be 0600")
        if info.st_size > MAX_INPUT_BYTES:
            raise PlanError("input exceeds limit")
    return path


def _read_owner_json(path_value: str | os.PathLike[str]) -> Any:
    path = _owner_path(path_value)
    before = path.lstat()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise PlanError("input cannot be opened") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise PlanError("input changed during read")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise PlanError("input cannot be read") from exc
    finally:
        os.close(fd)
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise PlanError("input changed during read")
    return _parse_json(content)


def _read_owner_text(path_value: str | os.PathLike[str]) -> str:
    path = _owner_path(path_value)
    before = path.lstat()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise PlanError("input cannot be opened") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise PlanError("input changed during read")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise PlanError("input cannot be read") from exc
    finally:
        os.close(fd)
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise PlanError("input changed during read")
    if len(content) > MAX_INPUT_BYTES:
        raise PlanError("input exceeds limit")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlanError("input is not UTF-8") from exc


def _parse_date(value: Any) -> dt.date:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise PlanError("date must be YYYY-MM-DD")
    try:
        result = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PlanError("date is invalid") from exc
    if result.isoformat() != value:
        raise PlanError("date is invalid")
    return result


def _parse_instant(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        raise PlanError("timestamp must be strict RFC3339")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanError("timestamp is invalid") from exc
    if result.tzinfo is None:
        raise PlanError("timestamp needs a timezone")
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise PlanError("value cannot be canonicalized") from exc


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{name} must be a non-empty string")
    return value


def _validate_legacy(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict) or set(source) != LEGACY_KEYS:
        raise PlanError("legacy authority fields are unsupported")
    if source["schema_version"] != LEGACY_SCHEMA:
        raise PlanError("legacy authority schema is unsupported")
    if source["confirmation_status"] != "confirmed":
        raise PlanError("legacy authority is not confirmed")
    review = _parse_date(source["review_date"])
    confirmed_at = _parse_instant(source["confirmed_at"])
    if not isinstance(source["approved_draft_hash"], str) or not HASH_RE.fullmatch(source["approved_draft_hash"]):
        raise PlanError("approved draft hash is invalid")
    _require_string(source["approved_draft_schema_version"], "approved draft schema version")
    _require_string(source["review_type"], "review type")
    _require_string(source["source"], "source")
    _require_string(source["source_contract_version"], "source contract version")
    scope = source["scope"]
    if not isinstance(scope, dict) or set(scope) != SCOPE_KEYS:
        raise PlanError("legacy authority scope is unsupported")
    interview = source["approved_interview"]
    if not isinstance(interview, dict) or set(interview) != INTERVIEW_KEYS:
        raise PlanError("approved interview fields are unsupported")
    if interview["review_date"] != review.isoformat():
        raise PlanError("approved interview date conflicts")
    if interview["schema_version"] != source["approved_draft_schema_version"]:
        raise PlanError("approved interview schema conflicts")
    for key, expected in (("candidates", list), ("holdings", list), ("global_rules", dict), ("open_questions", list)):
        if not isinstance(interview[key], expected):
            raise PlanError(f"approved interview {key} has unsupported type")
        if len(interview[key]) > MAX_ROWS:
            raise PlanError(f"approved interview {key} exceeds limit")
    if not all(isinstance(item, dict) for item in interview["candidates"] + interview["holdings"]):
        raise PlanError("approved interview rows are invalid")
    if not all(isinstance(item, str) for item in interview["open_questions"]):
        raise PlanError("approved interview questions are invalid")
    for key in ("daily_review_workflow", "valuation_summary"):
        if not isinstance(interview[key], dict):
            raise PlanError(f"approved interview {key} has unsupported type")
    for key in ("strategy_categories",):
        if not isinstance(interview[key], list) or len(interview[key]) > MAX_ROWS:
            raise PlanError(f"approved interview {key} has unsupported type")
    for key in ("generated_at", "schema_version", "source", "status", "review_date"):
        _require_string(interview[key], f"approved interview {key}")
    expected_hash = hashlib.sha256(_canonical(interview)).hexdigest()
    if expected_hash != source["approved_draft_hash"]:
        raise PlanError("approved draft hash does not match canonical interview")
    return {"source": source, "review": review, "confirmed_at": confirmed_at, "interview": interview}


def _safe_symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,20}\.US", value) else None


def _normalized_action(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned in {"buy", "purchase", "买入"}:
        return "买入"
    if cleaned in {"sell", "sale", "卖出"}:
        return "卖出"
    return None


def _normalized_tool(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    aliases = {
        "stock": "正股",
        "equity": "正股",
        "正股": "正股",
        "single_stock_leveraged_etf": "单股杠杆 ETF",
        "leveraged_etf": "单股杠杆 ETF",
        "单股杠杆 ETF": "单股杠杆 ETF",
        "zero_dte_option": "0DTE 期权",
        "0dte": "0DTE 期权",
        "0DTE 期权": "0DTE 期权",
        "other_option": "其他期权",
        "option": "其他期权",
        "其他期权": "其他期权",
        "unknown": "无法识别",
        "无法识别": "无法识别",
    }
    return aliases.get(cleaned) or aliases.get(cleaned.lower())


def _display_underlying(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,20}", cleaned):
        return cleaned if cleaned.endswith(".US") else f"{cleaned}.US"
    return None


def _actual_underlying(value: Any) -> str | None:
    direct = _safe_symbol(value)
    if direct is not None:
        return direct
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([A-Z][A-Z0-9.\-]{0,20})\s+option", value.strip(), re.IGNORECASE)
    return f"{match.group(1).upper()}.US" if match else None


def _rich_tool(value: Any) -> str | None:
    normalized = _normalized_tool(value)
    if normalized is not None:
        return normalized
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    lowered = cleaned.lower()
    if "待确认" in cleaned or lowered.startswith("沿用"):
        return None
    if lowered in {"actual_broker_call", "user_label_long_call_actual_broker_call"}:
        return "Call"
    if "long call" in lowered:
        return "Call"
    if "long put" in lowered:
        return "Put"
    if lowered == "stock" or cleaned.startswith("Stock"):
        return "正股"
    return None


def _rich_actions(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    actions: list[str] = []
    if "加仓" in value or value.strip() in {"计划买入", "买入"}:
        actions.append("买入")
    if "减仓" in value or "退出" in value or value.strip() == "卖出":
        actions.append("卖出")
    return tuple(actions)


def _snapshot_identity_rows(snapshot: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for key in ("holdings", "candidates"):
        for row in snapshot[key]:
            if isinstance(row, dict):
                yield row


def _update_identity_registry(snapshot: Mapping[str, Any], registry: dict[str, tuple[str, str]]) -> None:
    for row in _snapshot_identity_rows(snapshot):
        display = _display_underlying(row.get("display_symbol"))
        actual = _actual_underlying(row.get("actual_trade_symbol")) or display
        tool = _rich_tool(row.get("tool")) or _rich_tool(row.get("tool_kind"))
        if display is None or actual is None or tool is None:
            continue
        registry[display] = (actual, tool)


def _role_underlyings(
    rows: Iterable[Mapping[str, Any]],
    registry: Mapping[str, tuple[str, str]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    holdings: set[str] = set()
    observations: set[str] = set()
    ignored: set[str] = set()
    context: set[str] = set()
    for row in rows:
        display = _display_underlying(row.get("display_symbol"))
        if display is None:
            continue
        underlying = registry.get(display, (display, ""))[0]
        context.add(underlying)
        status = str(row.get("status", "")).strip().lower()
        action = str(row.get("action", "")).strip()
        if status == "ignored" or action.lower() == "no specific plan":
            ignored.add(underlying)
        elif status == "holding_management_intent" or action.startswith("持仓管理"):
            holdings.add(underlying)
        else:
            observations.add(underlying)
    return sorted(holdings), sorted(observations), sorted(ignored), sorted(context)


def _normalize_rich_plans(
    rows: list[Any],
    *,
    confirmed_at: dt.datetime,
    effective_at: dt.datetime,
    registry: Mapping[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not set(row) <= RICH_PLAN_KEYS:
            raise PlanError("rich plan row fields are unsupported")
        display = _display_underlying(row.get("display_symbol"))
        if display is None:
            raise PlanError("rich plan display symbol is invalid")
        status = str(row.get("status", "")).strip()
        action_value = row.get("action")
        actions = _rich_actions(action_value)
        is_holding = status == "holding_management_intent" or (
            isinstance(action_value, str) and action_value.startswith("持仓管理")
        )
        # Observation rows remain context.  They are not promoted into exact
        # execution plans merely because their containing version is confirmed.
        if not is_holding or not actions:
            continue
        inherited = registry.get(display)
        underlying = inherited[0] if inherited is not None else display
        tool = _rich_tool(row.get("tool_kind")) or (inherited[1] if inherited is not None else None)
        if tool is None:
            # The holding plan still remains visible as context, but cannot be
            # exact alignment evidence without a confirmed tool identity.
            continue
        result.append(
            {
                "underlying": underlying,
                "actions": list(actions),
                "tool": tool,
                "status": "confirmed",
                "confirmed_at": confirmed_at.isoformat(),
                "effective_at": effective_at.isoformat(),
                "plan_stage": "holding_management",
            }
        )
    return result


def _explicit_confirmed(row: Mapping[str, Any]) -> bool:
    """Apply only explicit exclusion markers to an outer-confirmed row.

    The legacy outer envelope already proves confirmation of the interview;
    ordinary business ``status`` values therefore never become a second
    confirmation gate.  Explicit row-level false/draft/pending evidence still
    prevents promotion.
    """

    if "confirmed" in row and type(row["confirmed"]) is not bool:
        return False
    if row.get("confirmed") is False:
        return False
    for key in ("confirmation_status", "plan_status"):
        if key not in row:
            continue
        value = row[key]
        if not isinstance(value, str):
            return False
        normalized = value.strip().lower()
        if normalized in {"draft", "pending"}:
            return False
        if normalized not in {"confirmed", "active"}:
            return False
    return True


def _extract_explicit_plans(interview: Mapping[str, Any], confirmed_at: dt.datetime) -> list[dict[str, Any]]:
    """Only promote rows that are explicit, normalized, and confirmed.

    The legacy interview is retained verbatim in the source snapshot.  A row
    becomes alignment evidence only when its symbol, action and tool are
    independently normalizable; explicit row-level false/draft/pending markers
    still exclude it from the outer-confirmed envelope.
    """

    result: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], bool] = {}
    for collection_name in ("candidates", "holdings"):
        collection = interview[collection_name]
        for row in collection:
            if not isinstance(row, dict):
                continue
            action = _normalized_action(row.get("action")) or _normalized_action(row.get("side"))
            tool = _normalized_tool(row.get("tool")) or _normalized_tool(row.get("tool_kind"))
            if "actual_trade_symbol" in row:
                symbol = _safe_symbol(row["actual_trade_symbol"])
            else:
                symbol = _safe_symbol(row.get("display_symbol"))
            if action is None or tool is None or tool == "无法识别" or symbol is None:
                continue
            key = (symbol, action, tool)
            eligible = _explicit_confirmed(row)
            if key in seen and seen[key] != eligible:
                raise PlanError("conflicting explicit plan row")
            if key in seen:
                # Exact duplicates are deterministic no-ops, including exact
                # duplicates that are explicitly excluded from promotion.
                continue
            seen[key] = eligible
            if not eligible:
                continue
            result.append(
                {
                    "underlying": symbol,
                    "action": action,
                    "tool": tool,
                    "status": "confirmed",
                    "confirmed_at": confirmed_at.isoformat(),
                    "effective_at": confirmed_at.isoformat(),
                }
            )
    return result


def _version_id(confirmed_at: dt.datetime) -> str:
    shanghai = confirmed_at.astimezone(dt.timezone(dt.timedelta(hours=8)))
    return shanghai.strftime("%Y-%m-%d-%H%M%S")


def _migration_payload(validated: Mapping[str, Any]) -> dict[str, Any]:
    source = validated["source"]
    interview = validated["interview"]
    review = validated["review"]
    confirmed_at = validated["confirmed_at"]
    return {
        "schema_version": PLAN_SCHEMA,
        "version": _version_id(confirmed_at),
        "review_date": review.isoformat(),
        "status": "confirmed",
        "confirmation_status": "confirmed",
        "confirmed_at": confirmed_at.isoformat(),
        "effective_at": confirmed_at.isoformat(),
        "source_schema": source["schema_version"],
        "source_content_hash": hashlib.sha256(_canonical(interview)).hexdigest(),
        "approved_draft_schema_version": source["approved_draft_schema_version"],
        "approved_draft_hash": source["approved_draft_hash"],
        "plans": _extract_explicit_plans(interview, confirmed_at),
        "source_snapshot": {
            "candidates": interview["candidates"],
            "holdings": interview["holdings"],
            "global_rules": interview["global_rules"],
            "open_questions": interview["open_questions"],
        },
    }


def _markdown(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return "\n".join(
        [
            "---",
            "type: confirmed-trading-plan",
            f"plan_version: {payload['version']}",
            f"review_date: {payload['review_date']}",
            "confirmation_status: confirmed",
            f"confirmed_at: {payload['confirmed_at']}",
            f"effective_at: {payload['effective_at']}",
            "---",
            "",
            f"# 已确认交易计划 · {payload['version']}",
            "",
            "本文件是不可变的确认计划版本。机器读取仅使用下方唯一结构化区块。",
            "迁移保留旧 authority 的 candidates、holdings、global_rules 和 open_questions；未被明确规范化确认的内容不会参与成交对齐。",
            "",
            MARKER_START,
            encoded,
            MARKER_END,
            "",
        ]
    )


def _atomic_write(path_value: str | os.PathLike[str], content: bytes, *, replace: bool = True) -> None:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.abspath(path))
    parent = path.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise PlanError("output directory cannot be read") from exc
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022:
        raise PlanError("output directory is not owner-safe")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PlanError("output identity is not safe")
        if not replace:
            raise PlanError("immutable version already exists")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Link creation is the no-clobber primitive: unlike a pre-check
            # followed by rename, it fails atomically if a concurrent writer
            # has created this immutable version.
            os.link(temporary, path)
            temporary.unlink()
        os.chmod(path, 0o600)
        info = path.lstat()
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PlanError("output permissions are unsafe")
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def migrate(source_path: str, output_path: str) -> dict[str, Any]:
    validated = _validate_legacy(_read_owner_json(source_path))
    payload = _migration_payload(validated)
    _atomic_write(output_path, _markdown(payload).encode("utf-8"), replace=False)
    return {"status": "complete", "version": payload["version"], "plan_rows": len(payload["plans"])}


def _validate_snapshot(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != SOURCE_SNAPSHOT_KEYS:
        raise PlanError("source snapshot fields are unsupported")
    if not isinstance(value["candidates"], list) or not isinstance(value["holdings"], list):
        raise PlanError("source snapshot collections are invalid")
    if not isinstance(value["global_rules"], dict) or not isinstance(value["open_questions"], list):
        raise PlanError("source snapshot fields are invalid")
    if not all(isinstance(item, dict) for item in value["candidates"] + value["holdings"]):
        raise PlanError("source snapshot rows are invalid")
    if not all(isinstance(item, str) for item in value["open_questions"]):
        raise PlanError("source snapshot questions are invalid")
    if len(value["candidates"]) > MAX_ROWS or len(value["holdings"]) > MAX_ROWS or len(value["open_questions"]) > MAX_ROWS:
        raise PlanError("source snapshot exceeds limit")


def _validate_plan_row(row: Any, inherited_confirmed_at: dt.datetime, inherited_effective_at: dt.datetime) -> dict[str, Any]:
    if not isinstance(row, dict) or not set(row) <= PLAN_KEYS:
        raise PlanError("plan row fields are unsupported")
    if "underlying" not in row or "tool" not in row or ("action" not in row and "actions" not in row):
        raise PlanError("plan row is incomplete")
    if _safe_symbol(row["underlying"]) is None:
        raise PlanError("plan underlying is invalid")
    action_values = row.get("actions")
    if action_values is not None:
        if (
            "action" in row
            or not isinstance(action_values, list)
            or not action_values
            or any(_normalized_action(item) is None for item in action_values)
        ):
            raise PlanError("plan actions are invalid")
    elif _normalized_action(row["action"]) is None:
        raise PlanError("plan action is invalid")
    if _normalized_tool(row["tool"]) is None and row["tool"] not in {"Call", "Put"}:
        raise PlanError("plan tool is invalid")
    if "status" in row and row["status"] not in {"confirmed", "active"}:
        raise PlanError("plan row is not confirmed")
    if "confirmed" in row and row["confirmed"] is not True:
        raise PlanError("plan row confirmation is invalid")
    for key in ("confirmed_at", "effective_at", "expires_at"):
        if key in row:
            _parse_instant(row[key])
    if "plan_stage" in row and row["plan_stage"] != "holding_management":
        raise PlanError("plan stage is invalid")
    return dict(row)


def _parse_version(payload: Any, identity_registry: dict[str, tuple[str, str]] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != PLAN_VERSION_KEYS:
        raise PlanError("plan version fields are unsupported")
    if payload["schema_version"] != PLAN_SCHEMA or payload["status"] != "confirmed" or payload["confirmation_status"] != "confirmed":
        raise PlanError("plan version is not confirmed")
    if not isinstance(payload["version"], str) or not VERSION_RE.fullmatch(payload["version"]):
        raise PlanError("plan version identifier is invalid")
    _parse_date(payload["review_date"])
    confirmed_at = _parse_instant(payload["confirmed_at"])
    effective_at = _parse_instant(payload["effective_at"])
    _require_string(payload["source_schema"], "source schema")
    _require_string(payload["approved_draft_schema_version"], "approved draft schema version")
    for key in ("source_content_hash", "approved_draft_hash"):
        if not isinstance(payload[key], str) or not HASH_RE.fullmatch(payload[key]):
            raise PlanError(f"{key} is invalid")
    _validate_snapshot(payload["source_snapshot"])
    if not isinstance(payload["plans"], list) or len(payload["plans"]) > MAX_ROWS:
        raise PlanError("plan rows are invalid")
    registry = identity_registry if identity_registry is not None else {}
    _update_identity_registry(payload["source_snapshot"], registry)
    if payload["source_schema"] == RICH_PLAN_SOURCE_SCHEMA:
        normalized_rows = _normalize_rich_plans(
            payload["plans"],
            confirmed_at=confirmed_at,
            effective_at=effective_at,
            registry=registry,
        )
    else:
        normalized_rows = payload["plans"]
    rows = [_validate_plan_row(row, confirmed_at, effective_at) for row in normalized_rows]
    seen = set()
    for row in rows:
        actions = tuple(row.get("actions", [row.get("action")]))
        key = (row["underlying"], actions, row["tool"], row.get("effective_at"), row.get("expires_at"))
        if key in seen:
            raise PlanError("duplicate plan row")
        seen.add(key)
    source_snapshot = payload["source_snapshot"]
    context_available = any(bool(source_snapshot[key]) for key in SOURCE_SNAPSHOT_KEYS)
    role_rows = [*source_snapshot["holdings"], *source_snapshot["candidates"]]
    if payload["source_schema"] == RICH_PLAN_SOURCE_SCHEMA:
        role_rows = payload["plans"]
    holding_underlyings, observation_underlyings, ignored_underlyings, context_underlyings = _role_underlyings(
        role_rows,
        registry,
    )
    role_underlyings = set(context_underlyings)
    tool_by_underlying = {
        actual: tool
        for actual, tool in registry.values()
        if actual in role_underlyings
    }
    return {
        "schema_version": PLAN_SCHEMA,
        "version": payload["version"],
        "review_date": payload["review_date"],
        "status": "confirmed",
        "confirmation_status": "confirmed",
        "confirmed_at": payload["confirmed_at"],
        "effective_at": payload["effective_at"],
        "source_schema": payload["source_schema"],
        "source_content_hash": payload["source_content_hash"],
        "approved_draft_schema_version": payload["approved_draft_schema_version"],
        "approved_draft_hash": payload["approved_draft_hash"],
        "plans": rows,
        # This is an extractor-only signal.  The immutable Markdown keeps the
        # validated source snapshot; the projector receives only this boolean,
        # never the snapshot or its free text.
        "context_available": context_available,
        "holding_underlyings": holding_underlyings,
        "observation_underlyings": observation_underlyings,
        "ignored_underlyings": ignored_underlyings,
        "context_underlyings": context_underlyings,
        "tool_by_underlying": tool_by_underlying,
    }


def _extract_markdown(text: str, identity_registry: dict[str, tuple[str, str]] | None = None) -> dict[str, Any]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line == MARKER_START]
    ends = [i for i, line in enumerate(lines) if line == MARKER_END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise PlanError("managed plan block is missing or duplicated")
    body = "\n".join(lines[starts[0] + 1 : ends[0]]).strip().encode("utf-8")
    payload = _parse_json(body)
    return _parse_version(payload, identity_registry)


def extract(plans_dir: str, output_path: str) -> dict[str, Any]:
    directory = _owner_path(plans_dir, directory=True)
    paths = sorted(directory.glob("*.md"), key=lambda item: item.name)
    output = Path(output_path).expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    try:
        output = Path(os.path.abspath(output)).resolve(strict=False)
        for path in paths:
            if path.is_symlink():
                raise PlanError("symbolic links are not accepted")
        input_paths = {path.resolve(strict=True) for path in paths}
    except (OSError, RuntimeError) as exc:
        raise PlanError("output path cannot be normalized") from exc
    if output.suffix.lower() == ".md" or output in input_paths:
        raise PlanError("extract output conflicts with immutable plan version")
    versions: list[dict[str, Any]] = []
    seen: set[str] = set()
    identity_registry: dict[str, tuple[str, str]] = {}
    for path in paths:
        if path.is_symlink():
            raise PlanError("symbolic links are not accepted")
        version = _extract_markdown(_read_owner_text(path), identity_registry)
        if version["version"] in seen:
            raise PlanError("duplicate plan version")
        seen.add(version["version"])
        versions.append(version)
    versions.sort(key=lambda item: (item["confirmed_at"], item["effective_at"], item["version"]))
    result = {"schema_version": PLAN_INPUT_SCHEMA, "versions": versions}
    _atomic_write(output_path, (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return {"status": "complete", "versions": len(versions), "plan_rows": sum(len(item["plans"]) for item in versions)}


def _blocked(output_path: str | None) -> None:
    if not output_path:
        return
    try:
        # Failed runs must not overwrite an existing artifact; the non-zero
        # exit status remains the failure signal when no envelope can be written.
        _atomic_write(
            output_path,
            (json.dumps({"schema_version": PLAN_INPUT_SCHEMA, "status": "blocked", "versions": []}, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
            replace=False,
        )
    except Exception:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and extract immutable confirmed daily-trade-journal plans")
    parser.add_argument("--mode", choices=("migrate", "extract"), required=True)
    parser.add_argument("--source-authority")
    parser.add_argument("--plans-dir")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.mode == "migrate":
            if not args.source_authority or args.plans_dir:
                raise PlanError("migrate requires source authority only")
            result = migrate(args.source_authority, args.output)
        else:
            if not args.plans_dir or args.source_authority:
                raise PlanError("extract requires plans directory only")
            result = extract(args.plans_dir, args.output)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (PlanError, OSError, TypeError, ValueError, json.JSONDecodeError):
        _blocked(args.output)
        return 2


if __name__ == "__main__":
    sys.exit(main())
