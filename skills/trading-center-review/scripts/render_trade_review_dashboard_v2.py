#!/usr/bin/env python3
"""Render a private, offline V2 trading-review dashboard from a sanitized packet."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import html
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "trading-review-dashboard.v2"
WEEKLY_SCHEMA_VERSION = "trading-review-weekly-dashboard.v2"
BODY_MARKER = "<!--__TRADING_REVIEW_DASHBOARD_V2_BODY__-->"
PRIVATE_ROOT = Path("/private/tmp/trading-center-review-runtime").resolve()
DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "trade-review-dashboard-v2-standalone.html"
)

MODULE_STATUSES = frozenset({"complete", "partial", "empty", "stale", "blocked"})
OVERALL_STATUSES = frozenset({"complete", "partial", "blocked"})
EVENT_STATUSES = frozenset({"已发生", "预期", "未公布", "未验证", "已取消"})
DIRECTIONS = frozenset({"up", "down", "flat"})
TONES = frozenset({"neutral", "blue", "green", "amber", "red"})
PLAN_TABS = frozenset({"holdings", "plan"})
VALUE_KINDS = frozenset({"money", "number", "text"})
WEEKLY_SECTION_NAMES = (
    "market_radar",
    "judgement",
    "operations",
    "positions_plan",
    "plan_review",
    "next_week",
    "events",
    "data_note",
)
WEEKLY_SUBJECT_SECTIONS = {
    "市场雷达": "market_radar",
    "周度判断": "judgement",
    "本周操作": "operations",
    "持仓计划": "positions_plan",
    "计划复核": "plan_review",
    "下周草案": "next_week",
    "下周事件": "events",
    "数据说明": "data_note",
}
WEEKLY_ITEM_KINDS = frozenset(
    {"plan_actual", "discipline", "retain", "delete", "rewrite", "add", "risk", "gap"}
)
WEEKLY_EVIDENCE_KINDS = frozenset({"fact", "interpretation", "draft", "gap"})
TOP_LEVEL_KEYS = frozenset(
    {
        "meta",
        "market",
        "account",
        "codex_analysis",
        "operations",
        "positions_plans",
        "events",
        "data_note",
    }
)

SENSITIVE_KEY_RE = re.compile(
    r"(?:account[_ -]?(?:id|no|number|identifier)|"
    r"order[_ -]?(?:id|no|number)|"
    r"execution[_ -]?(?:id|no|number)|"
    r"trade[_ -]?(?:id|no|number)|"
    r"client[_ -]?(?:id|no|number)|"
    r"api[_ -]?key|cookie|password|secret|credential|"
    r"commission|cost|raw[_ -]?(?:response|json|payload)|"
    r"(?:access|refresh)[_ -]?token)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9_])(?:access|refresh)\s*[_ -]?\s*token(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])client\s*[_ -]?\s*secret(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])api\s*[_ -]?\s*key(?![A-Za-z0-9_])|"
    r"[\"']?\bauthorization[\"']?\s*[:=：＝]\s*[\"']?(?:bearer\s+)?[^\"'\s,;|]+|"
    r"(?<![A-Za-z0-9_])bearer\s+[A-Za-z0-9._~+/=-]{3,}(?![A-Za-z0-9_])|"
    r"[\"']?\bkey[\"']?\s*[:=：＝]\s*[\"']?[A-Za-z0-9._~+/=-]{3,}[\"']?|"
    r"(?:cookie|password|secret|credential)\s*[:=：＝]\s*[\"']?[^\"'\s,;|]+|"
    r"(?:账户编号|账户标识|订单\s*(?:id|号|编号)|成交\s*(?:id|号|编号)|凭据)\s*[:=：＝]\s*[\"']?[^\"'\s,;|]+|"
    r"(?:account|order|execution|trade)[_ -]?(?:id|no|number|identifier)|"
    r"sk-[A-Za-z0-9]{12,})",
    re.IGNORECASE,
)
# Deliberately assembled so provider names and internal implementation labels
# are not copied into user-facing artifacts by an otherwise valid field.
INTERNAL_UI_VALUE_RE = re.compile(
    r"(?:d[e]ep[s]eek|(?<![A-Za-z0-9_])(?:agents|context)(?:\s*\.?\s*md)?(?![A-Za-z0-9_])|"
    r"internal\s+(?:reviewer|review|tool|agent)|"
    r"(?:reviewer|subagent|tool|agent)\s+(?:status|result|output)|"
    r"(?:内部|后台)(?:审查|工具|agent)|(?:工具|代理)(?:状态|注入)|"
    r"(?:人工|人类|浏览器|设计)\s*(?:验收|qa)|"
    r"\b(?:human|browser|design)\s+(?:acceptance|qa)\b|"
    r"(?:schema|v2)\s*(?:id|debug|调试)|"
    r"(?<![A-Za-z0-9_])(?:reviewer|tool|agent|schema|v2)(?![A-Za-z0-9_])|"
    r"trading[-_ ]?review[-_ ]?dashboard\.v2|反证审查)",
    re.IGNORECASE,
)
SENSITIVE_CJK_KEYS = (
    "账户标识",
    "账户编号",
    "订单号",
    "订单编号",
    "订单ID",
    "成交号",
    "成交编号",
    "成交ID",
    "交易号",
    "交易编号",
    "凭据",
    "令牌",
    "密码",
    "佣金",
    "成本",
    "原始响应",
    "原始数据",
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
LOCAL_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} (?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)? "
    r"(?:Asia/Shanghai|ET|UTC)$"
)
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T"
    r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:0\d|1\d|2[0-3]):[0-5]\d)$"
)
WEEKLY_OPTION_IDENTITY_RE = re.compile(
    r"(?::OPTION\b|\b(?:strike|expiry|expiration)\b|行权价|到期日|"
    r"\b\d{4}-\d{2}-\d{2}\s+(?:call|put)\b)",
    re.IGNORECASE,
)
NY_TZ = ZoneInfo("America/New_York")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
UTC_TZ = dt.timezone.utc
KNOWN_PROXY_SYMBOLS = frozenset(
    {"SPY.US", "VOO.US", "QQQ.US", "GLD.US", "USO.US", "TLT.US", "IBIT.US"}
)

STATUS_LABELS = {
    "complete": "已完成",
    "partial": "部分可用",
    "empty": "暂无数据",
    "stale": "数据陈旧",
    "blocked": "待核对",
}
STATUS_TONES = {
    "complete": "blue",
    "partial": "amber",
    "empty": "neutral",
    "stale": "amber",
    "blocked": "amber",
}
DIRECTION_LABELS = {"up": "↑", "down": "↓", "flat": "→"}
EVENT_STATUS_TONES = {
    "已发生": "fact",
    "预期": "watch",
    "未公布": "watch",
    "未验证": "unverified",
    "已取消": "unverified",
}
# Input remains an audited private packet. These terms must not be promoted
# into the user-facing copy, including collapsed details and HTML attributes.
PRIVATE_DIAGNOSTIC_RE = re.compile(
    r"(?:\b(?:hash|sha-?256|revision|partition|payload|underlying|setup|CLI|"
    r"schema|snapshot_at|source_scope|finance-calendar|macrodata|projection)\b|"
    r"\b[a-z]+(?:_[a-z0-9]+)+\b|\b[0-9a-f]{32,}\b|"
    r"半开|白名单|勾稽|消歧|分区|修订版本|修订编号|修订号|版本修订|字段|接口|投影|数据规范|数据契约|"
    r"/private/|/Users/)", re.IGNORECASE,
)
NON_US_SYMBOL_RE = re.compile(r"\b[A-Z0-9][A-Z0-9.\-]*\.(?:HK|SH|SZ|SG|JP)\b", re.IGNORECASE)
US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]*\.US$", re.IGNORECASE)
FED_CALENDAR_HOSTS = frozenset({
    "federalreserve.gov", "newyorkfed.org", "bostonfed.org", "philadelphiafed.org",
    "clevelandfed.org", "richmondfed.org", "atlantafed.org", "chicagofed.org",
    "stlouisfed.org", "minneapolisfed.org", "kansascityfed.org", "dallasfed.org",
    "frbsf.org",
})
class DashboardRenderError(ValueError):
    """Raised when a V2 packet or template violates the contract."""


def _object(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise DashboardRenderError(f"{path} must be an object")
    return value


def _array(value: Any, path: str) -> List[Any]:
    if not isinstance(value, list):
        raise DashboardRenderError(f"{path} must be an array")
    return value


def _text(value: Any, path: str, required: bool = True) -> str:
    if not isinstance(value, str):
        raise DashboardRenderError(f"{path} must be a string")
    if required and not value.strip():
        raise DashboardRenderError(f"{path} must not be empty")
    return value


def _optional_text(value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    return _text(value, path)


EMPTY_TEXT_MARKERS = frozenset(
    {
        "",
        "—",
        "-",
        "不可用",
        "暂无数据",
        "暂无已确认数据",
        "未提供",
        "未知",
        "无数据",
        "成功为空",
    }
)


def _require_empty_text(value: str, path: str) -> None:
    if value.strip().casefold() not in EMPTY_TEXT_MARKERS:
        raise DashboardRenderError(f"{path} empty data cannot contain factual text")


def _require_empty_number(value: Any, path: str) -> None:
    if value is not None:
        raise DashboardRenderError(f"{path} empty data cannot contain a factual number")


def _number(value: Any, path: str, allow_none: bool = False) -> Optional[float]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DashboardRenderError(f"{path} must be a number")
    if not math.isfinite(float(value)):
        raise DashboardRenderError(f"{path} must be finite")
    return float(value)


def _integer(value: Any, path: str, allow_none: bool = False) -> Optional[int]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardRenderError(f"{path} must be an integer")
    if value < 0:
        raise DashboardRenderError(f"{path} must be non-negative")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise DashboardRenderError(f"{path} must be a boolean")
    return value


def _enum(value: Any, allowed: Iterable[str], path: str) -> str:
    value = _text(value, path)
    if value not in allowed:
        raise DashboardRenderError(f"{path} must be one of {sorted(allowed)}")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: Iterable[str], path: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise DashboardRenderError(f"{path} unsupported field: {unknown[0]}")


def _required_keys(value: Mapping[str, Any], required: Iterable[str], path: str) -> None:
    missing = sorted(set(required) - set(value))
    if missing:
        raise DashboardRenderError(f"{path} missing field: {missing[0]}")


def _date_text(value: Any, path: str) -> str:
    text = _text(value, path)
    if not DATE_RE.fullmatch(text):
        raise DashboardRenderError(f"{path} must be YYYY-MM-DD")
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise DashboardRenderError(f"{path} must be a real calendar date") from exc
    return text


def _rfc3339_timestamp(value: Any, path: str) -> dt.datetime:
    text = _text(value, path)
    if not RFC3339_RE.fullmatch(text):
        raise DashboardRenderError(f"{path} must be an RFC3339 timestamp with T and timezone")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DashboardRenderError(f"{path} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise DashboardRenderError(f"{path} must include a timezone")
    return parsed


def _display_timestamp(value: Any, path: str) -> str:
    text = _text(value, path)
    if LOCAL_TIMESTAMP_RE.fullmatch(text):
        _date_text(text[:10], f"{path}.date")
        return text
    if _is_rfc3339(text):
        return text
    raise DashboardRenderError(f"{path} must include a date, time and timezone")


def _is_rfc3339(text: str) -> bool:
    if not RFC3339_RE.fullmatch(text):
        return False
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _clock_text(value: Any, path: str) -> str:
    text = _text(value, path)
    if not CLOCK_RE.fullmatch(text):
        raise DashboardRenderError(f"{path} must be HH:MM or HH:MM:SS")
    return text


def _local_datetime(date_text: str, clock_text: str, zone: ZoneInfo, path: str) -> dt.datetime:
    """Turn a local wall-clock value into one unambiguous aware timestamp."""

    date_value = dt.date.fromisoformat(date_text)
    clock_value = dt.time.fromisoformat(clock_text)
    naive = dt.datetime.combine(date_value, clock_value)
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise DashboardRenderError(f"{path} is ambiguous in {zone.key}")
    round_trip = first.astimezone(UTC_TZ).astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        raise DashboardRenderError(f"{path} is not a valid local time in {zone.key}")
    return first


def _assert_fixed_zone_timestamp(
    parsed: dt.datetime, date_text: str, zone: ZoneInfo, path: str
) -> None:
    """Require an RFC3339 value to carry the offset for its named local zone."""

    local = parsed.astimezone(zone)
    if local.date().isoformat() != date_text:
        raise DashboardRenderError(f"{path} does not use the expected {zone.key} date")
    if local.replace(tzinfo=None) != parsed.replace(tzinfo=None):
        raise DashboardRenderError(f"{path} does not carry the expected {zone.key} offset")
    expected = _local_datetime(
        date_text,
        local.strftime("%H:%M:%S"),
        zone,
        f"{path}.{zone.key}",
    )
    if parsed.utcoffset() != expected.utcoffset():
        raise DashboardRenderError(f"{path} does not carry the expected {zone.key} offset")


def _security_scan(value: Any, path: str = "$") -> None:
    """Reject forbidden keys and suspicious sensitive values before rendering."""

    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise DashboardRenderError(f"{path} contains a non-string field name")
            normalized = key.lower().replace("_", "").replace("-", "").replace(" ", "")
            if SENSITIVE_KEY_RE.search(key) or any(
                token.casefold() in key.casefold() for token in SENSITIVE_CJK_KEYS
            ):
                raise DashboardRenderError(f"{path}.{key} contains a forbidden field")
            if normalized in {
                "accountid",
                "accountno",
                "orderno",
                "orderid",
                "executionid",
                "executionno",
                "tradeid",
                "tradeno",
                "clientid",
                "apikey",
                "cookie",
                "password",
                "secret",
                "credential",
                "commission",
                "cost",
                "rawresponse",
                "rawjson",
                "rawpayload",
                "accesstoken",
                "refreshtoken",
                "authorization",
            }:
                raise DashboardRenderError(f"{path}.{key} contains a forbidden field")
            _security_scan(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _security_scan(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if SENSITIVE_VALUE_RE.search(value):
            raise DashboardRenderError(f"{path} contains a forbidden sensitive value")
        if INTERNAL_UI_VALUE_RE.search(value):
            raise DashboardRenderError(f"{path} contains a forbidden internal UI state")


def _validate_meta(value: Any) -> Dict[str, Any]:
    path = "$.meta"
    item = _object(value, path)
    _reject_unknown(
        item,
        {
            "review_label",
            "account_label",
            "review_date",
            "generated_at",
            "market_as_of",
            "account_snapshot_at",
            "previous_trading_window",
            "period_label",
            "overall_status",
        },
        path,
    )
    _required_keys(
        item,
        {
            "review_label",
            "account_label",
            "review_date",
            "generated_at",
            "market_as_of",
            "account_snapshot_at",
            "previous_trading_window",
            "period_label",
            "overall_status",
        },
        path,
    )
    _text(item["review_label"], f"{path}.review_label")
    _text(item["account_label"], f"{path}.account_label")
    review_date = _date_text(item["review_date"], f"{path}.review_date")
    review_day = dt.date.fromisoformat(review_date)
    if review_day.weekday() >= 5:
        raise DashboardRenderError(f"{path}.review_date must be Monday-Friday")
    generated_at = _rfc3339_timestamp(item["generated_at"], f"{path}.generated_at")
    _assert_fixed_zone_timestamp(
        generated_at,
        generated_at.astimezone(SHANGHAI_TZ).date().isoformat(),
        SHANGHAI_TZ,
        f"{path}.generated_at",
    )
    for key in ("market_as_of", "account_snapshot_at"):
        _display_timestamp(item[key], f"{path}.{key}")
    _text(item["period_label"], f"{path}.period_label")
    _enum(item["overall_status"], OVERALL_STATUSES, f"{path}.overall_status")

    window_path = f"{path}.previous_trading_window"
    window = _object(item["previous_trading_window"], window_path)
    _reject_unknown(
        window,
        {"label", "market_date", "ny_start", "ny_end", "utc_start", "utc_end"},
        window_path,
    )
    _required_keys(
        window,
        {"label", "market_date", "ny_start", "ny_end", "utc_start", "utc_end"},
        window_path,
    )
    _text(window["label"], f"{window_path}.label")
    market_date = _date_text(window["market_date"], f"{window_path}.market_date")
    for key in ("ny_start", "ny_end", "utc_start", "utc_end"):
        parsed = _rfc3339_timestamp(window[key], f"{window_path}.{key}")
        if key.startswith("utc_") and parsed.utcoffset() != dt.timedelta(0):
            raise DashboardRenderError(f"{window_path}.{key} must be UTC")
    ny_start = _rfc3339_timestamp(window["ny_start"], f"{window_path}.ny_start")
    ny_end = _rfc3339_timestamp(window["ny_end"], f"{window_path}.ny_end")
    utc_start = _rfc3339_timestamp(window["utc_start"], f"{window_path}.utc_start")
    utc_end = _rfc3339_timestamp(window["utc_end"], f"{window_path}.utc_end")
    if ny_start >= ny_end or utc_start >= utc_end:
        raise DashboardRenderError(f"{window_path} must be a non-empty half-open window")
    if market_date != review_date:
        raise DashboardRenderError(f"{window_path}.market_date must match $.meta.review_date")
    next_day = review_day + dt.timedelta(days=1)
    _assert_fixed_zone_timestamp(ny_start, review_date, NY_TZ, f"{window_path}.ny_start")
    _assert_fixed_zone_timestamp(ny_end, next_day.isoformat(), NY_TZ, f"{window_path}.ny_end")
    expected_ny_start = _local_datetime(
        review_date, "00:00:00", NY_TZ, f"{window_path}.ny_start"
    )
    expected_ny_end = _local_datetime(
        next_day.isoformat(), "00:00:00", NY_TZ, f"{window_path}.ny_end"
    )
    if ny_start.astimezone(UTC_TZ) != expected_ny_start.astimezone(UTC_TZ):
        raise DashboardRenderError(f"{window_path}.ny_start must be the exact NY midnight")
    if ny_end.astimezone(UTC_TZ) != expected_ny_end.astimezone(UTC_TZ):
        raise DashboardRenderError(f"{window_path}.ny_end must be the exact NY midnight")
    if ny_start.astimezone(UTC_TZ) != utc_start.astimezone(UTC_TZ):
        raise DashboardRenderError(f"{window_path}.ny_start and utc_start must be the same instant")
    if ny_end.astimezone(UTC_TZ) != utc_end.astimezone(UTC_TZ):
        raise DashboardRenderError(f"{window_path}.ny_end and utc_end must be the same instant")
    return item


def _validate_status_module(value: Any, path: str, extra: Iterable[str]) -> Dict[str, Any]:
    item = _object(value, path)
    _reject_unknown(item, set(extra) | {"status", "note"}, path)
    _required_keys(item, {"status"}, path)
    _enum(item["status"], MODULE_STATUSES, f"{path}.status")
    if "note" in item:
        _text(item["note"], f"{path}.note", required=False)
    return item


def _validate_child_statuses(
    item: Mapping[str, Any], path: str, child_statuses: Sequence[str]
) -> None:
    """Keep parent status from hiding blocked or contradictory child states."""

    status = item["status"]
    if any(child_status == "blocked" for child_status in child_statuses):
        raise DashboardRenderError(f"{path} cannot mask a blocked child")
    if status == "complete" and any(child_status != "complete" for child_status in child_statuses):
        raise DashboardRenderError(f"{path} complete status conflicts with child data status")
    if status in {"partial", "stale"} and (
        not child_statuses or all(child_status == "complete" for child_status in child_statuses)
    ) and not item.get("note", "").strip():
        raise DashboardRenderError(
            f"{path} {status} status requires a note when child state does not explain it"
        )


def _validate_market(value: Any) -> Dict[str, Any]:
    path = "$.market"
    item = _validate_status_module(value, path, {"title", "source_scope", "items"})
    _required_keys(item, {"title", "items"}, path)
    _text(item["title"], f"{path}.title")
    if "source_scope" in item:
        _text(item["source_scope"], f"{path}.source_scope")
    rows = _array(item["items"], f"{path}.items")
    child_statuses = []
    for index, raw in enumerate(rows):
        row_path = f"{path}.items[{index}]"
        row = _object(raw, row_path)
        _reject_unknown(
            row,
            {
                "name",
                "symbol",
                "is_proxy",
                "proxy_for",
                "value",
                "change_pct",
                "direction",
                "strength",
                "state",
                "session",
                "as_of",
                "risk_note",
                "data_status",
                "unavailable_reason",
                "capital_flow",
            },
            row_path,
        )
        _required_keys(
            row,
            {
                "name",
                "symbol",
                "is_proxy",
                "proxy_for",
                "value",
                "change_pct",
                "direction",
                "strength",
                "state",
                "session",
                "as_of",
                "risk_note",
                "data_status",
            },
            row_path,
        )
        data_status = _enum(row["data_status"], MODULE_STATUSES, f"{row_path}.data_status")
        child_statuses.append(data_status)
        for key in ("name", "symbol"):
            _text(row[key], f"{row_path}.{key}")
        for key in ("state", "session", "risk_note"):
            _text(row[key], f"{row_path}.{key}", required=data_status != "empty")
            if data_status == "empty":
                _require_empty_text(row[key], f"{row_path}.{key}")
        is_proxy = _boolean(row["is_proxy"], f"{row_path}.is_proxy")
        proxy_for = _optional_text(row["proxy_for"], f"{row_path}.proxy_for")
        if is_proxy and not proxy_for:
            raise DashboardRenderError(f"{row_path}.proxy_for is required for a proxy")
        if not is_proxy and proxy_for is not None:
            raise DashboardRenderError(f"{row_path}.proxy_for must be null when is_proxy is false")
        if row["symbol"].upper() in KNOWN_PROXY_SYMBOLS and not is_proxy:
            raise DashboardRenderError(f"{row_path} known proxy symbol must declare is_proxy")
        _display_timestamp(row["as_of"], f"{row_path}.as_of")
        _number(row["value"], f"{row_path}.value", allow_none=True)
        _number(row["change_pct"], f"{row_path}.change_pct", allow_none=True)
        _enum(row["direction"], DIRECTIONS, f"{row_path}.direction")
        strength = _integer(row["strength"], f"{row_path}.strength")
        if strength is None or strength > 3:
            raise DashboardRenderError(f"{row_path}.strength must be between 0 and 3")
        if data_status == "complete" and (row["value"] is None or row["change_pct"] is None):
            raise DashboardRenderError(f"{row_path} complete data must include value and change_pct")
        if data_status == "empty":
            _require_empty_number(row["value"], f"{row_path}.value")
            _require_empty_number(row["change_pct"], f"{row_path}.change_pct")
            if row["direction"] != "flat":
                raise DashboardRenderError(f"{row_path}.direction empty data must be flat")
            if strength != 0:
                raise DashboardRenderError(f"{row_path}.strength empty data must be 0")
        if "unavailable_reason" in row:
            _text(row["unavailable_reason"], f"{row_path}.unavailable_reason")
        if "capital_flow" in row:
            flow_path = f"{row_path}.capital_flow"
            flow = _object(row["capital_flow"], flow_path)
            _reject_unknown(flow, {"label", "direction", "value", "as_of", "data_status"}, flow_path)
            _required_keys(flow, {"label", "direction", "value", "as_of", "data_status"}, flow_path)
            flow_status = _enum(flow["data_status"], MODULE_STATUSES, f"{flow_path}.data_status")
            _text(flow["label"], f"{flow_path}.label", required=flow_status != "empty")
            if flow_status == "empty":
                _require_empty_text(flow["label"], f"{flow_path}.label")
            _enum(flow["direction"], DIRECTIONS, f"{flow_path}.direction")
            flow_value = _number(flow["value"], f"{flow_path}.value", allow_none=True)
            _display_timestamp(flow["as_of"], f"{flow_path}.as_of")
            child_statuses.append(flow_status)
            if flow_status == "complete" and flow_value is None:
                raise DashboardRenderError(f"{flow_path} complete data must include value")
            if flow_status == "empty":
                _require_empty_number(flow_value, f"{flow_path}.value")
                if flow["direction"] != "flat":
                    raise DashboardRenderError(f"{flow_path}.direction empty data must be flat")
    if item["status"] == "empty" and rows:
        raise DashboardRenderError(f"{path} empty status cannot contain child items")
    _validate_child_statuses(item, path, child_statuses)
    return item


def _validate_account(value: Any) -> Dict[str, Any]:
    path = "$.account"
    item = _validate_status_module(value, path, {"title", "base_currency", "snapshot_at", "metrics"})
    _required_keys(item, {"title", "base_currency", "snapshot_at", "metrics"}, path)
    _text(item["title"], f"{path}.title")
    _text(item["base_currency"], f"{path}.base_currency")
    _display_timestamp(item["snapshot_at"], f"{path}.snapshot_at")
    rows = _array(item["metrics"], f"{path}.metrics")
    child_statuses = []
    for index, raw in enumerate(rows):
        row_path = f"{path}.metrics[{index}]"
        row = _object(raw, row_path)
        _reject_unknown(row, {"label", "value", "kind", "data_status", "note"}, row_path)
        _required_keys(row, {"label", "value", "kind", "data_status"}, row_path)
        _text(row["label"], f"{row_path}.label")
        kind = _enum(row["kind"], VALUE_KINDS, f"{row_path}.kind")
        data_status = _enum(row["data_status"], MODULE_STATUSES, f"{row_path}.data_status")
        if kind == "text":
            if row["value"] is not None:
                _text(row["value"], f"{row_path}.value")
        else:
            _number(row["value"], f"{row_path}.value", allow_none=True)
        child_statuses.append(data_status)
        if data_status == "complete" and row["value"] is None:
            raise DashboardRenderError(f"{row_path} complete data must include value")
        if data_status == "empty" and row["value"] is not None:
            raise DashboardRenderError(f"{row_path}.value empty data cannot contain a factual value")
        if "note" in row:
            _text(row["note"], f"{row_path}.note")
    if item["status"] == "empty" and rows:
        raise DashboardRenderError(f"{path} empty status cannot contain child items")
    _validate_child_statuses(item, path, child_statuses)
    return item


def _validate_analysis_items(value: Any, path: str) -> List[Dict[str, Any]]:
    rows = _array(value, path)
    validated = []
    for index, raw in enumerate(rows):
        row_path = f"{path}[{index}]"
        row = _object(raw, row_path)
        _reject_unknown(row, {"label", "text"}, row_path)
        _required_keys(row, {"label", "text"}, row_path)
        _text(row["label"], f"{row_path}.label")
        _text(row["text"], f"{row_path}.text")
        validated.append(row)
    return validated


def _validate_analysis(value: Any) -> Dict[str, Any]:
    path = "$.codex_analysis"
    item = _validate_status_module(
        value,
        path,
        {"title", "headline", "facts", "interpretation", "risks", "checks", "gaps"},
    )
    _required_keys(
        item,
        {"title", "headline", "facts", "interpretation", "risks", "checks", "gaps"},
        path,
    )
    _text(item["title"], f"{path}.title")
    _text(item["headline"], f"{path}.headline")
    for key in ("facts", "interpretation", "risks", "gaps"):
        item[key] = _validate_analysis_items(item[key], f"{path}.{key}")
    checks = _array(item["checks"], f"{path}.checks")
    validated_checks = []
    for index, raw in enumerate(checks):
        check_path = f"{path}.checks[{index}]"
        check = _object(raw, check_path)
        _reject_unknown(check, {"if", "then", "else", "evidence_refs", "boundary"}, check_path)
        _required_keys(check, {"if", "then", "else", "evidence_refs", "boundary"}, check_path)
        for key in ("if", "then", "else", "boundary"):
            _text(check[key], f"{check_path}.{key}")
        refs = _array(check["evidence_refs"], f"{check_path}.evidence_refs")
        for ref_index, ref in enumerate(refs):
            _text(ref, f"{check_path}.evidence_refs[{ref_index}]")
        validated_checks.append(check)
    item["checks"] = validated_checks
    if item["status"] in {"partial", "stale"} and not item["gaps"] and not item.get("note", "").strip():
        raise DashboardRenderError(f"{path} {item['status']} status requires an explanatory gap or note")
    if item["status"] == "empty" and any(item[key] for key in ("facts", "interpretation", "risks", "checks")):
        raise DashboardRenderError(f"{path} empty status cannot contain confirmed analysis children")
    return item


def _validate_count_block(value: Any, path: str) -> Dict[str, Any]:
    block = _object(value, path)
    _reject_unknown(block, {"count", "data_status", "note"}, path)
    _required_keys(block, {"count", "data_status", "note"}, path)
    count = _integer(block["count"], f"{path}.count", allow_none=True)
    data_status = _enum(block["data_status"], MODULE_STATUSES, f"{path}.data_status")
    if data_status == "complete" and count is None:
        raise DashboardRenderError(f"{path} complete data must include count")
    if data_status == "empty" and count not in (None, 0):
        raise DashboardRenderError(f"{path}.count empty data must be 0 or null")
    _text(block["note"], f"{path}.note")
    return block


def _validate_operations(value: Any) -> Dict[str, Any]:
    path = "$.operations"
    item = _validate_status_module(
        value,
        path,
        {"title", "window_label", "orders", "executions", "items", "reconciliation", "market_scope"},
    )
    _required_keys(
        item,
        {"title", "window_label", "orders", "executions", "items", "reconciliation"},
        path,
    )
    _text(item["title"], f"{path}.title")
    _text(item["window_label"], f"{path}.window_label")
    if "market_scope" in item:
        _enum(item["market_scope"], {"US"}, f"{path}.market_scope")
    item["orders"] = _validate_count_block(item["orders"], f"{path}.orders")
    item["executions"] = _validate_count_block(item["executions"], f"{path}.executions")
    child_statuses = [item["orders"]["data_status"], item["executions"]["data_status"]]
    _text(item["reconciliation"], f"{path}.reconciliation")
    rows = _array(item["items"], f"{path}.items")
    if item["status"] == "empty":
        if rows or any(
            block["data_status"] not in {"empty", "complete"}
            or block["count"] not in (None, 0)
            for block in (item["orders"], item["executions"])
        ):
            raise DashboardRenderError(f"{path} empty status cannot contain factual children")
    for index, raw in enumerate(rows):
        row_path = f"{path}.items[{index}]"
        row = _object(raw, row_path)
        _reject_unknown(
            row,
            {
                "symbol",
                "display_name",
                "action",
                "role",
                "state",
                "plan_relation",
                "reconciliation",
                "data_status",
                "execution_count",
            },
            row_path,
        )
        _required_keys(
            row,
            {
                "symbol",
                "display_name",
                "action",
                "role",
                "state",
                "plan_relation",
                "reconciliation",
                "data_status",
            },
            row_path,
        )
        data_status = _enum(row["data_status"], MODULE_STATUSES, f"{row_path}.data_status")
        child_statuses.append(data_status)
        if "execution_count" in row:
            execution_count = _integer(row["execution_count"], f"{row_path}.execution_count", allow_none=True)
            if data_status == "empty" and execution_count not in (None, 0):
                raise DashboardRenderError(f"{row_path} empty data cannot contain executions")
        _text(row["symbol"], f"{row_path}.symbol")
        if item.get("market_scope") == "US" and not US_SYMBOL_RE.fullmatch(row["symbol"]):
            raise DashboardRenderError("US operation scope cannot contain an unverified or non-US symbol")
        _text(row["display_name"], f"{row_path}.display_name")
        for key in ("action", "role", "state", "plan_relation", "reconciliation"):
            _text(row[key], f"{row_path}.{key}", required=data_status != "empty")
            if data_status == "empty":
                _require_empty_text(row[key], f"{row_path}.{key}")
    known_executions = sum(row.get("execution_count") or 0 for row in rows)
    total = item["executions"]
    if total["data_status"] in {"complete", "empty"} and total["count"] is not None and known_executions > total["count"]:
        raise DashboardRenderError(f"{path} confirmed fills exceed the execution total")
    _validate_child_statuses(item, path, child_statuses)
    return item


def _validate_plan_detail(value: Any, path: str) -> Dict[str, Any]:
    item = _object(value, path)
    keys = {
        "plan_id", "version", "plan_stage", "plan_status", "setup_type",
        "evidence", "zones", "parent_plan_id", "parent_plan_version",
        "initial_buy_episode_key", "quote_relation",
    }
    _reject_unknown(item, keys, path)
    _required_keys(item, keys, path)
    _text(item["plan_id"], f"{path}.plan_id")
    version = _integer(item["version"], f"{path}.version")
    if version < 1:
        raise DashboardRenderError(f"{path}.version must be positive")
    stage = _enum(item["plan_stage"], {"pre_entry", "position_management"}, f"{path}.plan_stage")
    status = _enum(item["plan_status"], {"draft", "confirmed", "expired"}, f"{path}.plan_status")
    setup = _enum(
        item["setup_type"],
        {"pullback", "breakout", "range", "bottom_reversal", "position_management"},
        f"{path}.setup_type",
    )
    if (stage == "position_management") != (setup == "position_management"):
        raise DashboardRenderError("position_management stage and setup must match")
    if stage == "pre_entry":
        if any(item[key] is not None for key in ("parent_plan_id", "parent_plan_version", "initial_buy_episode_key")):
            raise DashboardRenderError("pre_entry plan cannot reference a parent or buy episode")
    else:
        _text(item["parent_plan_id"], f"{path}.parent_plan_id")
        parent_version = _integer(item["parent_plan_version"], f"{path}.parent_plan_version")
        if parent_version < 1:
            raise DashboardRenderError("position_management parent version must be positive")
        _text(item["initial_buy_episode_key"], f"{path}.initial_buy_episode_key")
    _enum(item["quote_relation"], {"below", "inside", "above", "stale", "unavailable"}, f"{path}.quote_relation")

    evidence = _object(item["evidence"], f"{path}.evidence")
    evidence_keys = {"evidence_id", "source", "as_of", "timezone", "adjustment", "bars_used", "atr14"}
    _reject_unknown(evidence, evidence_keys, f"{path}.evidence")
    _required_keys(evidence, evidence_keys, f"{path}.evidence")
    if not re.fullmatch(r"[0-9a-f]{64}", _text(evidence["evidence_id"], f"{path}.evidence.evidence_id")):
        raise DashboardRenderError("plan evidence must have a SHA-256 identity")
    _enum(evidence["source"], {"Longbridge"}, f"{path}.evidence.source")
    _enum(evidence["timezone"], {"America/New_York"}, f"{path}.evidence.timezone")
    _enum(evidence["adjustment"], {"forward", "backward"}, f"{path}.evidence.adjustment")
    _date_text(evidence["as_of"], f"{path}.evidence.as_of")
    if _integer(evidence["bars_used"], f"{path}.evidence.bars_used") < 319:
        raise DashboardRenderError("plan evidence requires at least 319 completed daily bars")
    if _float_decimal(evidence["atr14"], f"{path}.evidence.atr14") <= 0:
        raise DashboardRenderError("plan ATR14 must be positive")

    zones = _array(item["zones"], f"{path}.zones")
    zone_kinds = []
    for index, raw in enumerate(zones):
        zone_path = f"{path}.zones[{index}]"
        zone = _object(raw, zone_path)
        zone_keys = {"kind", "low", "high", "currency", "condition", "derived_from", "data_status"}
        _reject_unknown(zone, zone_keys, zone_path)
        _required_keys(zone, zone_keys, zone_path)
        kind = _enum(zone["kind"], {"observation", "entry", "add", "reduce", "exit", "invalidation"}, f"{zone_path}.kind")
        zone_kinds.append(kind)
        if stage == "pre_entry" and kind == "add":
            raise DashboardRenderError("pre_entry plan cannot contain an add zone")
        low = _float_decimal(zone["low"], f"{zone_path}.low")
        high = _float_decimal(zone["high"], f"{zone_path}.high")
        if low <= 0 or high <= 0 or low > high:
            raise DashboardRenderError("plan zone price range is invalid")
        for key in ("currency", "condition", "derived_from"):
            _text(zone[key], f"{zone_path}.{key}")
            if re.search(r"\bSMA\d*\b", zone[key], re.IGNORECASE):
                raise DashboardRenderError("plan zones must use EMA rather than SMA")
        zone_status = _enum(zone["data_status"], {"complete", "partial", "stale"}, f"{zone_path}.data_status")
        if status == "confirmed" and zone_status != "complete":
            raise DashboardRenderError("confirmed plan zones must be complete")
    if zone_kinds.count("invalidation") != 1:
        raise DashboardRenderError("plan must contain exactly one invalidation zone")
    expected_action = "entry" if stage == "pre_entry" else "add"
    if status == "confirmed" and expected_action not in zone_kinds:
        raise DashboardRenderError("confirmed plan must contain its stage-appropriate action zone")
    if status == "confirmed" and not {"reduce", "exit"}.intersection(zone_kinds):
        raise DashboardRenderError("confirmed plan requires a reduce or exit boundary")
    return item


def _validate_positions_plans(value: Any) -> Dict[str, Any]:
    path = "$.positions_plans"
    item = _validate_status_module(value, path, {"title", "items", "strategy_categories"})
    _required_keys(item, {"title", "items"}, path)
    _text(item["title"], f"{path}.title")
    rows = _array(item["items"], f"{path}.items")
    categories = _array(item.get("strategy_categories", []), f"{path}.strategy_categories")
    for category in categories:
        _text(category, f"{path}.strategy_categories[]")
    if categories and (len(categories) != 5 or len(set(categories)) != 5):
        raise DashboardRenderError("strategy_categories must preserve five distinct confirmed labels")
    child_statuses = []
    for index, raw in enumerate(rows):
        row_path = f"{path}.items[{index}]"
        row = _object(raw, row_path)
        _reject_unknown(
            row,
            {
                "symbol",
                "display_name",
                "tab",
                "role",
                "holding_state",
                "plan_coverage",
                "trigger_distance",
                "near_trigger",
                "signals",
                "invalidation",
                "next_checks",
                "has_gap",
                "gap",
                "boundary",
                "data_status",
                "plan_detail",
                "strategy_category",
            },
            row_path,
        )
        _required_keys(
            row,
            {
                "symbol",
                "display_name",
                "tab",
                "role",
                "holding_state",
                "plan_coverage",
                "trigger_distance",
                "near_trigger",
                "signals",
                "invalidation",
                "next_checks",
                "has_gap",
                "gap",
                "boundary",
                "data_status",
            },
            row_path,
        )
        data_status = _enum(row["data_status"], MODULE_STATUSES, f"{row_path}.data_status")
        child_statuses.append(data_status)
        for key in (
            "symbol",
            "display_name",
            "role",
            "holding_state",
            "plan_coverage",
            "boundary",
        ):
            _text(row[key], f"{row_path}.{key}")
        _text(row["gap"], f"{row_path}.gap", required=False)
        _enum(row["tab"], PLAN_TABS, f"{row_path}.tab")
        if row.get("strategy_category") is not None:
            _text(row["strategy_category"], f"{row_path}.strategy_category")
            if row["strategy_category"] not in categories:
                raise DashboardRenderError("strategy category must belong to the confirmed five labels")
        _boolean(row["near_trigger"], f"{row_path}.near_trigger")
        _boolean(row["has_gap"], f"{row_path}.has_gap")
        trigger_path = f"{row_path}.trigger_distance"
        trigger = _object(row["trigger_distance"], trigger_path)
        _reject_unknown(trigger, {"label", "value", "tone"}, trigger_path)
        _required_keys(trigger, {"label", "value", "tone"}, trigger_path)
        _text(trigger["label"], f"{trigger_path}.label")
        _text(trigger["value"], f"{trigger_path}.value")
        _enum(trigger["tone"], TONES, f"{trigger_path}.tone")
        for key in ("signals", "invalidation", "next_checks"):
            values = _array(row[key], f"{row_path}.{key}")
            for value_index, value_text in enumerate(values):
                _text(value_text, f"{row_path}.{key}[{value_index}]")
        if row.get("plan_detail") is not None:
            _validate_plan_detail(row["plan_detail"], f"{row_path}.plan_detail")
            if row["tab"] == "plan" and row["plan_detail"]["plan_stage"] == "position_management":
                raise DashboardRenderError("position management belongs to holdings, not unheld buy plans")
        if data_status == "empty":
            raise DashboardRenderError(
                f"{row_path} empty position item cannot contain factual fields"
            )
    if item["status"] == "empty" and rows:
        raise DashboardRenderError(f"{path} empty status cannot contain child items")
    _validate_child_statuses(item, path, child_statuses)
    if item["status"] in {"partial", "stale"} and (
        not rows or not any(row["has_gap"] for row in rows)
    ) and not item.get("note", "").strip():
        raise DashboardRenderError(f"{path} {item['status']} status requires an explanatory gap or note")
    return item


def _validate_events(value: Any) -> Dict[str, Any]:
    path = "$.events"
    item = _validate_status_module(value, path, {"title", "display_timezone", "groups", "reference_at", "coverage"})
    _required_keys(item, {"title", "display_timezone", "groups"}, path)
    _text(item["title"], f"{path}.title")
    _text(item["display_timezone"], f"{path}.display_timezone")
    if "reference_at" in item:
        _rfc3339_timestamp(item["reference_at"], f"{path}.reference_at")
    for index, coverage in enumerate(_array(item.get("coverage", []), f"{path}.coverage")):
        coverage_path = f"{path}.coverage[{index}]"
        _object(coverage, coverage_path)
        _reject_unknown(coverage, {"label", "status", "note"}, coverage_path)
        _required_keys(coverage, {"label", "status", "note"}, coverage_path)
        _text(coverage["label"], f"{coverage_path}.label")
        _text(coverage["note"], f"{coverage_path}.note", required=False)
        _enum(coverage["status"], MODULE_STATUSES, f"{coverage_path}.status")
        if item["status"] == "complete" and coverage["status"] not in {"complete", "empty"}:
            raise DashboardRenderError("event coverage cannot be hidden by complete status")
    groups = _array(item["groups"], f"{path}.groups")
    child_statuses = []
    for group_index, raw_group in enumerate(groups):
        group_path = f"{path}.groups[{group_index}]"
        group = _object(raw_group, group_path)
        _reject_unknown(group, {"date", "label", "range", "events"}, group_path)
        _required_keys(group, {"date", "label", "range", "events"}, group_path)
        group_date = _date_text(group["date"], f"{group_path}.date")
        _text(group["label"], f"{group_path}.label")
        _text(group["range"], f"{group_path}.range")
        event_rows = _array(group["events"], f"{group_path}.events")
        for event_index, raw_event in enumerate(event_rows):
            event_path = f"{group_path}.events[{event_index}]"
            event = _object(raw_event, event_path)
            _reject_unknown(
                event,
                {
                    "shanghai_time",
                    "et_date",
                    "et_time",
                    "title",
                    "status",
                    "source",
                    "data_status",
                    "impact_channel",
                    "object",
                    "watch_for",
                    "kind",
                    "speaker",
                    "source_url",
                },
                event_path,
            )
            _required_keys(
                event,
                {
                    "shanghai_time",
                    "et_date",
                    "et_time",
                    "title",
                    "status",
                    "source",
                    "data_status",
                    "impact_channel",
                    "object",
                },
                event_path,
            )
            _clock_text(event["shanghai_time"], f"{event_path}.shanghai_time")
            et_date = _date_text(event["et_date"], f"{event_path}.et_date")
            _clock_text(event["et_time"], f"{event_path}.et_time")
            for key in ("title", "source", "impact_channel", "object"):
                _text(event[key], f"{event_path}.{key}")
            for key in ("watch_for", "speaker"):
                if key in event:
                    _text(event[key], f"{event_path}.{key}")
            if "kind" in event:
                _enum(event["kind"], {"macro", "earnings", "fed_speech"}, f"{event_path}.kind")
            if "source_url" in event:
                try:
                    url = urlsplit(_text(event["source_url"], f"{event_path}.source_url"))
                    port = url.port
                except ValueError as exc:
                    raise DashboardRenderError("invalid official event source URL") from exc
                host = (url.hostname or "").removeprefix("www.")
                if url.scheme != "https" or host not in FED_CALENDAR_HOSTS or url.username or url.password or port not in {None, 443} or url.query or url.fragment or re.search(r"\s", event["source_url"]):
                    raise DashboardRenderError("event source URL must be an approved official Fed calendar")
            if event.get("kind") == "fed_speech" and not (event.get("speaker") and event.get("source_url")):
                raise DashboardRenderError("Fed speech requires a named speaker and official calendar source")
            _enum(event["status"], EVENT_STATUSES, f"{event_path}.status")
            event_status = _enum(event["data_status"], MODULE_STATUSES, f"{event_path}.data_status")
            child_statuses.append(event_status)
            if event_status == "empty":
                raise DashboardRenderError(
                    f"{event_path} empty event item cannot contain factual fields"
                )
            shanghai_dt = _local_datetime(
                group_date,
                event["shanghai_time"],
                SHANGHAI_TZ,
                f"{event_path}.shanghai_time",
            )
            et_dt = _local_datetime(
                et_date,
                event["et_time"],
                NY_TZ,
                f"{event_path}.et_time",
            )
            if shanghai_dt.astimezone(UTC_TZ) != et_dt.astimezone(UTC_TZ):
                raise DashboardRenderError(
                    f"{event_path} Shanghai and ET times must be the same instant"
                )
    if item["status"] == "empty" and groups:
        raise DashboardRenderError(f"{path} empty status cannot contain child groups")
    _validate_child_statuses(item, path, child_statuses)
    return item


def _validate_data_note(value: Any) -> Dict[str, Any]:
    path = "$.data_note"
    item = _validate_status_module(value, path, {"title", "items", "boundary"})
    _required_keys(item, {"title", "items", "boundary"}, path)
    _text(item["title"], f"{path}.title")
    _text(item["boundary"], f"{path}.boundary")
    rows = _array(item["items"], f"{path}.items")
    child_statuses = []
    for index, raw in enumerate(rows):
        row_path = f"{path}.items[{index}]"
        row = _object(raw, row_path)
        _reject_unknown(row, {"label", "value", "state"}, row_path)
        _required_keys(row, {"label", "value", "state"}, row_path)
        _text(row["label"], f"{row_path}.label")
        _text(row["value"], f"{row_path}.value")
        child_statuses.append(_enum(row["state"], MODULE_STATUSES, f"{row_path}.state"))
    if item["status"] == "empty" and rows:
        raise DashboardRenderError(f"{path} empty status cannot contain child items")
    _validate_child_statuses(item, path, child_statuses)
    return item


def validate_packet(packet: Any) -> Dict[str, Any]:
    """Validate the complete V2 packet and return the same sanitized structure."""

    root = _object(packet, "$")
    _reject_unknown(root, TOP_LEVEL_KEYS, "$")
    _required_keys(root, TOP_LEVEL_KEYS, "$")
    _security_scan(root)
    source = copy.deepcopy(root)
    validated_meta = _validate_meta(source["meta"])
    if validated_meta["overall_status"] == "blocked":
        raise DashboardRenderError("packet overall status is blocked")
    validated = {
        "meta": validated_meta,
        "market": _validate_market(source["market"]),
        "account": _validate_account(source["account"]),
        "codex_analysis": _validate_analysis(source["codex_analysis"]),
        "operations": _validate_operations(source["operations"]),
        "positions_plans": _validate_positions_plans(source["positions_plans"]),
        "events": _validate_events(source["events"]),
        "data_note": _validate_data_note(source["data_note"]),
    }
    for key in TOP_LEVEL_KEYS - {"meta"}:
        if validated[key]["status"] == "blocked":
            raise DashboardRenderError(f"{key} status is blocked")
    if validated["meta"]["overall_status"] == "complete":
        for key in TOP_LEVEL_KEYS - {"meta"}:
            if validated[key]["status"] in {"partial", "stale", "blocked"}:
                raise DashboardRenderError(
                    f"complete overall status conflicts with {key} status"
                )
    return validated


def _float_decimal(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise DashboardRenderError(f"{path} must be a decimal")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DashboardRenderError(f"{path} must be a decimal") from exc
    if not math.isfinite(result):
        raise DashboardRenderError(f"{path} must be finite")
    return result


def _validate_weekly_section_item(value: Any, path: str) -> Dict[str, Any]:
    item = _object(value, path)
    allowed = {"label", "summary", "boundary", "evidence_kind", "item_kind", "data_status"}
    _reject_unknown(item, allowed, path)
    _required_keys(item, allowed, path)
    for key in ("label", "summary", "boundary"):
        _text(item[key], f"{path}.{key}")
        if WEEKLY_OPTION_IDENTITY_RE.search(item[key]):
            raise DashboardRenderError(f"{path}.{key} contains option contract identity")
    _enum(item["evidence_kind"], WEEKLY_EVIDENCE_KINDS, f"{path}.evidence_kind")
    _enum(item["item_kind"], WEEKLY_ITEM_KINDS, f"{path}.item_kind")
    _enum(item["data_status"], MODULE_STATUSES, f"{path}.data_status")
    return item


WEEKLY_METRIC_COUNT_FIELDS = (
    "eligible_episode_count",
    "covered_episode_count",
    "assessable_episode_count",
    "compliant_episode_count",
    "resolved_episode_count",
    "successful_episode_count",
    "open_episode_count",
    "flat_episode_count",
    "unverifiable_episode_count",
    "review_needed_count",
)
WEEKLY_METRIC_RATE_FIELDS = ("coverage_rate", "execution_rate", "plan_win_rate")
WEEKLY_PNL_UI_RE = re.compile(
    r"(?:盈亏|周度收益|(?:账户|组合|投资)收益率|时间加权|归因|现金流|\bTWR\b|\bP&L\b|\bprofit\b)",
    re.IGNORECASE,
)


def _episode_needs_review(row: Mapping[str, Any]) -> bool:
    return (
        row["compliance_status"] != "compliant"
        or row["outcome_status"] == "failure"
        or row["deviation_type"] is not None
    )


def build_weekly_packet(review: Mapping[str, Any]) -> Dict[str, Any]:
    """Project one weekly readback as inline increments, never a second page.

    Historical result tables and their legacy narrative items are deliberately
    not read into the new display contract. Missing v3 assessments stay blocked.
    """

    if not isinstance(review, Mapping):
        raise DashboardRenderError("weekly review readback must be an object")
    required = {
        "period_start", "period_end", "generated_at", "data_status",
        "freshness", "confirmation_status", "review_items",
    }
    missing = sorted(required - set(review))
    if missing:
        raise DashboardRenderError(f"weekly review readback missing field: {missing[0]}")
    sections: Dict[str, List[Dict[str, Any]]] = {
        name: [] for name in WEEKLY_SECTION_NAMES
    }
    for index, raw in enumerate(_array(review["review_items"], "$weekly_readback.review_items")):
        path = f"$weekly_readback.review_items[{index}]"
        row = _object(raw, path)
        subject = _text(row.get("subject"), f"{path}.subject")
        if "｜" not in subject:
            raise DashboardRenderError("weekly review item subject has no fixed section")
        subject_section, label = subject.split("｜", 1)
        section = WEEKLY_SUBJECT_SECTIONS.get(subject_section)
        if section is None or not label.strip():
            raise DashboardRenderError("weekly review item section is unsupported")
        visible_text = " ".join(
            str(row.get(key, "")) for key in ("subject", "summary", "evidence_boundary")
        )
        if WEEKLY_PNL_UI_RE.search(visible_text):
            continue
        sections[section].append(
            {
                "label": label.strip(),
                "summary": row.get("summary"),
                "boundary": row.get("evidence_boundary"),
                "evidence_kind": row.get("evidence_kind"),
                "item_kind": row.get("item_kind"),
                "data_status": row.get("data_status"),
            }
        )

    raw_metrics = review.get("execution_metrics")
    if raw_metrics is None:
        metrics = {key: 0 for key in WEEKLY_METRIC_COUNT_FIELDS}
        metrics.update({key: None for key in WEEKLY_METRIC_RATE_FIELDS})
        metrics.update(
            {
                "data_status": "blocked",
                "gap": "缺少可核验的事前计划或成交执行证据，暂不计算周度指标。",
            }
        )
    else:
        source_metrics = _object(raw_metrics, "$weekly_readback.execution_metrics")
        metrics = {
            key: source_metrics.get(key) for key in WEEKLY_METRIC_COUNT_FIELDS
        }
        metrics.update(
            {
                key: (
                    None if source_metrics.get(key) is None
                    else _float_decimal(source_metrics[key], f"$weekly_readback.execution_metrics.{key}")
                )
                for key in WEEKLY_METRIC_RATE_FIELDS
            }
        )
        metrics["data_status"] = source_metrics.get("data_status")
        metrics["gap"] = source_metrics.get("gap")

    review_episodes = []
    episode_keys = {
        "market_date", "underlying", "side", "plan_id", "plan_version",
        "coverage_status", "compliance_status", "outcome_status",
        "deviation_type", "reason", "next_rule", "data_status",
    }
    for index, raw in enumerate(_array(review.get("episode_assessments", []), "$weekly_readback.episode_assessments")):
        row = _object(raw, f"$weekly_readback.episode_assessments[{index}]")
        if _episode_needs_review(row):
            review_episodes.append({key: row.get(key) for key in episode_keys})

    freshness = _object(review["freshness"], "$weekly_readback.freshness")
    overall_status = review["data_status"]
    if metrics["data_status"] in {"partial", "stale", "blocked"} and overall_status == "complete":
        overall_status = "partial"
    packet = {
        "schema_version": WEEKLY_SCHEMA_VERSION,
        "meta": {
            "review_label": "周度复盘",
            "period_start": review["period_start"],
            "period_end": review["period_end"],
            "generated_at": review["generated_at"],
            "overall_status": overall_status,
            "freshness": freshness.get("status"),
            "confirmation_status": review["confirmation_status"],
        },
        "execution_metrics": metrics,
        "review_episodes": review_episodes,
        "sections": sections,
    }
    return validate_weekly_packet(packet)


def validate_weekly_packet(packet: Any) -> Dict[str, Any]:
    """Validate only the weekly increments accepted by the single daily UI."""

    root = _object(packet, "$weekly")
    top_keys = {
        "schema_version", "meta", "execution_metrics", "review_episodes", "sections"
    }
    _reject_unknown(root, top_keys, "$weekly")
    _required_keys(root, top_keys, "$weekly")
    if root["schema_version"] != WEEKLY_SCHEMA_VERSION:
        raise DashboardRenderError("unsupported weekly dashboard packet")
    _security_scan(
        {key: value for key, value in root.items() if key != "schema_version"},
        "$weekly",
    )
    source = copy.deepcopy(root)

    meta = _object(source["meta"], "$weekly.meta")
    meta_keys = {
        "review_label", "period_start", "period_end", "generated_at",
        "overall_status", "freshness", "confirmation_status",
    }
    _reject_unknown(meta, meta_keys | {"market_scope"}, "$weekly.meta")
    _required_keys(meta, meta_keys, "$weekly.meta")
    if "market_scope" in meta:
        _enum(meta["market_scope"], {"US"}, "$weekly.meta.market_scope")
    _text(meta["review_label"], "$weekly.meta.review_label")
    start = _date_text(meta["period_start"], "$weekly.meta.period_start")
    end = _date_text(meta["period_end"], "$weekly.meta.period_end")
    if start > end:
        raise DashboardRenderError("weekly period must not be reversed")
    _rfc3339_timestamp(meta["generated_at"], "$weekly.meta.generated_at")
    overall = _enum(meta["overall_status"], OVERALL_STATUSES, "$weekly.meta.overall_status")
    if overall == "blocked":
        raise DashboardRenderError("weekly packet overall status is blocked")
    _enum(meta["freshness"], {"current", "stale"}, "$weekly.meta.freshness")
    _enum(meta["confirmation_status"], {"pending", "confirmed"}, "$weekly.meta.confirmation_status")

    metrics = _object(source["execution_metrics"], "$weekly.execution_metrics")
    metric_keys = set(WEEKLY_METRIC_COUNT_FIELDS + WEEKLY_METRIC_RATE_FIELDS) | {"data_status", "gap"}
    _reject_unknown(metrics, metric_keys, "$weekly.execution_metrics")
    _required_keys(metrics, metric_keys, "$weekly.execution_metrics")
    for key in WEEKLY_METRIC_COUNT_FIELDS:
        _integer(metrics[key], f"$weekly.execution_metrics.{key}")
    metric_status = _enum(metrics["data_status"], MODULE_STATUSES, "$weekly.execution_metrics.data_status")
    gap = _optional_text(metrics["gap"], "$weekly.execution_metrics.gap")
    if metric_status in {"partial", "stale", "blocked"} and gap is None:
        raise DashboardRenderError("non-success weekly metrics require a gap")
    if metric_status in {"complete", "empty"} and gap is not None:
        raise DashboardRenderError("successful weekly metrics cannot include a gap")
    eligible = metrics["eligible_episode_count"]
    covered = metrics["covered_episode_count"]
    assessable = metrics["assessable_episode_count"]
    compliant = metrics["compliant_episode_count"]
    resolved = metrics["resolved_episode_count"]
    successful = metrics["successful_episode_count"]
    if not (compliant <= assessable <= covered <= eligible):
        raise DashboardRenderError("weekly execution counts have inconsistent denominators")
    if successful > resolved or metrics["review_needed_count"] > eligible:
        raise DashboardRenderError("weekly result counts exceed eligible episodes")
    if resolved + sum(metrics[key] for key in (
        "open_episode_count", "flat_episode_count", "unverifiable_episode_count"
    )) != eligible:
        raise DashboardRenderError("weekly outcome counts must partition eligible episodes")
    for key, numerator, denominator in (
        ("coverage_rate", covered, eligible),
        ("execution_rate", compliant, assessable),
        ("plan_win_rate", successful, resolved),
    ):
        rate = _number(metrics[key], f"$weekly.execution_metrics.{key}", allow_none=True)
        if denominator == 0:
            if rate is not None:
                raise DashboardRenderError("zero denominator must render an unavailable rate")
        elif rate is None or not 0 <= rate <= 1 or abs(rate - numerator / denominator) > 0.000001:
            raise DashboardRenderError("weekly rate does not match its numerator and denominator")
    if metric_status in {"empty", "blocked"} and any(metrics[key] for key in WEEKLY_METRIC_COUNT_FIELDS):
        raise DashboardRenderError("empty or blocked weekly metrics cannot contain factual counts")
    if metric_status == "complete" and eligible == 0:
        raise DashboardRenderError("complete weekly metrics require eligible episodes")
    if overall == "complete" and metric_status not in {"complete", "empty"}:
        raise DashboardRenderError("complete weekly packet conflicts with execution metrics")

    episode_fields = {
        "market_date", "underlying", "side", "plan_id", "plan_version",
        "coverage_status", "compliance_status", "outcome_status",
        "deviation_type", "reason", "next_rule", "data_status",
    }
    episodes = _array(source["review_episodes"], "$weekly.review_episodes")
    identities = set()
    for index, raw in enumerate(episodes):
        path = f"$weekly.review_episodes[{index}]"
        row = _object(raw, path)
        _reject_unknown(row, episode_fields, path)
        _required_keys(row, episode_fields, path)
        date = _date_text(row["market_date"], f"{path}.market_date")
        if not start <= date <= end:
            raise DashboardRenderError("weekly episode date is outside the review period")
        for key in ("underlying", "side", "reason", "next_rule"):
            _text(row[key], f"{path}.{key}")
            if WEEKLY_OPTION_IDENTITY_RE.search(row[key]):
                raise DashboardRenderError(f"{path}.{key} contains option contract identity")
            if WEEKLY_PNL_UI_RE.search(row[key]):
                raise DashboardRenderError(f"{path}.{key} contains retired result content")
        _optional_text(row["plan_id"], f"{path}.plan_id")
        version = _integer(row["plan_version"], f"{path}.plan_version", allow_none=True)
        if version is not None and version < 1:
            raise DashboardRenderError("weekly episode plan_version must be positive")
        _enum(row["coverage_status"], {"covered", "uncovered"}, f"{path}.coverage_status")
        _enum(row["compliance_status"], {"compliant", "non_compliant", "unassessable"}, f"{path}.compliance_status")
        _enum(row["outcome_status"], {"success", "failure", "open", "flat", "unverifiable"}, f"{path}.outcome_status")
        _optional_text(row["deviation_type"], f"{path}.deviation_type")
        _enum(row["data_status"], MODULE_STATUSES - {"empty"}, f"{path}.data_status")
        if not _episode_needs_review(row):
            raise DashboardRenderError("review_episodes must contain only episodes needing review")
        identity = (date, row["underlying"], row["side"])
        if identity in identities:
            raise DashboardRenderError("weekly review episodes contain a duplicate natural key")
        identities.add(identity)
    if len(episodes) != metrics["review_needed_count"]:
        raise DashboardRenderError("weekly review-needed count does not match displayed episodes")

    section_root = _object(source["sections"], "$weekly.sections")
    _reject_unknown(section_root, WEEKLY_SECTION_NAMES, "$weekly.sections")
    _required_keys(section_root, WEEKLY_SECTION_NAMES, "$weekly.sections")
    for section in WEEKLY_SECTION_NAMES:
        rows = _array(section_root[section], f"$weekly.sections.{section}")
        for index, raw in enumerate(rows):
            row = _validate_weekly_section_item(raw, f"$weekly.sections.{section}[{index}]")
            if any(WEEKLY_PNL_UI_RE.search(row[key]) for key in ("label", "summary", "boundary")):
                raise DashboardRenderError("weekly increments cannot contain retired result content")
    return source


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _class_tone(tone: str) -> str:
    return f" v2-tone-{_escape(tone)}"


def _status_badge(status: str) -> str:
    return (
        f'<span class="v2-status-badge v2-status-{_escape(status)}">'
        f"{_escape(STATUS_LABELS[status])}</span>"
    )


def _event_status_badge(status: str) -> str:
    tone = EVENT_STATUS_TONES[status]
    return f'<span class="v2-event-status v2-event-status-{tone}">{_escape(status)}</span>'


def _module_status(status: str, note: str = "") -> str:
    detail = f' <span class="v2-status-detail">{_ui(note, "")}</span>' if note else ""
    return f"{_status_badge(status)}{detail}"


def _format_number(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "不可用"
    if decimals == 0:
        return f"{value:,.0f}"
    return f"{value:,.{decimals}f}"


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "不可用"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):.2f}%"


def _direction_class(direction: str) -> str:
    return f"v2-direction-{_escape(direction)}"


def _count_text(value: Optional[int]) -> str:
    return "不可用" if value is None else str(value)


def _is_us(symbol: str) -> bool:
    return bool(US_SYMBOL_RE.fullmatch(symbol))


def _ui(value: Any, fallback: str = "待核对") -> str:
    """Render business copy only; diagnostic evidence stays in the private input."""
    text = str(value)
    if PRIVATE_DIAGNOSTIC_RE.search(text) or NON_US_SYMBOL_RE.search(text):
        return _escape(fallback)
    return _escape(re.sub(r"(?<=[A-Za-z0-9])\.US\b", "", text, flags=re.IGNORECASE))


def _time_label(value: str) -> str:
    if _is_rfc3339(value):
        instant = _rfc3339_timestamp(value, "display time").astimezone(SHANGHAI_TZ)
        return instant.strftime("%Y-%m-%d %H:%M") + " 北京"
    return value.replace("Asia/Shanghai", "北京").replace(" ET", " 纽约")


def _display_position_rows(positions: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = [copy.deepcopy(row) for row in positions["items"] if _is_us(row["symbol"])]
    holdings = {row["symbol"].upper(): row for row in rows if row["tab"] == "holdings"}
    visible = []
    for row in rows:
        held = holdings.get(row["symbol"].upper())
        if row["tab"] == "plan" and held:
            # Legacy technical-reference copies have no plan authority. A real
            # different plan needs explicit reconciliation by its producer.
            if row.get("plan_detail") and row["plan_detail"] != held.get("plan_detail"):
                raise DashboardRenderError("held symbol buy plan requires explicit holding assignment")
            continue
        visible.append(row)
    return visible


def _render_header(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    meta = packet["meta"]
    return f"""
      <header class="v2-header">
        <div class="v2-brand">
          <strong>美股复盘</strong>
        </div>
        <div class="v2-header-meta">
          <span>日度回看</span>
          <strong>{_escape(meta["review_date"])}</strong>
          <span>（ET）</span>
        </div>
        <div class="v2-header-meta v2-header-cutoff">
          <span>内容更新</span>
          <strong>{_escape(_time_label(meta["generated_at"]))}</strong>
        </div>
      </header>
      <div class="v2-boundary-strip">
        <span>{_ui(meta["review_label"], "盘前观察与交易纪律")}</span>
        <span>行情截至 {_escape(_time_label(meta["market_as_of"]))}</span>
        {_status_badge(meta["overall_status"])}
      </div>
      {_render_weekly_context(weekly)}
    """


def _render_market(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    market = packet["market"]
    rows = []
    for row in market["items"]:
        direction = row["direction"]
        # Non-complete rows may carry a directional hint, but it is not a
        # verified change. Keep the glyph while removing red/green emphasis.
        direction_tone = (
            direction
            if market["status"] == "complete" and row["data_status"] == "complete"
            else "flat"
        )
        proxy_note = (
            f' · 代理：{_ui(row["proxy_for"])}'
            if row["is_proxy"]
            else ""
        )
        value = _format_number(row["value"])
        change = _format_pct(row["change_pct"])
        dots = "".join(
            f'<span class="v2-meter-dot{" is-on" if index < row["strength"] else ""}" aria-hidden="true"></span>'
            for index in range(3)
        )
        flow = ""
        if row.get("capital_flow"):
            capital = row["capital_flow"]
            flow_value = _format_number(capital["value"])
            flow = (
                f'<small class="v2-flow">标的资金流 · '
                f'{_escape(capital["label"])} {flow_value} · '
                f'{_escape(STATUS_LABELS[capital["data_status"]])}</small>'
            )
        unavailable = (
            f'<small class="v2-unavailable-note">{_ui(row["unavailable_reason"], "报价待核对")}</small>'
            if row.get("unavailable_reason")
            else ""
        )
        rows.append(
            f"""
            <div class="v2-market-row">
              <div class="v2-market-name">
                <strong>{_ui(row["name"])}</strong>
                <small>{_ui(row["symbol"])}{proxy_note} · {_ui(row["session"])}</small>
                {flow}{unavailable}
              </div>
              <div class="v2-market-direction {_direction_class(direction_tone)}">
                <strong>{_escape(DIRECTION_LABELS[direction])}</strong>
                <small>{_escape(change)}</small>
              </div>
              <div class="v2-market-strength" aria-label="强度 {row["strength"]}/3">
                {dots}
              </div>
              <div class="v2-market-state">
                <strong>{_ui(row["state"])}</strong>
                <small>{_escape(value)}</small>
              </div>
            </div>
            """
        )
    body = (
        '<div class="v2-empty">暂无已确认市场数据</div>'
        if not rows
        else "".join(rows)
    )
    return f"""
      <section class="v2-market" aria-labelledby="market-heading">
        <div class="v2-section-title">
          <h1 id="market-heading">市场风险雷达</h1>
          {_module_status(market["status"])}
        </div>
        <p class="v2-side-note">相对昨日收盘；代理价格不等同于指数或收益率。</p>
        <div class="v2-market-head" role="row">
          <span>资产/指数</span><span>方向</span><span>强度</span><span>状态</span>
        </div>
        <div class="v2-market-list" role="table">
          {body}
        </div>
        <p class="v2-side-note">Longbridge · {_escape(_time_label(packet["meta"]["market_as_of"]))}</p>
        {_weekly_inline(weekly, "market_radar", "周度市场背景")}
      </section>
    """


def _render_analysis(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    analysis = packet["codex_analysis"]

    def render_items(items: Sequence[Mapping[str, Any]], empty: str) -> str:
        visible = [item for item in items if _ui(item["text"], "")]
        if not visible:
            return f'<li class="v2-empty-inline">{_escape(empty)}</li>'
        return "".join(
            f'<li><strong>{_ui(item["label"], "观察")}</strong><span>{_ui(item["text"])}</span></li>'
            for item in visible
        )

    facts = render_items(analysis["facts"], "暂无已确认事实")
    risks = render_items(analysis["risks"], "暂无已确认主要风险")
    interpretations = render_items(analysis["interpretation"], "暂无额外解释")
    gaps = render_items(analysis["gaps"], "当前没有额外缺口")
    checks = []
    for check in analysis["checks"]:
        checks.append(
            f"""
            <div class="v2-check-row">
              <div><strong>如果</strong><span>{_ui(check["if"], "条件待确认")}</span></div>
              <div><strong>则</strong><span>{_ui(check["then"], "先核对计划再行动")}</span></div>
              <div><strong>否则</strong><span>{_ui(check["else"], "等待确认，不新增动作")}</span></div>
            </div>
            """
        )
    checks_body = "".join(checks) if checks else '<p class="v2-empty-inline">暂无条件式检查</p>'
    return f"""
      <section class="v2-judgement" aria-labelledby="judgement-heading">
        <div class="v2-section-title">
          <h1 id="judgement-heading">Codex 盘前判断 <span>（核心结论）</span></h1>
          {_module_status(analysis["status"])}
          <span class="v2-period">{_ui(packet["meta"]["period_label"], "盘前观察")}</span>
        </div>
        <p class="v2-headline">{_ui(analysis["headline"], "先核对持仓计划，再评估新的买入机会。")}</p>
        <div class="v2-analysis-grid">
          <section class="v2-analysis-card v2-card-fact">
            <h2>已确认事实</h2>
            <ul>{facts}</ul>
          </section>
          <section class="v2-analysis-card v2-card-risk">
            <h2>主要风险</h2>
            <ul>{risks}</ul>
          </section>
        </div>
        <section class="v2-analysis-card v2-card-interpretation">
          <h2>Codex 解释</h2>
          <ul>{interpretations}</ul>
        </section>
        <section class="v2-checks">
          <h2>今日条件式行动</h2>
          <div class="v2-check-list">{checks_body}</div>
        </section>
        <details class="v2-analysis-card v2-card-gap">
          <summary>待确认事项</summary>
          <ul>{gaps}</ul>
        </details>
        {_weekly_inline(weekly, "judgement", "周度判断与纪律")}
      </section>
    """


def _render_operations(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    operations = packet["operations"]
    executions = operations["executions"]
    us_items = [row for row in operations["items"] if _is_us(row["symbol"])]
    scoped = operations.get("market_scope") == "US" and len(us_items) == len(operations["items"])
    # Submission/action text is not evidence of a fill. Legacy rows without
    # an explicit count remain private until the producer verifies them.
    filled_items = [row for row in us_items if (row.get("execution_count") or 0) > 0 and row["data_status"] not in {"empty", "blocked"}]
    confirmed_total = scoped and executions["data_status"] in {"complete", "empty"} and executions["count"] is not None
    if filled_items:
        items = "".join(
            f'<li><strong>{_ui(row["action"])} · {_ui(row["display_name"])}</strong>'
            f'<span>{_ui(row["role"], "")} · {_ui(row["state"], "待核对")} · '
            f'{_ui(row["plan_relation"], "执行是否符合计划待核对")}</span></li>'
            for row in filled_items
        )
        if confirmed_total and sum(row["execution_count"] for row in filled_items) < executions["count"]:
            items += '<li class="v2-empty-inline">另有成交明细尚待核对。</li>'
    elif confirmed_total and executions["count"] == 0:
        items = '<li class="v2-empty-inline">上一交易日无已成交记录。</li>'
    else:
        items = '<li class="v2-empty-inline">成交明细尚待核对。</li>'
    freshness = '<span>成交记录较旧，请重新核对。</span>' if executions["data_status"] == "stale" else ""
    return f"""
      <section class="v2-operations" aria-labelledby="operations-heading">
        <div class="v2-section-title">
          <h1 id="operations-heading">上一交易日成交</h1>
          <span class="v2-section-note">只看实际成交 · 对照事前计划</span>
        </div>
        <div class="v2-operation-meta">
          <span>{_escape(packet["meta"]["review_date"])} · 纽约交易日</span>
          {freshness}
        </div>
        <ul class="v2-operations-list">{items}</ul>
      </section>
    """


def _render_plan_detail(detail: Optional[Mapping[str, Any]]) -> str:
    if detail is None:
        return ""
    setup_labels = {
        "pullback": "趋势回调",
        "breakout": "突破确认",
        "range": "区间交易",
        "bottom_reversal": "抄底反转（右侧确认）",
        "position_management": "买入后仓位管理",
    }
    zone_labels = {
        "observation": "观察区间", "entry": "建仓区间",
        "add": "加仓区间", "reduce": "减仓区间",
        "exit": "退出区间", "invalidation": "失效区间",
    }
    quote_labels = {
        "below": "报价低于区间", "inside": "报价位于区间",
        "above": "报价高于区间", "stale": "报价陈旧，区间保持不变",
        "unavailable": "报价不可用，区间保持不变",
    }
    status_labels = {"draft": "待确认草案", "confirmed": "已确认计划", "expired": "已到期"}
    status_classes = {"draft": "partial", "confirmed": "complete", "expired": "stale"}
    evidence = detail["evidence"]
    zones = []
    for zone in detail["zones"]:
        label = zone_labels[zone["kind"]]
        if zone["kind"] == "add" and detail["plan_status"] != "confirmed":
            label += " · 待单独确认"
        zones.append(
            f'<div class="v2-plan-zone" data-zone-kind="{_escape(zone["kind"])}">'
            f'<strong>{_escape(label)}</strong>'
            f'<span>{_escape(_format_number(_float_decimal(zone["low"], "zone.low")))}–{_escape(_format_number(_float_decimal(zone["high"], "zone.high")))} {_escape(zone["currency"])}</span>'
            f'<small>{_ui(zone["condition"], "条件待确认")}</small></div>'
        )
    action_kind = "entry" if detail["plan_stage"] == "pre_entry" else "add"
    if not any(zone["kind"] == action_kind for zone in detail["zones"]):
        readiness = "仅观察：确认条件未齐，暂无可执行区间。"
    elif detail["plan_status"] != "confirmed":
        readiness = "区间仅为草案；该版本经你确认后才生效。"
    else:
        readiness = "仅在该版本的全部条件满足时有效，不是无条件买卖指令。"
    return f"""
      <div class="v2-plan-detail">
        <div class="v2-plan-detail-header">
          <strong>{_escape(setup_labels[detail['setup_type']])}</strong>
          <span class="v2-status-badge v2-status-{status_classes[detail['plan_status']]}">{_escape(status_labels[detail['plan_status']])}</span>
          <small>{_escape(quote_labels[detail['quote_relation']])}</small>
        </div>
        <small class="v2-plan-evidence">技术参考：{_escape(evidence['as_of'])} 收盘 · EMA20/50/200 · ATR14 {_escape(evidence['atr14'])}</small>
        <div class="v2-plan-zones">{"".join(zones)}</div>
        <small class="v2-plan-evidence">{_escape(readiness)}</small>
      </div>
    """


def _render_plan_row(row: Mapping[str, Any], allow_verified_tone: bool) -> str:
    trigger = row["trigger_distance"]
    trigger_tone = trigger["tone"]
    if not allow_verified_tone and trigger_tone in {"red", "green"}:
        trigger_tone = "amber"
    def list_text(values: Sequence[str], empty: str = "未提供") -> str:
        if not values:
            return f'<span class="v2-unavailable">{_escape(empty)}</span>'
        return "".join(f"<span>{_ui(value, empty)}</span>" for value in values)

    classes = [
        "v2-plan-row",
        f'v2-tab-{row["tab"]}',
        "v2-near-trigger" if row["near_trigger"] else "",
        "v2-has-gap" if row["has_gap"] else "",
    ]
    classes = " ".join(value for value in classes if value)
    gap = (
        f'<small class="v2-gap-label">{_ui(row["gap"], "计划条件待确认")}</small>'
        if row["has_gap"] and row["gap"]
        else ""
    )
    symbol = _ui(row["symbol"])
    display_name = _ui(row["display_name"])
    symbol_note = f"<small>{symbol}</small>" if symbol != display_name else ""
    detail_label = "查看持仓计划" if row["tab"] == "holdings" else "查看买入计划"
    if not row.get("plan_detail"):
        detail_label = "查看观察条件与下一步"
    return f"""
      <div class="{classes}" data-tab="{_escape(row["tab"])}">
        <div class="v2-plan-symbol"><strong>{display_name}</strong>{symbol_note}</div>
        <div class="v2-plan-role"><strong>{_ui(row["role"], "持仓" if row["tab"] == "holdings" else "买入候选")}</strong><small>{_ui(row["holding_state"], "本次读取时持仓" if row["tab"] == "holdings" else "尚未持有")}</small></div>
        <div class="v2-plan-coverage">{_ui(row["plan_coverage"], "计划待确认")}</div>
        <div class="v2-trigger v2-tone-{_escape(trigger_tone)}"><small>{_ui(trigger["label"], "触发条件")}</small><strong>{_ui(trigger["value"], "待确认")}</strong></div>
        <details class="v2-plan-checks">
          <summary>{detail_label}</summary>
          <div class="v2-plan-check-grid">
            <div class="v2-plan-list"><strong>验证信号</strong>{list_text(row["signals"], "信号待确认")}</div>
            <div class="v2-plan-list"><strong>失效条件</strong>{list_text(row["invalidation"], "失效条件待确认")}</div>
            <div class="v2-plan-list"><strong>下一步检查</strong>{list_text(row["next_checks"], "先确认计划")}</div>
          </div>
          {gap}
          {_render_plan_detail(row.get("plan_detail"))}
        </details>
      </div>
    """


def _render_positions(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    positions = packet["positions_plans"]
    visible = _display_position_rows(positions)
    held = [row for row in visible if row["tab"] == "holdings"]
    plans = [row for row in visible if row["tab"] == "plan"]
    categories = positions.get("strategy_categories", [])

    def render_rows(rows: Sequence[Mapping[str, Any]]) -> str:
        return "".join(_render_plan_row(row, allow_verified_tone=positions["status"] == "complete") for row in rows)

    holdings_body = render_rows(held) or '<p class="v2-empty v2-view-empty">暂无已核验的当前持仓。</p>'
    plan_groups = []
    for category in categories:
        members = [row for row in plans if row.get("strategy_category") == category]
        plan_groups.append(
            f'<section class="v2-strategy-group"><h2>{_ui(category, "分类待确认")}<span>{len(members)} 个候选</span></h2>'
            + (render_rows(members) or '<p class="v2-empty-inline">暂未加入候选</p>') + '</section>'
        )
    uncategorized = [row for row in plans if not row.get("strategy_category")]
    if uncategorized:
        plan_groups.append('<section class="v2-strategy-group"><h2>待分类</h2>' + render_rows(uncategorized) + '</section>')
    category_note = '<p class="v2-plan-view-note">原五类策略名称待确认；不会用技术形态替代。</p>' if not categories else ""
    plans_body = "".join(plan_groups)
    if not plans:
        plans_body += '<p class="v2-empty v2-view-empty">暂无已核验的未持仓买入候选。已有仓位请切换至“当前持仓及计划”。</p>'
    holdings_checked = " checked" if held or not plans else ""
    plan_checked = "" if holdings_checked else " checked"
    filter_empty = '<p class="v2-empty v2-filter-empty" role="status">没有符合当前筛选条件的标的；取消筛选可查看全部。</p>'
    return f"""
      <section class="v2-plans" aria-labelledby="plans-heading">
        <div class="v2-section-title v2-plans-title">
          <h1 id="plans-heading">持仓 × 计划</h1>
          {_module_status(positions["status"])}
        </div>
        {_render_execution_strip(weekly)}
        <div class="v2-plan-controls">
          <div class="v2-tabs" role="group" aria-label="持仓和计划视图">
            <label for="v2-view-holdings"><input class="v2-state" id="v2-view-holdings" type="radio" name="v2-plan-view"{holdings_checked} aria-label="查看当前持仓及计划" aria-controls="v2-plan-panel">当前持仓及计划 <small>{len(held)}</small></label>
            <label for="v2-view-plan"><input class="v2-state" id="v2-view-plan" type="radio" name="v2-plan-view"{plan_checked} aria-label="查看未持仓买入计划" aria-controls="v2-plan-panel">未持仓买入计划 <small>{len(plans)}</small></label>
          </div>
          <div class="v2-filters" role="group" aria-label="持仓和计划筛选">
            <label for="v2-filter-near"><input class="v2-state" id="v2-filter-near" type="checkbox" aria-label="只看接近触发" aria-controls="v2-plan-panel">只看接近触发</label>
            <label for="v2-filter-gap"><input class="v2-state" id="v2-filter-gap" type="checkbox" aria-label="只看待确认" aria-controls="v2-plan-panel">只看待确认</label>
          </div>
        </div>
        <div id="v2-plan-panel" class="v2-plan-scroll" role="region" aria-label="持仓与计划内容">
          <div class="v2-plan-list-grid" data-holdings-checked="{holdings_checked.strip()}" data-plan-checked="{plan_checked.strip()}">
            <div class="v2-plan-view v2-tab-holdings">
              <p class="v2-plan-view-note">已有仓位的计划随持仓查看；买入之后才评估加仓。</p>
              <div class="v2-plan-head"><span>标的</span><span>持仓状态</span><span>自身计划</span><span>触发条件</span></div>
              {holdings_body}{filter_empty if held else ""}
            </div>
            <div class="v2-plan-view v2-tab-plan">
              {category_note}{plans_body}{filter_empty if plans else ""}
            </div>
          </div>
        </div>
        {_render_weekly_plan_review(weekly)}
      </section>
    """


def _calendar_rows(packet: Mapping[str, Any], weekly: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    events = packet["events"]
    rows = [copy.deepcopy(row) for group in events["groups"] for row in group["events"]]
    # Explicit reference_at means the producer supplied the combined calendar.
    # Older daily inputs can still use timestamped weekly events, but their
    # internal boundary prose is never interpreted as an impact or source.
    if weekly is not None and "reference_at" not in events:
        known = {(row["et_date"], row["et_time"][:5], row["title"].strip().casefold()) for row in rows}
        for row in weekly["sections"]["events"]:
            if not _is_rfc3339(row["label"]) or not _ui(row["summary"], ""):
                continue
            instant = _rfc3339_timestamp(row["label"], "weekly event").astimezone(NY_TZ)
            identity = (instant.date().isoformat(), instant.strftime("%H:%M"), row["summary"].strip().casefold())
            if identity in known:
                continue
            rows.append({
                "et_date": identity[0], "et_time": identity[1],
                "shanghai_time": instant.astimezone(SHANGHAI_TZ).strftime("%H:%M"),
                "title": row["summary"], "status": "未验证", "source": "排期待核对",
                "data_status": "partial", "object": "影响对象待补充", "impact_channel": "影响因素待补充",
            })
            known.add(identity)
    unique = {}
    for row in rows:
        if not _ui(row["title"], "") or NON_US_SYMBOL_RE.search(row["object"]):
            continue
        identity = (row["et_date"], row["et_time"], row["title"].strip().casefold(), row["object"].strip().casefold())
        existing = unique.get(identity)
        if existing is None:
            unique[identity] = row
        elif existing != row:
            # Never choose an optimistic status when two sources disagree.
            if existing["status"] != row["status"] or existing["data_status"] != row["data_status"]:
                existing["status"], existing["data_status"] = "未验证", "partial"
            for key in ("watch_for", "speaker", "source_url", "kind"):
                if key not in existing and key in row:
                    existing[key] = row[key]
    return sorted(unique.values(), key=lambda row: (row["et_date"], row["et_time"], row["title"]))


def _event_title(value: str) -> str:
    return re.sub(r"^美国\s*[,，]\s*", "", value)


def _render_calendar_event(event: Mapping[str, Any]) -> str:
    instant = _local_datetime(event["et_date"], event["et_time"], NY_TZ, "event time")
    beijing = instant.astimezone(SHANGHAI_TZ)
    speech = f'<span class="v2-speech-tag">联储讲话 · {_ui(event["speaker"])}</span>' if event.get("kind") == "fed_speech" else ""
    # Coverage diagnostics and routine expected/unreleased states stay private.
    # Only a concrete exception that changes how this event is used is shown.
    status = _event_status_badge("已取消") if event["status"] == "已取消" else ""
    if event["data_status"] == "stale":
        status += '<span class="v2-event-caution">排期较旧，请重新核对</span>'
    elif event["status"] == "未验证" or event["data_status"] == "blocked":
        status += '<span class="v2-event-caution">事件信息待核对</span>'
    watch = "".join(
        f'<p class="v2-event-watch">{safe}</p>'
        for line in event.get("watch_for", "").splitlines()
        if (safe := _ui(line.strip(), ""))
    )
    return f"""
      <article class="v2-event-row">
        <div class="v2-event-times"><strong>{instant.strftime('%H:%M')} 纽约</strong><small>{beijing.strftime('%m-%d %H:%M')} 北京</small></div>
        <div class="v2-event-main">
          <div class="v2-event-title"><strong>{_ui(_event_title(event['title']))}</strong>{speech}{status}</div>
          <p class="v2-event-impact"><strong>{_ui(event['object'], '影响对象待补充')}</strong> · {_ui(event['impact_channel'], '影响因素待补充')}</p>
          {watch}
        </div>
      </article>
    """


def _render_events(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    events = packet["events"]
    reference = _rfc3339_timestamp(events.get("reference_at", packet["meta"]["generated_at"]), "calendar reference").astimezone(NY_TZ)
    monday = reference.date() - dt.timedelta(days=reference.weekday())
    rows = _calendar_rows(packet, weekly)
    rows = [row for row in rows if monday.isoformat() <= row["et_date"] < (monday + dt.timedelta(days=14)).isoformat()]
    weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    weeks = []
    for week_index, label in enumerate(("本周", "下周")):
        start = monday + dt.timedelta(days=7 * week_index)
        end = start + dt.timedelta(days=6)
        days = []
        for day_index, weekday in enumerate(weekdays):
            day = start + dt.timedelta(days=day_index)
            members = [row for row in rows if row["et_date"] == day.isoformat()]
            titles = " / ".join(_event_title(row["title"]) for row in members[:2])
            if len(members) > 2:
                titles += " 等"
            preview = _ui(titles, "事件信息待核对") if members else "暂无已收录事件"
            count_label = f"{len(members)} 项"
            opened = " open" if day == reference.date() and members else ""
            body = "".join(_render_calendar_event(row) for row in members)
            if not body:
                body = '<p class="v2-empty-inline">暂无已收录事件。</p>'
            days.append(
                f'<details class="v2-calendar-day" data-date="{day.isoformat()}"{opened}>'
                f'<summary><span class="v2-calendar-date"><strong>{weekday}</strong><time datetime="{day.isoformat()}">{day.strftime("%m/%d")}</time></span>'
                f'<span class="v2-calendar-preview">{preview}</span><small>{count_label}</small></summary>'
                f'<div class="v2-event-list">{body}</div></details>'
            )
        weeks.append(f'<section class="v2-calendar-week"><h2>{label}<span>{start.isoformat()} — {end.isoformat()}</span></h2>{"".join(days)}</section>')
    freshness = '<p class="v2-calendar-asof">日历较旧，使用前请重新核对排期。</p>' if events["status"] == "stale" else ""
    return f"""
      <section class="v2-events" aria-labelledby="events-heading">
        <div class="v2-section-title">
          <h1 id="events-heading">重要事件与时间轴</h1>
          <span class="v2-section-note">按纽约日期分桶 · 同时显示北京时间</span>
        </div>
        <p class="v2-calendar-asof">日历核对：{_escape(_time_label(events.get('reference_at', packet['meta']['generated_at'])))} · 以下为情景分析，不是已公布结果或确定涨跌。</p>
        {freshness}
        <div class="v2-calendar-weeks">{"".join(weeks)}</div>
      </section>
    """


def _render_data_note(packet: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None) -> str:
    # Raw diagnostics are intentionally not placed in hidden/collapsed HTML.
    weekly_time = _escape(_time_label(weekly["meta"]["generated_at"])) if weekly else "尚未生成"
    return f"""
      <details class="v2-data-note">
        <summary><strong>更新与使用说明</strong><span>点击展开</span></summary>
        <div class="v2-data-content">
          <p>行情截至：{_escape(_time_label(packet['meta']['market_as_of']))}</p>
          <p>周度更新：{weekly_time}。周度内容不随每日页面刷新而重新计算。</p>
          <p>刷新仅重载这份记录，不代表新行情；未确认的计划不能直接执行。</p>
        </div>
      </details>
    """


def _weekly_item_rows(rows: Sequence[Mapping[str, Any]], empty_text: str) -> str:
    rows = [row for row in rows if _ui(row["label"], "") and _ui(row["summary"], "")]
    if not rows:
        return f'<p class="v2-empty-inline">{_escape(empty_text)}</p>'
    return "".join(
        f"""
        <article class="v2-weekly-item">
          <div class="v2-weekly-item-head">
            <strong>{_ui(row['label'])}</strong>
            {_status_badge(row['data_status'])}
          </div>
          <p>{_ui(row['summary'])}</p>
        </article>
        """
        for row in rows
    )


def _weekly_period(packet: Dict[str, Any]) -> str:
    meta = packet["meta"]
    return f"{meta['period_start']} 至 {meta['period_end']}"


def _weekly_freshness_label(packet: Dict[str, Any]) -> str:
    return "" if packet["meta"]["freshness"] == "current" else "内容陈旧，请重新复核"


def _render_weekly_context(packet: Optional[Dict[str, Any]]) -> str:
    if packet is None:
        return '<div class="v2-weekly-context"><span>周度复盘尚未生成</span></div>'
    confirmation = "已确认" if packet["meta"]["confirmation_status"] == "confirmed" else "待确认"
    state = " · ".join(value for value in (_weekly_freshness_label(packet), confirmation) if value)
    return f"""
      <div class="v2-weekly-context">
        <strong>周度复盘 · {_escape(_weekly_period(packet))}</strong>
        <span>{_escape(state)}</span>
        <small>周度更新：{_escape(_time_label(packet['meta']['generated_at']))}</small>
      </div>
    """


def _weekly_inline(
    packet: Optional[Dict[str, Any]],
    section: str,
    heading: str,
) -> str:
    if packet is None or not packet["sections"][section]:
        return ""
    if section in {"operations", "positions_plan", "next_week"} and packet["meta"].get("market_scope") != "US":
        rows = '<p class="v2-empty-inline">美股范围尚待核对。</p>'
    else:
        rows = _weekly_item_rows(packet["sections"][section], "交易结论待补充")
    return f"""
      <details class="v2-weekly-inline" data-weekly-section="{_escape(section)}">
        <summary><strong>{_escape(heading)}</strong><span>{_escape(_weekly_period(packet))}</span></summary>
        <div class="v2-weekly-stack">{rows}</div>
      </details>
    """


def _render_execution_strip(packet: Optional[Dict[str, Any]]) -> str:
    if packet is None:
        return ""
    metrics = packet["execution_metrics"]
    scoped = packet["meta"].get("market_scope") == "US"
    unavailable = metrics["data_status"] in {"blocked", "empty"} or not scoped

    def rate_text(key: str) -> str:
        value = metrics[key]
        return "不可计算" if value is None or unavailable else f"{value * 100:.1f}%"

    cards = (
        (
            "计划覆盖率",
            rate_text("coverage_rate"),
            f"事前计划 {metrics['covered_episode_count']} / 适用 {metrics['eligible_episode_count']}",
        ),
        (
            "按计划执行率",
            rate_text("execution_rate"),
            f"完全遵守 {metrics['compliant_episode_count']} / 可评估 {metrics['assessable_episode_count']}",
        ),
        (
            "计划胜率",
            rate_text("plan_win_rate"),
            f"成功 {metrics['successful_episode_count']} / 已结案 {metrics['resolved_episode_count']}",
        ),
        (
            "需复盘",
            "不可计算" if unavailable else f"{metrics['review_needed_count']} 笔",
            "违规、计划失败或证据不足",
        ),
    )
    rendered_cards = "".join(
        f'<div class="v2-execution-metric"><small>{_escape(label)}</small>'
        f'<strong>{_escape(value)}</strong>'
        f'<span>{_escape("" if unavailable else denominator)}</span></div>'
        for label, value, denominator in cards
    )
    exclusions = (
        "排除于胜率分母："
        f"未结束 {metrics['open_episode_count']} · 持平 {metrics['flat_episode_count']} · "
        f"不可核验 {metrics['unverifiable_episode_count']}。"
    ) if not unavailable else ""
    unavailable_note = "本周没有适用的交易。" if metrics["data_status"] == "empty" else "缺少事前计划或完整执行记录，暂不能评估。"
    if not scoped:
        unavailable_note = "美股统计范围待核对，暂不展示比例。"
    gap = f'<p class="v2-execution-gap">{_escape(unavailable_note)}</p>' if unavailable else (f'<p class="v2-execution-gap">{_ui(metrics["gap"], "部分交易仍待复核。")}</p>' if metrics["gap"] else "")
    return f"""
      <div class="v2-execution-quality" aria-label="周度计划执行质量">
        <div class="v2-execution-heading"><strong>周度执行质量</strong><span>{_escape(_weekly_period(packet))}</span>{_status_badge(metrics['data_status'] if scoped else 'partial')}</div>
        <div class="v2-execution-metrics">{rendered_cards}</div>
        <p class="v2-execution-exclusions">{_escape(exclusions)}</p>
        {gap}
      </div>
    """


def _render_weekly_plan_review(packet: Optional[Dict[str, Any]]) -> str:
    if packet is None:
        return ""
    compliance_labels = {
        "compliant": "按计划执行",
        "non_compliant": "未按计划执行",
        "unassessable": "执行不可评估",
    }
    outcome_labels = {
        "success": "计划成功", "failure": "计划失败",
        "open": "尚未结束", "flat": "结果持平", "unverifiable": "结果不可核验",
    }
    episodes = []
    for row in packet["review_episodes"]:
        if not _is_us(row["underlying"]):
            continue
        plan_ref = "无事前已确认计划" if row["plan_id"] is None else "依据事前已确认计划复核"
        episodes.append(
            f'<article class="v2-episode-review"><div><strong>{_escape(row["market_date"])} · {_ui(row["underlying"])} · {_escape({"buy": "买入", "sell": "卖出"}.get(row["side"], "交易"))}</strong>'
            f'{_status_badge(row["data_status"])}</div>'
            f'<p>{_escape(compliance_labels[row["compliance_status"]])} · {_escape(outcome_labels[row["outcome_status"]])}</p>'
            f'<small>{_escape(plan_ref)}</small>'
            f'<p><strong>原因：</strong>{_ui(row["reason"], "原因待复核")}</p>'
            f'<p><strong>下一条规则：</strong>{_ui(row["next_rule"], "先核对事前计划")}</p></article>'
        )
    episode_block = ""
    if episodes:
        episode_block = (
            '<details class="v2-weekly-inline v2-episode-details"><summary>'
            f'<strong>需具体复盘 · {len(episodes)} 笔</strong><span>只看执行与规则</span>'
            '</summary><div class="v2-weekly-stack">' + "".join(episodes) + '</div></details>'
        )
    return (
        episode_block
        + _weekly_inline(packet, "positions_plan", "周度持仓计划回看")
        + _weekly_inline(packet, "plan_review", "计划复核与纪律")
        + _weekly_inline(packet, "next_week", "后续计划待确认")
    )


def _template_security_errors(template: str) -> List[str]:
    lowered = template.lower()
    errors = []
    for marker in (
        "<script",
        "<iframe",
        "<link",
        "<img",
        "<object",
        "<embed",
        "<svg",
        "srcdoc",
        "document.write",
        "innerhtml",
        "outerhtml",
        "insertadjacenthtml",
        "eval(",
        "new function(",
        "fetch(",
        "xmlhttprequest",
        "websocket",
        "javascript:",
        "data:text/html",
        "@import",
        "url(",
        "https://",
        "http://",
    ):
        if marker in lowered:
            errors.append(marker)
    if re.search(r"\bon[a-z][a-z0-9_-]*\s*=", lowered):
        errors.append("inline event handler")
    if template.count("<style") != 1 or template.count("</style>") != 1:
        errors.append("style block")
    return errors


def _validate_template(template: str) -> None:
    try:
        bundled_template = DEFAULT_TEMPLATE.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DashboardRenderError("bundled V2 template is unavailable") from exc
    if template != bundled_template:
        raise DashboardRenderError("template must match the bundled V2 template")
    if template.count(BODY_MARKER) != 1:
        raise DashboardRenderError("template must contain exactly one V2 body marker")
    template_errors = _template_security_errors(template)
    if template_errors:
        raise DashboardRenderError(f"template security violation: {template_errors[0]}")


def _render_daily_content(
    validated: Dict[str, Any],
    weekly: Optional[Dict[str, Any]] = None,
) -> str:
    return (
        _render_header(validated, weekly)
        + '<div class="v2-top-grid">'
        + '<div class="v2-market-pane">'
        + _render_market(validated, weekly)
        + "</div>"
        + _render_analysis(validated, weekly)
        + "</div>"
        + _render_operations(validated, weekly)
        + _render_positions(validated, weekly)
        + _render_events(validated, weekly)
        + _render_data_note(validated, weekly)
    )


def render_unified_dashboard(
    *,
    daily_packet: Any = None,
    weekly_packet: Any = None,
    template: str,
) -> str:
    """Render one daily skeleton with optional, independently dated weekly increments."""

    if daily_packet is None:
        raise DashboardRenderError("a validated daily packet is required; weekly-only pages are not supported")
    _validate_template(template)
    daily = validate_packet(daily_packet)
    weekly = None if weekly_packet is None else validate_weekly_packet(weekly_packet)
    daily_body = _render_daily_content(daily, weekly)
    body = f"""
      <div class="v2-shell">
        <main class="v2-unified-view">{daily_body}</main>
      </div>
    """
    rendered = template.replace(BODY_MARKER, body)
    return rendered.replace(
        "<title>交易中心 · 盘前复盘 V2</title>", "<title>交易中心 · 复盘</title>"
    )


def render_dashboard(packet: Any, template: str) -> str:
    return render_unified_dashboard(daily_packet=packet, template=template)


def render_weekly_dashboard(packet: Any, template: str, *, daily_packet: Any = None) -> str:
    return render_unified_dashboard(
        daily_packet=daily_packet, weekly_packet=packet, template=template
    )


def _git_root_for(path: Path) -> Optional[Path]:
    resolved = path.expanduser().resolve()
    probe = resolved if resolved.is_dir() else resolved.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _require_private_path(path: Path, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise DashboardRenderError(f"{label} must not be a symbolic link")
    resolved = expanded.resolve()
    try:
        resolved.relative_to(PRIVATE_ROOT)
    except ValueError as exc:
        raise DashboardRenderError(f"{label} must be under {PRIVATE_ROOT}") from exc
    if resolved == PRIVATE_ROOT:
        raise DashboardRenderError(f"{label} must be a file below {PRIVATE_ROOT}")
    if _git_root_for(resolved) is not None:
        raise DashboardRenderError(f"{label} must be outside every Git worktree")
    if resolved.exists() and resolved.is_file() and stat.S_IMODE(resolved.stat().st_mode) != 0o600:
        raise DashboardRenderError(f"{label} must have owner-only permissions 0600")
    return resolved


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            Path(temporary_name).unlink()
        except OSError:
            pass
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Legacy private daily JSON packet")
    parser.add_argument("--daily-input", type=Path, help="Private daily JSON packet")
    parser.add_argument("--weekly-input", type=Path, help="Private weekly JSON packet")
    parser.add_argument("--output", required=True, type=Path, help="Private standalone HTML output")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="Bundled V2 HTML template")
    args = parser.parse_args(argv)
    try:
        if args.input is not None and args.daily_input is not None:
            raise DashboardRenderError("--input and --daily-input cannot be combined")
        daily_arg = args.daily_input if args.daily_input is not None else args.input
        if daily_arg is None:
            raise DashboardRenderError("a daily input is required for the single-page dashboard")
        daily_packet = None
        weekly_packet = None
        if daily_arg is not None:
            daily_path = _require_private_path(daily_arg, "daily input")
            daily_packet = json.loads(daily_path.read_text(encoding="utf-8"))
        if args.weekly_input is not None:
            weekly_path = _require_private_path(args.weekly_input, "weekly input")
            weekly_packet = json.loads(weekly_path.read_text(encoding="utf-8"))
        output_path = _require_private_path(args.output, "output")
        template_path = args.template.expanduser().resolve()
        if template_path != DEFAULT_TEMPLATE.resolve():
            raise DashboardRenderError("template must use the bundled V2 template path")
        template = template_path.read_text(encoding="utf-8")
        rendered = render_unified_dashboard(
            daily_packet=daily_packet,
            weekly_packet=weekly_packet,
            template=template,
        )
        _write_private(output_path, rendered)
    except (DashboardRenderError, OSError, UnicodeError, json.JSONDecodeError):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "schema_version": SCHEMA_VERSION,
                    "error_category": "v2_render_or_validation_failure",
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "completed",
                "schema_version": SCHEMA_VERSION,
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
