#!/usr/bin/env python3
"""Project private execution rows into safe trade types and plan alignment.

Raw Longbridge rows are read only from the owner-only runtime. Concrete option
symbols, prices, quantities and upstream identifiers never enter the result.
The command reads confirmed plans from the fixed owner-only state database and
writes a new immutable display snapshot; it does not call Longbridge or write
the database.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

import render_trade_review_dashboard_v2 as dashboard
import trading_review_state as state
from private_runtime_io import (
    PrivateRuntimeError,
    prepare_private_output,
    write_owner_only_text,
)


NY_TZ = ZoneInfo("America/New_York")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LIMIT = 8 * 1024 * 1024
RAW_EXECUTION_KEYS = frozenset(
    {"symbol", "side", "time", "order_id", "price", "quantity", "instrument"}
)
RAW_EXECUTION_REQUIRED_KEYS = frozenset({"symbol", "side", "time"})
PLAN_TOOL_KINDS = frozenset({"stock", "single_stock_leveraged_etf", "leap_call"})
INSTRUMENT_TOOL_KINDS = PLAN_TOOL_KINDS | {"unknown"}
OCC_RE = re.compile(
    r"^(?P<underlying>[A-Z][A-Z0-9.\-]{0,20})"
    r"(?P<expiry>\d{6})(?P<right>[CP])\d+(?:\.US)?$"
)
US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,20}\.US$")


class OperationsRefreshError(RuntimeError):
    """Execution evidence cannot be safely projected."""


def _unique_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperationsRefreshError("duplicate JSON key")
        result[key] = value
    return result


def _parse_json(content: bytes) -> Any:
    if len(content) > LIMIT:
        raise OperationsRefreshError("input exceeds limit")

    def invalid(_: str) -> None:
        raise OperationsRefreshError("non-finite JSON value")

    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=invalid,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise OperationsRefreshError("invalid JSON input") from exc


def _instant(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise OperationsRefreshError("execution time is missing")
    if not dashboard.RFC3339_RE.fullmatch(value):
        raise OperationsRefreshError("execution time must be strict RFC3339")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperationsRefreshError("execution time is invalid") from exc
    if result.tzinfo is None:
        raise OperationsRefreshError("execution time must include a timezone")
    return result


def _expiry_date(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%y%m%d").date()
    except ValueError as exc:
        raise OperationsRefreshError("option expiry encoding is invalid") from exc


def safe_execution(row: Any, review_date: str) -> Dict[str, Any]:
    """Return the minimum non-identifying execution fact for one raw row."""

    if not isinstance(row, dict):
        raise OperationsRefreshError("execution row must be an object")
    if not set(row) <= RAW_EXECUTION_KEYS:
        raise OperationsRefreshError("execution row contains an unsupported field")
    if not RAW_EXECUTION_REQUIRED_KEYS <= set(row):
        raise OperationsRefreshError("execution row is missing required facts")
    instant = _instant(row["time"])
    market_date = instant.astimezone(NY_TZ).date()
    if market_date.isoformat() != review_date:
        raise OperationsRefreshError("raw execution window differs from the review date")
    side_value = row["side"]
    if not isinstance(side_value, str) or side_value.lower() not in {"buy", "sell"}:
        raise OperationsRefreshError("execution side is unsupported")
    side = side_value.lower()
    symbol = row["symbol"]
    if not isinstance(symbol, str):
        raise OperationsRefreshError("execution symbol is missing")
    symbol = symbol.upper()
    option = OCC_RE.fullmatch(symbol)
    if option is not None:
        underlying = option.group("underlying").removesuffix(".US") + ".US"
        expiry = _expiry_date(option.group("expiry"))
        safe = {
            "instant": instant,
            "underlying": underlying,
            "symbol": underlying + ":OPTION",
            "side": side,
            "option_right": "call" if option.group("right") == "C" else "put",
            "same_day_expiry": expiry == market_date,
        }
    elif not US_TICKER_RE.fullmatch(symbol):
        raise OperationsRefreshError("execution symbol cannot be safely projected")
    else:
        safe = {
            "instant": instant,
            "underlying": symbol,
            "symbol": symbol,
            "side": side,
            "option_right": None,
            "same_day_expiry": False,
        }
    if "instrument" in row:
        safe["instrument"] = _safe_instrument(row["instrument"], safe)
    return safe


def _safe_instrument(value: Any, execution: Mapping[str, Any]) -> Dict[str, str]:
    """Admit an optional provider-side tool fact without retaining raw identity."""

    if not isinstance(value, dict) or set(value) != {"tool_kind", "underlying"}:
        raise OperationsRefreshError("instrument evidence has an unsupported structure")
    tool_kind = value["tool_kind"]
    underlying = value["underlying"]
    if not isinstance(tool_kind, str) or tool_kind not in INSTRUMENT_TOOL_KINDS:
        raise OperationsRefreshError("instrument evidence has an unsupported tool")
    if not isinstance(underlying, str) or not US_TICKER_RE.fullmatch(underlying.upper()):
        raise OperationsRefreshError("instrument evidence has an unsupported underlying")
    underlying = underlying.upper()
    actual_symbol = str(execution["symbol"]).upper()
    is_option = execution["option_right"] is not None
    actual_underlying = str(execution["underlying"]).upper()
    if tool_kind == "stock" and (is_option or actual_symbol != underlying):
        raise OperationsRefreshError("stock instrument evidence conflicts with execution")
    if tool_kind == "single_stock_leveraged_etf" and (
        is_option or actual_symbol == underlying
    ):
        raise OperationsRefreshError("leveraged ETF evidence conflicts with execution")
    if tool_kind == "leap_call" and (
        not is_option
        or execution.get("option_right") != "call"
        or actual_underlying != underlying
    ):
        raise OperationsRefreshError("Long Call evidence conflicts with execution")
    if tool_kind == "unknown" and actual_underlying != underlying:
        raise OperationsRefreshError("unknown instrument evidence conflicts with execution")
    return {"tool_kind": tool_kind, "underlying": underlying}


def confirmed_plans(store: state.StateStore) -> list[Dict[str, Any]]:
    """Read fixed plan context without returning plan identities.

    Drafts are admitted only as evidence of an unresolved plan context.  They
    can never become an exact match because ``_plan_timing`` keeps them
    incomplete, but hiding them would incorrectly turn an evidence gap into
    ``outside_plan``.
    """

    rows = store.connection.execute(
        """
        SELECT p.plan_stage, p.underlying, p.direction, p.effective_at,
               p.confirmed_at, p.expires_at, p.plan_status, p.data_status,
               c.tool_kind, c.trade_symbol, c.observation_symbol,
               c.evidence_symbol, c.evidence_period
        FROM plan_versions AS p
        LEFT JOIN plan_execution_contexts AS c
          ON c.plan_id=p.plan_id AND c.plan_version=p.version
        WHERE p.plan_status IN ('draft', 'confirmed', 'expired')
        ORDER BY p.effective_at, p.plan_id, p.version
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _plan_timing(plan: Mapping[str, Any], instant: dt.datetime) -> str:
    """Return active/future/expired/incomplete without treating gaps as facts."""

    plan_status = plan.get("plan_status")
    if plan_status not in {"confirmed", "expired"}:
        return "incomplete"
    if plan.get("data_status") != "complete":
        return "incomplete"
    if plan_status == "expired":
        return "expired"
    values = [plan.get("confirmed_at"), plan.get("effective_at")]
    expires_at = plan.get("expires_at")
    if any(value is None for value in values) or expires_at is None:
        return "incomplete"
    try:
        confirmed_at, effective_at = (_instant(value) for value in values)
        expiry = _instant(expires_at)
    except OperationsRefreshError:
        return "incomplete"
    if confirmed_at > effective_at or (expiry is not None and effective_at >= expiry):
        return "incomplete"
    if max(confirmed_at, effective_at) >= instant:
        return "future"
    if instant >= expiry:
        return "expired"
    return "active"


def _plan_context_complete(plan: Mapping[str, Any]) -> bool:
    tool_kind = plan.get("tool_kind")
    trade_symbol = plan.get("trade_symbol")
    underlying = plan.get("underlying")
    if (
        tool_kind not in PLAN_TOOL_KINDS
        or not isinstance(trade_symbol, str)
        or not isinstance(underlying, str)
        or not US_TICKER_RE.fullmatch(trade_symbol.upper().removesuffix(":OPTION"))
        or not US_TICKER_RE.fullmatch(underlying.upper())
    ):
        return False
    trade_symbol = trade_symbol.upper()
    underlying = underlying.upper()
    is_option = trade_symbol.endswith(":OPTION")
    if tool_kind == "stock":
        return not is_option and trade_symbol == underlying
    if tool_kind == "single_stock_leveraged_etf":
        return not is_option and trade_symbol != underlying
    return is_option and trade_symbol.removesuffix(":OPTION") == underlying


def _related_plan(plan: Mapping[str, Any], execution: Mapping[str, Any]) -> bool:
    """Keep underlying context for notes while matching only exact instruments."""

    if plan.get("underlying") == execution.get("underlying"):
        return True
    # A leveraged ETF's observation underlying may differ from its traded
    # symbol.  Without a provider-side underlying fact, an exact, unique ETF
    # plan is the only admitted instrument evidence; no other tool uses this
    # fallback.
    return (
        plan.get("tool_kind") == "single_stock_leveraged_etf"
        and plan.get("trade_symbol") == execution.get("symbol")
        and execution.get("option_right") is None
    )


def _exact_tool_match(plan: Mapping[str, Any], execution: Mapping[str, Any]) -> bool:
    if not _plan_context_complete(plan) or plan.get("trade_symbol") != execution.get("symbol"):
        return False
    if execution.get("same_day_expiry"):
        return False
    explicit = execution.get("instrument")
    if isinstance(explicit, Mapping):
        if explicit.get("tool_kind") == "leap_call" and execution.get("option_right") != "call":
            return False
        return (
            plan.get("tool_kind") == explicit.get("tool_kind")
            and plan.get("underlying") == explicit.get("underlying")
        )
    if execution.get("option_right") is not None:
        return plan.get("tool_kind") == "leap_call" and execution.get("option_right") == "call"
    if plan.get("tool_kind") == "stock":
        return plan.get("underlying") == execution.get("underlying")
    return plan.get("tool_kind") == "single_stock_leveraged_etf"


def _matching_plan(
    execution: Mapping[str, Any], plans: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    related = [plan for plan in plans if _related_plan(plan, execution)]
    active = []
    exact_active = []
    timing_gaps = []
    incomplete_context = []
    for plan in related:
        timing = _plan_timing(plan, execution["instant"])
        if timing == "active":
            active.append(plan)
            if _exact_tool_match(plan, execution):
                exact_active.append(plan)
            elif not _plan_context_complete(plan):
                incomplete_context.append(plan)
        elif timing in {"future", "expired", "incomplete"}:
            timing_gaps.append((plan, timing))
            if not _plan_context_complete(plan):
                incomplete_context.append(plan)
    return {
        "related": related,
        "active": active,
        "exact_active": exact_active,
        "timing_gaps": timing_gaps,
        "incomplete_context": incomplete_context,
        "ambiguous": len(exact_active) > 1,
    }


def _trade_type(execution: Mapping[str, Any], match: Mapping[str, Any]) -> str:
    is_option = execution.get("option_right") is not None
    if is_option and execution.get("same_day_expiry"):
        # The expiry/date test is mechanical and has precedence over any
        # sanitized plan context.  A same-day option can never be Long Call.
        return "zero_dte_option"
    explicit = execution.get("instrument")
    exact = match.get("exact_active", [])
    if isinstance(explicit, Mapping):
        tool = explicit.get("tool_kind")
        if tool == "unknown":
            return "unknown"
        if is_option:
            # A provider-side tool fact alone does not establish a Long Call;
            # the exact, active confirmed plan must agree as well.  Without
            # that conjunction, keep the option generic rather than turning
            # an otherwise safe tool label into a plan assertion.
            if (
                tool == "leap_call"
                and execution.get("option_right") == "call"
                and len(exact) == 1
                and exact[0].get("direction") == "long"
            ):
                return "long_call"
            return "other_option"
        return "single_stock_leveraged_etf" if tool == "single_stock_leveraged_etf" else "stock"
    if len(exact) == 1:
        tool = exact[0].get("tool_kind")
        if (
            is_option
            and tool == "leap_call"
            and exact[0].get("direction") == "long"
            and execution.get("option_right") == "call"
        ):
            return "long_call"
        if not is_option and tool == "single_stock_leveraged_etf":
            return "single_stock_leveraged_etf"
    if is_option:
        return "other_option"
    # A bare `.US` ticker does not distinguish an ordinary stock from a
    # single-stock leveraged ETF.  Keep the instrument unknown until the
    # execution or an exact confirmed plan supplies that evidence.
    return "unknown"


def _plan_status(
    execution: Mapping[str, Any], match: Mapping[str, Any]
) -> tuple[str, str, Optional[Mapping[str, Any]]]:
    explicit = execution.get("instrument")
    if isinstance(explicit, Mapping) and explicit.get("tool_kind") == "unknown":
        return (
            "unknown",
            "工具证据不完整，暂不能确认实际交易类型或计划关系。",
            None,
        )
    exact = match.get("exact_active", [])
    if len(exact) > 1:
        return (
            "unknown",
            "存在多个相同工具的事前确认计划，暂不能唯一对齐。",
            None,
        )
    if len(exact) == 1:
        plan = exact[0]
        direction = plan.get("direction")
        stage = plan.get("plan_stage")
        if direction == "long" and (
            (stage == "pre_entry" and execution.get("side") == "buy")
            or (stage == "position_management" and execution.get("side") in {"buy", "sell"})
        ):
            if stage == "position_management":
                return (
                    "confirmed_plan",
                    "匹配到成交前已生效的确认持仓管理计划；具体管理条件仍需复核。",
                    plan,
                )
            return (
                "confirmed_plan",
                "匹配到成交前已生效的确认买入计划；具体触发条件仍需复核。",
                plan,
            )
        return (
            "mismatch",
            "工具与事前计划一致，但交易方向或管理阶段仍需核对。",
            plan,
        )
    if execution.get("option_right") is None and not isinstance(explicit, Mapping):
        return (
            "unknown",
            "缺少明确的实际交易工具证据，暂不能区分正股与单股杠杆 ETF。",
            None,
        )
    if match.get("timing_gaps") or match.get("incomplete_context"):
        return (
            "unknown",
            "存在相关计划，但确认/生效时间或工具上下文不完整，暂不能判断。",
            None,
        )
    if match.get("active"):
        # An active, complete plan exists for the same underlying but does not
        # describe this actual symbol/tool/right.  This is an explicit
        # mismatch, not missing evidence.
        return (
            "mismatch",
            "同一标的存在事前计划，但实际交易工具或期权方向不同，因此不计入计划内。",
            None,
        )
    if match.get("related"):
        return (
            "mismatch",
            "未匹配到成交前已生效、且交易工具一致的确认计划。",
            None,
        )
    return (
        "outside_plan",
        "未找到成交前已确认且工具一致的事前计划。",
        None,
    )


def classify_execution(
    execution: Mapping[str, Any], plans: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Classify without inferring a generic option to be a Long Call."""

    match = _matching_plan(execution, plans)
    trade_type = _trade_type(execution, match)
    plan_status, note, _ = _plan_status(execution, match)
    return {
        "symbol": execution["symbol"],
        "display_name": execution["symbol"].removesuffix(":OPTION").removesuffix(".US"),
        "side": execution["side"],
        "trade_type": trade_type,
        "option_right": execution["option_right"],
        "plan_status": plan_status,
        "plan_status_note": note,
        "execution_count": 1,
        "data_status": "complete",
    }


def aggregate_rows(rows: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    grouped: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        key = tuple(
            row[field] for field in (
                "symbol", "display_name", "side", "trade_type", "option_right",
                "plan_status", "plan_status_note", "data_status",
            )
        )
        if key not in grouped:
            grouped[key] = row
        else:
            grouped[key]["execution_count"] += row["execution_count"]
    return sorted(
        grouped.values(),
        key=lambda row: (
            row["display_name"], row["side"], row["trade_type"],
            row["option_right"] or "",
        ),
    )


def _next_generated_at(existing: str, now: Optional[dt.datetime]) -> str:
    current = (now or dt.datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ).replace(microsecond=0)
    previous = _instant(existing)
    if current <= previous:
        current = previous.astimezone(SHANGHAI_TZ) + dt.timedelta(seconds=1)
    return current.isoformat()


def refresh_snapshot(
    snapshot: Any,
    raw_executions: Any,
    plans: Iterable[Mapping[str, Any]],
    *,
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    admitted = dashboard.validate_display_snapshot(snapshot)
    if not isinstance(raw_executions, list):
        raise OperationsRefreshError("raw executions must be a list")
    view = copy.deepcopy(admitted)
    review_date = view["daily"]["meta"]["review_date"]
    safe_rows = [safe_execution(row, review_date) for row in raw_executions]
    plan_rows = list(plans)
    classified = aggregate_rows(classify_execution(row, plan_rows) for row in safe_rows)
    operations = view["daily"]["operations"]
    expected = operations["executions"]
    if expected["data_status"] not in {"complete", "empty"} or expected["count"] is None:
        raise OperationsRefreshError("execution total is not complete")
    if expected["count"] != len(raw_executions):
        raise OperationsRefreshError("raw execution rows do not match the verified total")
    operations.update(
        status="empty" if expected["data_status"] == "empty" or expected["count"] == 0 else "complete",
        market_scope="US",
        title="上一交易日成交",
        items=classified,
        note="仅展示已核验成交的安全聚合、交易类型和事前计划关系。",
    )
    view["daily"]["meta"]["generated_at"] = _next_generated_at(
        view["daily"]["meta"]["generated_at"], now
    )
    return dashboard.validate_display_snapshot(view)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-input", required=True, type=Path)
    parser.add_argument("--raw-executions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        display_path = prepare_private_output(args.display_input)
        raw_path = prepare_private_output(args.raw_executions)
        output_path = prepare_private_output(args.output)
        if output_path in {display_path, raw_path} or output_path.exists():
            raise OperationsRefreshError("output must be a new private artifact")
        snapshot = _parse_json(display_path.read_bytes())
        raw = _parse_json(raw_path.read_bytes())
        with state.read_state_store() as store:
            plans = confirmed_plans(store)
        refreshed = refresh_snapshot(snapshot, raw, plans)
        write_owner_only_text(
            output_path,
            json.dumps(
                refreshed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n",
        )
        print(json.dumps({
            "status": refreshed["daily"]["operations"]["status"],
            "review_date": refreshed["daily"]["meta"]["review_date"],
            "execution_rows": len(raw),
            "display_rows": len(refreshed["daily"]["operations"]["items"]),
            "output": str(output_path),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (
        OSError, PrivateRuntimeError, ValueError, TypeError, KeyError,
        OperationsRefreshError, state.StateStoreError,
    ):
        sys.stderr.write("daily_operations_refresh_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
