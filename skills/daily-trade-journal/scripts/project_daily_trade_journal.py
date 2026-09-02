#!/usr/bin/env python3
"""Project synthetic/private execution input into a minimal daily journal fact set.

This module is deliberately offline.  It accepts already collected JSON only;
it never calls a broker, reads a database, or writes an Obsidian note.  Raw
execution fields are admitted only long enough to validate and classify them,
then are discarded before the public result is built.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "daily-trade-journal-facts.v1"
STATUSES = frozenset({"complete", "empty", "blocked"})
ALIGNMENTS = frozenset({"按计划", "偏离计划", "无法核对"})
TOOLS = frozenset({"正股", "单股杠杆 ETF", "0DTE 期权", "其他期权", "无法识别"})
NY_TZ = ZoneInfo("America/New_York")
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,20}\.US\Z")
OCC_PATTERNS = (
    re.compile(
        r"^(?P<underlying>[A-Z][A-Z0-9.\-]{0,20}\.US)"
        r"(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d{1,20})(?:\.US)?\Z"
    ),
    re.compile(
        r"^(?P<underlying>[A-Z][A-Z0-9.\-]{0,20})"
        r"(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d{1,20})(?:\.US)?\Z"
    ),
)

RAW_ENVELOPE_KEYS = frozenset(
    {
        "executions",
        "review_date",
        "status",
        "calendar",
        "plans",
        "confirmed_plans",
        "weekly_plan",
        "intraday_revisions",
    }
)
RAW_ROW_KEYS = frozenset(
    {
        "symbol",
        "side",
        "time",
        "executed_at",
        "filled_at",
        "order_id",
        "execution_id",
        "id",
        "price",
        "quantity",
        "qty",
        "executed_quantity",
        "filled_quantity",
        "commission",
        "fee",
        "fees",
        "cost",
        "currency",
        "instrument",
        "underlying",
    }
)
INSTRUMENT_KEYS = frozenset({"tool_kind", "underlying"})
PLAN_ENVELOPE_KEYS = frozenset(
    {
        "plans",
        "confirmed_plans",
        "weekly_plan",
        "intraday_revisions",
        "revisions",
        "status",
        "review_date",
    }
)
PLAN_ROW_KEYS = frozenset(
    {
        "underlying",
        "action",
        "actions",
        "side",
        "tool",
        "tool_kind",
        "status",
        "plan_status",
        "confirmation_status",
        "confirmed",
        "prohibited",
        "forbidden",
        "allowed",
        "effective_at",
        "confirmed_at",
        "expires_at",
        "review_date",
        "market_date",
        "date",
        "revision",
        "revision_of",
        "plan_stage",
    }
)

ACTION_ALIASES = {
    "buy": "买入",
    "买入": "买入",
    "purchase": "买入",
    "sell": "卖出",
    "卖出": "卖出",
    "sale": "卖出",
}
TOOL_ALIASES = {
    "stock": "正股",
    "equity": "正股",
    "正股": "正股",
    "single_stock_leveraged_etf": "单股杠杆 ETF",
    "leveraged_etf": "单股杠杆 ETF",
    "single-stock leveraged etf": "单股杠杆 ETF",
    "单股杠杆ETF": "单股杠杆 ETF",
    "单股杠杆 ETF": "单股杠杆 ETF",
    "zero_dte_option": "0DTE 期权",
    "0dte": "0DTE 期权",
    "0DTE": "0DTE 期权",
    "0DTE 期权": "0DTE 期权",
    "other_option": "其他期权",
    "option": "其他期权",
    "other option": "其他期权",
    "leap_call": "其他期权",
    "long_call": "其他期权",
    "Long Call": "其他期权",
    "其他期权": "其他期权",
    "unknown": "无法识别",
    "无法识别": "无法识别",
}
CONFIRMED_STATUSES = frozenset({"confirmed", "active"})
NONCONFIRMED_STATUSES = frozenset(
    {"draft", "pending", "superseded", "expired", "cancelled", "blocked"}
)
CALENDAR_KEYS = frozenset(
    {
        "trading_dates",
        "dates",
        "sessions",
        "date",
        "review_date",
        "is_trading_day",
        "completed",
        "status",
        "start_at",
        "end_at",
        "session_start",
        "session_end",
        "window_start",
        "window_end",
    }
)


class ProjectionError(ValueError):
    """Input cannot be safely projected into the public contract."""


class Window(NamedTuple):
    start: dt.datetime
    end: dt.datetime


class ExecutionFact(NamedTuple):
    underlying: str
    action: str
    tool: str
    instant: dt.datetime


class PlanFact(NamedTuple):
    underlying: str
    actions: tuple[str, ...]
    tool: str | None
    prohibited: bool
    confirmed: bool
    effective_at: dt.datetime | None
    confirmed_at: dt.datetime | None
    expires_at: dt.datetime | None
    market_date: dt.date | None
    order: int


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectionError("duplicate JSON key")
        result[key] = value
    return result


def parse_json_bytes(content: bytes) -> Any:
    if len(content) > MAX_INPUT_BYTES:
        raise ProjectionError("input exceeds limit")

    def invalid_constant(_: str) -> None:
        raise ProjectionError("non-finite JSON value")

    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=invalid_constant,
        )
    except ProjectionError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise ProjectionError("invalid JSON input") from exc


def _absolute_no_links(path_value: str | os.PathLike[str]) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.abspath(path))
    # macOS exposes common temporary directories through harmless ancestor
    # links (for example /var -> /private/var).  Reject the file itself when
    # it is a link, then resolve ancestors once so reads/writes stay bound to
    # the actual destination rather than following a link at operation time.
    if path.is_symlink():
        raise ProjectionError("symbolic links are not accepted")
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise ProjectionError("path cannot be resolved") from exc


def read_input_json(path_value: str | os.PathLike[str]) -> Any:
    path = _absolute_no_links(path_value)
    try:
        before = path.lstat()
    except OSError as exc:
        raise ProjectionError("input cannot be read") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1:
        raise ProjectionError("input file identity is not owner-only")
    if before.st_mode & 0o002:
        raise ProjectionError("input file is world-writable")
    if before.st_size > MAX_INPUT_BYTES:
        raise ProjectionError("input exceeds limit")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ProjectionError("input cannot be opened") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise ProjectionError("input changed during read")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(MAX_INPUT_BYTES + 1)
        if len(content) > MAX_INPUT_BYTES:
            raise ProjectionError("input exceeds limit")
    except OSError as exc:
        raise ProjectionError("input cannot be read") from exc
    finally:
        os.close(fd)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ProjectionError("input changed during read") from exc
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise ProjectionError("input changed during read")
    return parse_json_bytes(content)


def parse_date(value: Any) -> dt.date:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ProjectionError("review date must be YYYY-MM-DD")
    try:
        result = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ProjectionError("review date is invalid") from exc
    if result.isoformat() != value:
        raise ProjectionError("review date is invalid")
    return result


def parse_instant(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        raise ProjectionError("timestamp must be strict RFC3339")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectionError("timestamp is invalid") from exc
    if result.tzinfo is None:
        raise ProjectionError("timestamp needs a timezone")
    return result


def normalize_action(value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectionError("action is invalid")
    cleaned = value.strip()
    normalized = ACTION_ALIASES.get(cleaned) or ACTION_ALIASES.get(cleaned.lower())
    if normalized is None:
        raise ProjectionError("action is unsupported")
    return normalized


def normalize_tool(value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectionError("tool is invalid")
    cleaned = value.strip()
    normalized = TOOL_ALIASES.get(cleaned) or TOOL_ALIASES.get(cleaned.lower())
    if normalized is None:
        raise ProjectionError("tool is unsupported")
    return normalized


def normalize_underlying(value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectionError("underlying is invalid")
    value = value.strip().upper()
    if not US_TICKER_RE.fullmatch(value):
        raise ProjectionError("underlying is not a US ticker")
    return value


def _parse_symbol(value: Any, review_date: dt.date) -> tuple[str, bool, bool]:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ProjectionError("execution symbol is invalid")
    symbol = value.strip().upper()
    for pattern in OCC_PATTERNS:
        match = pattern.fullmatch(symbol)
        if match is None:
            continue
        underlying_value = match.group("underlying")
        if not underlying_value.endswith(".US"):
            underlying_value += ".US"
        underlying = normalize_underlying(underlying_value)
        try:
            expiry = dt.datetime.strptime(match.group("expiry"), "%y%m%d").date()
        except ValueError as exc:
            raise ProjectionError("option expiry is invalid") from exc
        return underlying, expiry == review_date, True
    return normalize_underlying(symbol), False, False


def _safe_instrument(value: Any, *, underlying: str, is_option: bool, same_day: bool) -> str | None:
    if not isinstance(value, dict) or set(value) != INSTRUMENT_KEYS:
        raise ProjectionError("instrument evidence has an unsupported structure")
    evidence_underlying = normalize_underlying(value["underlying"])
    if evidence_underlying != underlying:
        raise ProjectionError("instrument evidence conflicts with execution")
    tool_kind = value["tool_kind"]
    if not isinstance(tool_kind, str):
        raise ProjectionError("instrument evidence has an unsupported tool")
    normalized = normalize_tool(tool_kind)
    if is_option:
        if normalized not in {"0DTE 期权", "其他期权"}:
            raise ProjectionError("option instrument evidence conflicts with execution")
        if same_day and normalized != "0DTE 期权":
            # A provider label cannot downgrade a mechanically same-day option.
            return "0DTE 期权"
        if not same_day and normalized == "0DTE 期权":
            raise ProjectionError("non-zero-day option conflicts with instrument evidence")
        return normalized
    if normalized not in {"正股", "单股杠杆 ETF", "无法识别"}:
        raise ProjectionError("equity instrument evidence conflicts with execution")
    return normalized


def project_execution(row: Any, review_date: str | dt.date) -> ExecutionFact:
    review = parse_date(review_date) if isinstance(review_date, str) else review_date
    if not isinstance(review, dt.date) or isinstance(review, dt.datetime):
        raise ProjectionError("review date is invalid")
    if not isinstance(row, dict) or not set(row) <= RAW_ROW_KEYS:
        raise ProjectionError("execution row contains unsupported fields")
    if "symbol" not in row or "side" not in row:
        raise ProjectionError("execution row is missing required facts")
    timestamp_keys = [key for key in ("time", "executed_at", "filled_at") if key in row]
    if len(timestamp_keys) != 1:
        raise ProjectionError("execution row needs one timestamp")
    instant = parse_instant(row[timestamp_keys[0]])
    local_date = instant.astimezone(NY_TZ).date()
    if local_date != review:
        raise ProjectionError("execution is outside the review date")
    action = normalize_action(row["side"])
    underlying, same_day, is_option = _parse_symbol(row["symbol"], review)
    if "underlying" in row and normalize_underlying(row["underlying"]) != underlying:
        raise ProjectionError("execution underlying conflicts with symbol")
    explicit = None
    if "instrument" in row:
        explicit = _safe_instrument(
            row["instrument"],
            underlying=underlying,
            is_option=is_option,
            same_day=same_day,
        )
    if is_option:
        # The date comparison always wins and no option right or contract is retained.
        tool = "0DTE 期权" if same_day else "其他期权"
        if explicit is not None and explicit != tool:
            raise ProjectionError("option tool evidence conflicts with expiry")
    elif explicit is None:
        tool = "无法识别"
    else:
        tool = explicit
    return ExecutionFact(underlying=underlying, action=action, tool=tool, instant=instant)


safe_execution = project_execution


def _extract_execution_rows(value: Any) -> tuple[list[Any], Any | None, list[Any] | None]:
    if isinstance(value, list):
        return value, None, None
    if not isinstance(value, dict) or not set(value) <= RAW_ENVELOPE_KEYS:
        raise ProjectionError("execution input envelope is unsupported")
    if "executions" not in value or not isinstance(value["executions"], list):
        raise ProjectionError("execution input needs an executions array")
    status = value.get("status")
    if status is not None and status not in STATUSES:
        raise ProjectionError("execution input status is unsupported")
    if status == "blocked":
        raise ProjectionError("execution input is blocked")
    if status == "empty" and value["executions"]:
        raise ProjectionError("empty execution input contains rows")
    embedded_calendar = value.get("calendar")
    plan_values: list[Any] = []
    for key in ("plans", "confirmed_plans", "weekly_plan", "intraday_revisions"):
        if key in value:
            plan_values.append(value[key])
    return value["executions"], embedded_calendar, plan_values or None


def _default_window(review_date: dt.date) -> Window:
    start = dt.datetime.combine(review_date, dt.time.min, tzinfo=NY_TZ)
    return Window(start=start, end=start + dt.timedelta(days=1))


def _parse_calendar_dates(value: Any) -> set[dt.date]:
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ProjectionError("calendar dates are invalid")
    dates: set[dt.date] = set()
    for item in value:
        if isinstance(item, str):
            dates.add(parse_date(item))
            continue
        if isinstance(item, dict):
            if not set(item) <= CALENDAR_KEYS or "date" not in item:
                raise ProjectionError("calendar entry is unsupported")
            dates.add(parse_date(item["date"]))
            continue
        raise ProjectionError("calendar date is invalid")
    return dates


def _calendar_window_fields(value: Mapping[str, Any], review_date: dt.date) -> Window:
    pairs = (("start_at", "end_at"), ("session_start", "session_end"), ("window_start", "window_end"))
    present = [(start, end) for start, end in pairs if start in value or end in value]
    if len(present) > 1 or present and any(key not in value for pair in present for key in pair):
        raise ProjectionError("calendar window is incomplete or ambiguous")
    if not present:
        return _default_window(review_date)
    start_key, end_key = present[0]
    start, end = parse_instant(value[start_key]), parse_instant(value[end_key])
    if start >= end:
        raise ProjectionError("calendar window is invalid")
    return Window(start=start, end=end)


def _calendar_entry(entry: Mapping[str, Any], review_date: dt.date) -> Window:
    if not set(entry) <= CALENDAR_KEYS:
        raise ProjectionError("calendar entry contains unsupported fields")
    entry_date_value = entry.get("date", entry.get("review_date", review_date.isoformat()))
    if parse_date(entry_date_value) != review_date:
        raise ProjectionError("calendar does not identify the review date")
    if entry.get("is_trading_day") is False or entry.get("completed") is False:
        raise ProjectionError("review date is not a completed trading day")
    status = entry.get("status")
    if isinstance(status, str):
        status = status.lower()
    if status in {"open", "trading"} and entry.get("completed") is not True:
        raise ProjectionError("open calendar session is not completed")
    if status is not None and status not in {"complete", "success", "open", "trading", "confirmed"}:
        raise ProjectionError("calendar status is not complete")
    return _calendar_window_fields(entry, review_date)


def parse_calendar(value: Any, review_date: str | dt.date) -> Window:
    review = parse_date(review_date) if isinstance(review_date, str) else review_date
    if value is None:
        raise ProjectionError("calendar artifact is required")
    if isinstance(value, list):
        if not value:
            raise ProjectionError("calendar has no entries")
        if all(isinstance(item, str) for item in value):
            dates = _parse_calendar_dates(value)
            if review not in dates:
                raise ProjectionError("review date is not a trading date")
            return _default_window(review)
        matches = []
        for item in value:
            if not isinstance(item, dict):
                raise ProjectionError("calendar entry is invalid")
            if item.get("date", item.get("review_date")) == review.isoformat():
                matches.append(item)
        if len(matches) != 1:
            raise ProjectionError("calendar review date is missing or ambiguous")
        return _calendar_entry(matches[0], review)
    if not isinstance(value, dict) or not set(value) <= CALENDAR_KEYS:
        raise ProjectionError("calendar input is unsupported")
    if "sessions" in value:
        sessions = value["sessions"]
        if not isinstance(sessions, list):
            raise ProjectionError("calendar sessions are invalid")
        matches = [item for item in sessions if isinstance(item, dict) and item.get("date") == review.isoformat()]
        if len(matches) != 1:
            raise ProjectionError("calendar review date is missing or ambiguous")
        return _calendar_entry(matches[0], review)
    for date_key in ("trading_dates", "dates"):
        if date_key in value:
            dates = _parse_calendar_dates(value[date_key])
            if review not in dates:
                raise ProjectionError("review date is not a trading date")
            return _calendar_window_fields(value, review)
    if any(key in value for key in ("date", "review_date", "is_trading_day", "completed", "status")):
        return _calendar_entry(value, review)
    # A pure window is still a valid calendar artifact when the caller has
    # already bound it to review_date.
    return _calendar_window_fields(value, review)


def _status_value(row: Mapping[str, Any]) -> str | None:
    values = []
    for key in ("status", "plan_status", "confirmation_status"):
        if key in row:
            value = row[key]
            if not isinstance(value, str):
                raise ProjectionError("plan status is invalid")
            values.append(value.strip().lower())
    if len(set(values)) > 1:
        # confirmation_status=confirmed with plan_status=confirmed is okay;
        # all other conflicting statuses are unresolved evidence.
        if not (set(values) <= CONFIRMED_STATUSES):
            raise ProjectionError("plan statuses conflict")
    return values[0] if values else None


def _plan_actions(row: Mapping[str, Any], prohibited: bool) -> tuple[str, ...]:
    candidates: list[tuple[str, ...]] = []
    if "action" in row:
        action_value = row["action"]
        if isinstance(action_value, str) and action_value.strip() in {"禁止", "prohibited", "forbidden", "do_not_trade"}:
            prohibited = True
        else:
            candidates.append((normalize_action(action_value),))
    if "side" in row:
        candidates.append((normalize_action(row["side"]),))
    if "actions" in row:
        action_values = row["actions"]
        if not isinstance(action_values, list) or not action_values:
            raise ProjectionError("plan actions are invalid")
        candidates.append(tuple(dict.fromkeys(normalize_action(item) for item in action_values)))
    if candidates and len({tuple(item) for item in candidates}) != 1:
        raise ProjectionError("plan actions conflict")
    if candidates:
        return candidates[0]
    if prohibited:
        return ()
    raise ProjectionError("plan action is missing")


def _plan_tool(row: Mapping[str, Any], prohibited: bool) -> str | None:
    candidates = []
    for key in ("tool", "tool_kind"):
        if key in row:
            candidates.append(normalize_tool(row[key]))
    if len(set(candidates)) > 1:
        raise ProjectionError("plan tools conflict")
    if candidates:
        return candidates[0]
    if prohibited:
        return None
    raise ProjectionError("plan tool is missing")


def _optional_instant(row: Mapping[str, Any], key: str) -> dt.datetime | None:
    return parse_instant(row[key]) if key in row and row[key] is not None else None


def parse_plan_row(row: Any, *, order: int) -> PlanFact:
    if not isinstance(row, dict) or not set(row) <= PLAN_ROW_KEYS:
        raise ProjectionError("plan row contains unsupported fields")
    if "underlying" not in row:
        raise ProjectionError("plan underlying is missing")
    underlying = normalize_underlying(row["underlying"])
    prohibited_values = []
    for key in ("prohibited", "forbidden"):
        if key in row:
            if type(row[key]) is not bool:
                raise ProjectionError("plan prohibition is invalid")
            prohibited_values.append(row[key])
    if "allowed" in row:
        if type(row["allowed"]) is not bool:
            raise ProjectionError("plan allowed flag is invalid")
        prohibited_values.append(not row["allowed"])
    prohibited = any(prohibited_values)
    if "action" in row and isinstance(row["action"], str) and row["action"].strip() in {
        "禁止",
        "prohibited",
        "forbidden",
        "do_not_trade",
    }:
        prohibited = True
    actions = _plan_actions(row, prohibited)
    tool = _plan_tool(row, prohibited)
    status = _status_value(row)
    if status not in {None, *CONFIRMED_STATUSES, *NONCONFIRMED_STATUSES}:
        raise ProjectionError("plan status is unsupported")
    confirmation_flag = None
    if "confirmed" in row:
        if type(row["confirmed"]) is not bool:
            raise ProjectionError("plan confirmed flag is invalid")
        confirmation_flag = row["confirmed"]
    if status is None:
        confirmed = confirmation_flag is True
    else:
        confirmed = status in CONFIRMED_STATUSES
        if confirmation_flag is not None:
            confirmed = confirmed and confirmation_flag
    if "confirmation_status" in row and str(row["confirmation_status"]).lower() not in CONFIRMED_STATUSES | {"pending"}:
        raise ProjectionError("plan confirmation status is unsupported")
    effective_at = _optional_instant(row, "effective_at")
    confirmed_at = _optional_instant(row, "confirmed_at")
    expires_at = _optional_instant(row, "expires_at")
    market_date = None
    for key in ("review_date", "market_date", "date"):
        if key in row:
            current = parse_date(row[key])
            if market_date is not None and market_date != current:
                raise ProjectionError("plan dates conflict")
            market_date = current
    return PlanFact(
        underlying=underlying,
        actions=actions,
        tool=tool,
        prohibited=prohibited,
        confirmed=confirmed,
        effective_at=effective_at,
        confirmed_at=confirmed_at,
        expires_at=expires_at,
        market_date=market_date,
        order=order,
    )


def _plan_rows_from_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and "underlying" in value:
        return [value]
    if not isinstance(value, dict) or not set(value) <= PLAN_ENVELOPE_KEYS:
        raise ProjectionError("plan input envelope is unsupported")
    status = value.get("status")
    if status is not None and status not in STATUSES:
        raise ProjectionError("plan input status is unsupported")
    if status == "blocked":
        raise ProjectionError("plan input is blocked")
    rows: list[Any] = []
    for key in ("plans", "confirmed_plans", "weekly_plan", "intraday_revisions", "revisions"):
        if key in value:
            part = value[key]
            if not isinstance(part, list):
                raise ProjectionError("plan collection must be an array")
            rows.extend(part)
    if status == "empty" and rows:
        raise ProjectionError("empty plan input contains rows")
    return rows


def parse_plans(values: Iterable[Any] | None) -> list[PlanFact]:
    if values is None:
        return []
    result: list[PlanFact] = []
    order = 0
    for value in values:
        rows = _plan_rows_from_value(value)
        if len(rows) > MAX_ROWS or len(result) + len(rows) > MAX_ROWS:
            raise ProjectionError("too many plan rows")
        for row in rows:
            result.append(parse_plan_row(row, order=order))
            order += 1
    return result


def _plan_is_prior(plan: PlanFact, instant: dt.datetime) -> bool:
    if not plan.confirmed:
        return False
    if plan.market_date is not None and plan.market_date > instant.astimezone(NY_TZ).date():
        return False
    if plan.confirmed_at is not None and plan.confirmed_at > instant:
        return False
    if plan.effective_at is not None and plan.effective_at > instant:
        return False
    if plan.expires_at is not None and plan.expires_at <= instant:
        return False
    return True


def _plan_priority(plan: PlanFact) -> tuple[dt.datetime, dt.datetime, int]:
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (plan.effective_at or plan.confirmed_at or floor, plan.confirmed_at or floor, plan.order)


def alignment_for(execution: ExecutionFact, plans: Sequence[PlanFact]) -> str:
    if execution.tool == "无法识别":
        return "无法核对"
    related = [plan for plan in plans if plan.underlying == execution.underlying]
    if not related:
        return "无法核对"
    prior = [plan for plan in related if _plan_is_prior(plan, execution.instant)]
    if not prior:
        return "无法核对"
    # A later confirmed revision governs an earlier plan.  Tied latest entries
    # with contradictory instructions are intentionally not guessed.
    latest_priority = max(_plan_priority(plan)[:2] for plan in prior)
    latest = [plan for plan in prior if _plan_priority(plan)[:2] == latest_priority]
    if len(latest) > 1:
        outcomes = {
            "prohibited" if plan.prohibited else "match" if execution.action in plan.actions and plan.tool == execution.tool else "different"
            for plan in latest
        }
        if len(outcomes) > 1:
            return "无法核对"
    plan = max(latest, key=lambda item: item.order)
    if plan.prohibited:
        return "偏离计划"
    if execution.action in plan.actions and plan.tool == execution.tool:
        return "按计划"
    return "偏离计划"


def _merge_alignment(values: Sequence[str]) -> str:
    if not values or any(value not in ALIGNMENTS for value in values):
        raise ProjectionError("alignment is invalid")
    if "无法核对" in values:
        return "无法核对"
    if "偏离计划" in values:
        return "偏离计划"
    return "按计划"


def _public_payload(review_date: dt.date, grouped: Mapping[tuple[str, str, str], list[str]]) -> dict[str, Any]:
    executions = []
    for underlying, action, tool in sorted(grouped):
        executions.append(
            {
                "underlying": underlying,
                "action": action,
                "tool": tool,
                "alignment": _merge_alignment(grouped[(underlying, action, tool)]),
            }
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "review_date": review_date.isoformat(),
        "status": "complete" if executions else "empty",
        "executions": executions,
    }
    _assert_public_payload(payload)
    return payload


def _assert_public_payload(payload: Mapping[str, Any]) -> None:
    allowed_top = {"schema_version", "review_date", "status", "executions"}
    if set(payload) != allowed_top or payload.get("schema_version") != SCHEMA_VERSION:
        raise ProjectionError("public schema mismatch")
    if payload.get("status") not in STATUSES or not isinstance(payload.get("review_date"), str):
        raise ProjectionError("public status is invalid")
    rows = payload.get("executions")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ProjectionError("public executions are invalid")
    row_keys = {"underlying", "action", "tool", "alignment"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_keys:
            raise ProjectionError("public execution fields mismatch")
        if not isinstance(row["underlying"], str) or not US_TICKER_RE.fullmatch(row["underlying"]):
            raise ProjectionError("public underlying is invalid")
        if row["action"] not in {"买入", "卖出"} or row["tool"] not in TOOLS or row["alignment"] not in ALIGNMENTS:
            raise ProjectionError("public execution value is invalid")
        if re.search(r"\d{6}[CP]\d{4,}\b", json.dumps(row, ensure_ascii=False)):
            raise ProjectionError("public option identity leaked")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    forbidden = re.compile(r"(?i)(?:order[_ -]?id|execution[_ -]?id|account[_ -]?(?:id|number)|price|quantity|qty|cost|commission|fee)")
    if forbidden.search(encoded):
        raise ProjectionError("public private field leaked")


def project_facts(
    review_date: str,
    raw_executions: Any,
    *,
    trading_calendar: Any | None = None,
    plans: Iterable[Any] | None = None,
) -> dict[str, Any]:
    review = parse_date(review_date)
    rows, embedded_calendar, embedded_plans = _extract_execution_rows(raw_executions)
    if isinstance(raw_executions, dict) and "review_date" in raw_executions:
        if parse_date(raw_executions["review_date"]) != review:
            raise ProjectionError("execution input review date conflicts")
    calendar = trading_calendar if trading_calendar is not None else embedded_calendar
    window = parse_calendar(calendar, review)
    if len(rows) > MAX_ROWS:
        raise ProjectionError("too many execution rows")
    plan_values: list[Any] = []
    if plans is not None:
        plan_values.extend(plans)
    if embedded_plans is not None:
        plan_values.extend(embedded_plans)
    plan_facts = parse_plans(plan_values)
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for row in rows:
        fact = project_execution(row, review)
        if not (window.start <= fact.instant < window.end):
            raise ProjectionError("execution is outside the calendar window")
        key = (fact.underlying, fact.action, fact.tool)
        grouped.setdefault(key, []).append(alignment_for(fact, plan_facts))
    return _public_payload(review, grouped)


def blocked_payload(review_date: str | dt.date) -> dict[str, Any]:
    review = parse_date(review_date) if isinstance(review_date, str) else review_date
    return {
        "schema_version": SCHEMA_VERSION,
        "review_date": review.isoformat(),
        "status": "blocked",
        "executions": [],
    }


def _validate_output_path(path_value: str | os.PathLike[str]) -> Path:
    path = _absolute_no_links(path_value)
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise ProjectionError("output directory cannot be read") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o002:
        raise ProjectionError("output directory is not owner-safe")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ProjectionError("output file identity is not safe")
    return path


def write_output(path_value: str | os.PathLike[str], payload: Mapping[str, Any]) -> Path:
    _assert_public_payload(payload)
    path = _validate_output_path(path_value)
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        info = path.lstat()
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ProjectionError("output permissions are unsafe")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project sanitized daily trade journal facts")
    parser.add_argument("--review-date", required=True)
    parser.add_argument("--raw-executions", required=True)
    parser.add_argument("--trading-calendar", "--calendar", dest="trading_calendar", required=True)
    parser.add_argument("--confirmed-plans", "--plans", dest="confirmed_plans")
    parser.add_argument("--weekly-plan")
    parser.add_argument("--intraday-revisions")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        review = parse_date(args.review_date)
    except ProjectionError:
        parser.error("--review-date must be a valid YYYY-MM-DD")
        return 2
    try:
        raw_path = _absolute_no_links(args.raw_executions)
        output_path = _validate_output_path(args.output)
        input_paths = [raw_path]
        values = {
            "calendar": args.trading_calendar,
            "plans": args.confirmed_plans,
            "weekly": args.weekly_plan,
            "revisions": args.intraday_revisions,
        }
        for value in values.values():
            if value:
                candidate = _absolute_no_links(value)
                if candidate == output_path:
                    raise ProjectionError("output must differ from input")
                input_paths.append(candidate)
        raw = read_input_json(raw_path)
        calendar = read_input_json(args.trading_calendar)
        plan_values: list[Any] = []
        for option in (args.confirmed_plans, args.weekly_plan, args.intraday_revisions):
            if option:
                plan_values.append(read_input_json(option))
        result = project_facts(review.isoformat(), raw, trading_calendar=calendar, plans=plan_values)
        write_output(output_path, result)
        return 0
    except (ProjectionError, OSError, json.JSONDecodeError, TypeError, ValueError):
        # A fixed blocked envelope is safe to retain and never echoes raw input.
        try:
            write_output(args.output, blocked_payload(review))
        except Exception:
            return 3
        return 2


if __name__ == "__main__":
    sys.exit(main())
