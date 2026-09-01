#!/usr/bin/env python3
"""Project sanitized weekly private facts into the plan-discipline SQLite-v3 contract.

The projector never calls a broker. It verifies the sanitized packet against
the latest persisted daily partition hashes, removes account and P&L facts from
the review projection, collapses option facts to underlying level, and emits
one owner-only weekly-state JSON file for ``run_incremental_review.py ingest-weekly``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import run_incremental_review as runner
import trading_review_state as state


PRIVATE_FACTS_SCHEMA = "trading-review-weekly-private-facts.v2"
STATE_SCHEMA = "trading-review-weekly-state.v2"
STATE_SCHEMA_V3 = "trading-review-weekly-state.v3"
SUBJECT_SEPARATOR = "｜"
SUBJECT_SECTIONS = frozenset(
    {
        "市场雷达",
        "周度判断",
        "本周操作",
        "持仓计划",
        "计划复核",
        "下周草案",
        "下周事件",
        "数据说明",
    }
)


class WeeklyProjectionError(state.StateContractError):
    """The private weekly packet cannot be safely projected."""


def _object(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise WeeklyProjectionError(f"{path} must be an object")
    return value


def _array(value: Any, path: str) -> List[Any]:
    if not isinstance(value, list):
        raise WeeklyProjectionError(f"{path} must be an array")
    return value


def _strict(value: Any, allowed: Iterable[str], required: Iterable[str], path: str) -> Dict[str, Any]:
    item = _object(value, path)
    unknown = sorted(set(item) - set(allowed))
    missing = sorted(set(required) - set(item))
    if unknown:
        raise WeeklyProjectionError(f"{path} has unsupported fields: {', '.join(unknown)}")
    if missing:
        raise WeeklyProjectionError(f"{path} is missing fields: {', '.join(missing)}")
    return item


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeeklyProjectionError(f"{path} must be a non-empty string")
    text = value.strip()
    if state.SENSITIVE_VALUE_RE.search(text):
        raise WeeklyProjectionError(f"{path} contains a forbidden sensitive value")
    if state.instruments.contains_contract_identity(text):
        raise WeeklyProjectionError(f"{path} contains option contract identity")
    return text


def _date(value: Any, path: str) -> str:
    text = _text(value, path)
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise WeeklyProjectionError(f"{path} must be a real YYYY-MM-DD date") from exc


def _timestamp(value: Any, path: str) -> str:
    text = _text(value, path)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WeeklyProjectionError(f"{path} must be RFC3339") from exc
    if "T" not in text or parsed.tzinfo is None:
        raise WeeklyProjectionError(f"{path} must be RFC3339 with timezone")
    return text


def _decimal(value: Any, path: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise WeeklyProjectionError(f"{path} must be a decimal")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise WeeklyProjectionError(f"{path} must be a decimal") from exc
    if not number.is_finite():
        raise WeeklyProjectionError(f"{path} must be finite")
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WeeklyProjectionError(f"{path} must be a non-negative integer")
    return value


def _status(value: Any, path: str) -> str:
    text = _text(value, path)
    if text not in state.DATA_STATUSES:
        raise WeeklyProjectionError(f"{path} has unsupported status")
    return text


def _subject(section: str, label: str) -> str:
    if section not in SUBJECT_SECTIONS:
        raise WeeklyProjectionError("unsupported weekly review section")
    return f"{section}{SUBJECT_SEPARATOR}{label}"


def split_subject(value: str) -> Tuple[str, str]:
    if SUBJECT_SEPARATOR not in value:
        raise WeeklyProjectionError("weekly review item subject has no section separator")
    section, label = value.split(SUBJECT_SEPARATOR, 1)
    if section not in SUBJECT_SECTIONS or not label.strip():
        raise WeeklyProjectionError("weekly review item subject section is unsupported")
    return section, label.strip()


def _item(
    *,
    section: str,
    label: str,
    item_kind: str,
    summary: str,
    boundary: str,
    evidence_kind: str,
    data_status: str,
) -> Dict[str, Any]:
    item = {
        "item_kind": item_kind,
        "subject": _subject(section, _text(label, "review_item.label")),
        "summary": _text(summary, "review_item.summary"),
        "evidence_boundary": _text(boundary, "review_item.boundary"),
        "evidence_kind": evidence_kind,
        "data_status": data_status,
    }
    return state._normalize_weekly_review_item(item, "$projected.review_item")


def _underlying(symbol: Any, explicit_underlying: Any, path: str) -> str:
    underlying = _text(explicit_underlying, f"{path}.underlying")
    symbol_text = _text(symbol, f"{path}.symbol")
    try:
        state.instruments.safe_symbol(symbol_text)
        state.instruments.safe_symbol(underlying, option=False)
    except state.instruments.InstrumentContractError as exc:
        raise WeeklyProjectionError(f"{path} contains a contract identity") from exc
    if ":OPTION" in symbol_text and symbol_text != f"{underlying}:OPTION":
        raise WeeklyProjectionError(f"{path}.symbol is not an underlying-only option projection")
    if ":OPTION" not in symbol_text and symbol_text != underlying:
        raise WeeklyProjectionError(f"{path}.symbol and underlying do not match")
    return underlying


def _latest_dependency(
    store: Optional[state.StateStore],
    *,
    dataset: str,
    period_start: str,
    period_end: str,
    contract_version: str,
    projected_payload: Any,
    expected_status: str,
) -> Dict[str, Any]:
    normalized_payload = state.validate_partition_payload(
        dataset, projected_payload, expected_status
    )
    projected_hash = state.content_hash(normalized_payload)
    if store is None:
        return {
            "dataset": dataset,
            "period_start": period_start,
            "period_end": period_end,
            "contract_version": contract_version,
            "partition_revision": 1,
            "payload_hash": projected_hash,
        }
    identity = store.latest_partition_identity(
        dataset, period_start, period_end, contract_version
    )
    if identity is None:
        raise WeeklyProjectionError(
            f"missing persisted dependency: {dataset} {period_start}..{period_end}"
        )
    if projected_hash != identity["payload_hash"]:
        raise WeeklyProjectionError(
            f"private facts do not match persisted dependency: {dataset} {period_start}"
        )
    if identity["status"] != expected_status:
        raise WeeklyProjectionError(
            f"private facts status does not match persisted dependency: {dataset} {period_start}"
        )
    return {key: identity[key] for key in (
        "dataset",
        "period_start",
        "period_end",
        "contract_version",
        "partition_revision",
        "payload_hash",
    )}




def project_weekly_state(
    facts: Any, store: Optional[state.StateStore]
) -> Dict[str, Any]:
    top_keys = {
        "schema_version",
        "run_id",
        "generated_at",
        "authorization",
        "source",
        "period",
        "account_current",
        "positions_current",
        "trades",
        "profit_analysis",
        "profit_analysis_by_market",
        "cash_flow",
        "market",
        "events_next_week",
        "plan",
        "known_gaps",
        "overall_data_status",
    }
    schema = _object(facts, "$").get("schema_version")
    if schema not in {PRIVATE_FACTS_SCHEMA, "trading-review-weekly-private-facts.v1"}:
        raise WeeklyProjectionError("unsupported weekly private facts schema")
    if schema == PRIVATE_FACTS_SCHEMA:
        top_keys -= {"account_current", "profit_analysis", "profit_analysis_by_market", "cash_flow"}
    root = _strict(facts, top_keys, top_keys, "$")
    generated_at = _timestamp(root["generated_at"], "$.generated_at")

    source = _strict(
        root["source"],
        {"provider", "cli_version", "contract_version"},
        {"provider", "cli_version", "contract_version"},
        "$.source",
    )
    if _text(source["provider"], "$.source.provider") != "Longbridge":
        raise WeeklyProjectionError("weekly projection is Longbridge-only")
    source_contract = _text(source["contract_version"], "$.source.contract_version")
    period = _strict(
        root["period"],
        {"start_date", "end_date", "timezone", "utc_start", "utc_end", "expected_trade_dates"},
        {"start_date", "end_date", "timezone", "utc_start", "utc_end", "expected_trade_dates"},
        "$.period",
    )
    period_start = _date(period["start_date"], "$.period.start_date")
    period_end = _date(period["end_date"], "$.period.end_date")
    if period_start > period_end:
        raise WeeklyProjectionError("weekly period is reversed")
    if period["timezone"] != "America/New_York":
        raise WeeklyProjectionError("weekly period must use America/New_York")
    utc_start = _timestamp(period["utc_start"], "$.period.utc_start")
    utc_end = _timestamp(period["utc_end"], "$.period.utc_end")
    expected_start = dt.datetime.combine(dt.date.fromisoformat(period_start), dt.time.min, tzinfo=ZoneInfo("America/New_York"))
    expected_end = dt.datetime.combine(dt.date.fromisoformat(period_end) + dt.timedelta(days=1), dt.time.min, tzinfo=ZoneInfo("America/New_York"))
    if (dt.datetime.fromisoformat(utc_start.replace("Z", "+00:00")) != expected_start
            or dt.datetime.fromisoformat(utc_end.replace("Z", "+00:00")) != expected_end):
        raise WeeklyProjectionError("weekly UTC window must match the full New York half-open period")
    expected_dates = [_date(value, "$.period.expected_trade_dates") for value in _array(
        period["expected_trade_dates"], "$.period.expected_trade_dates"
    )]
    if expected_dates != sorted(set(expected_dates)) or not expected_dates:
        raise WeeklyProjectionError("expected trade dates must be non-empty, unique and sorted")
    if any(day < period_start or day > period_end for day in expected_dates):
        raise WeeklyProjectionError("expected trade dates must stay inside the weekly period")

    trades = _object(root["trades"], "$.trades")
    daily_rows = _array(trades.get("daily"), "$.trades.daily")
    daily_by_date: Dict[str, Mapping[str, Any]] = {}
    dependencies: List[Dict[str, Any]] = []
    trade_contract = f"{source_contract}:trades"
    operation_items: List[Dict[str, Any]] = []
    for index, raw in enumerate(daily_rows):
        path = f"$.trades.daily[{index}]"
        day = _object(raw, path)
        market_date = _date(day.get("market_date"), f"{path}.market_date")
        if market_date in daily_by_date:
            raise WeeklyProjectionError("weekly trades contain a duplicate market date")
        daily_by_date[market_date] = day
        day_status = _status(day.get("status"), f"{path}.status")
        rows = _array(day.get("rows"), f"{path}.rows")
        dependencies.append(
            _latest_dependency(
                store,
                dataset="trades",
                period_start=market_date,
                period_end=market_date,
                contract_version=trade_contract,
                projected_payload=rows,
                expected_status=day_status,
            )
        )
        underlyings = sorted({
            (state.instruments.normalize_instrument(row["instrument"], row.get("symbol"))["underlying"]
             if isinstance(row, dict) and "instrument" in row else
             _underlying(row.get("symbol"), row.get("symbol", "").replace(":OPTION", ""), f"{path}.rows[{row_index}]"))
            for row_index, row in enumerate(rows) if isinstance(row, dict)
        })
        order_count = _integer(day.get("order_count"), f"{path}.order_count")
        execution_count = _integer(day.get("execution_count"), f"{path}.execution_count")
        duplicate_count = _integer(
            day.get("duplicate_execution_row_count"), f"{path}.duplicate_execution_row_count"
        )
        summary = (
            f"订单 {order_count} 笔、成交 {execution_count} 笔；涉及标的 "
            f"{('、'.join(underlyings) if underlyings else '无')}。"
        )
        if duplicate_count:
            summary += f" 有 {duplicate_count} 条重复成交行待消歧，不能上调为完整状态。"
        operation_items.append(
            _item(
                section="本周操作",
                label=market_date,
                item_kind="plan_actual",
                summary=summary,
                boundary="仅展示按交易日和 underlying 聚合的订单/成交事实；不含合约身份、数量或交易标识。",
                evidence_kind="fact",
                data_status="complete" if day_status == "empty" else day_status,
            )
        )
    if set(daily_by_date) != set(expected_dates):
        raise WeeklyProjectionError("weekly trade days do not cover every expected date")

    positions = _object(root["positions_current"], "$.positions_current")
    positions_status = _status(positions.get("status"), "$.positions_current.status")
    position_rows = _array(positions.get("rows"), "$.positions_current.rows")
    state.validate_partition_payload(
        "positions_snapshot", position_rows, positions_status
    )
    # Positions are an explicitly timestamped snapshot stored as a weekly
    # summary item. Only historical trade partitions are freshness
    # dependencies; a later current-position refresh must not rewrite W35.
    current_underlyings = sorted({
        _underlying(row.get("symbol"), row.get("underlying"), f"$.positions_current.rows[{index}]")
        for index, row in enumerate(position_rows)
        if isinstance(row, dict)
    })
    position_items = [
        _item(
            section="持仓计划",
            label="当前持仓",
            item_kind="plan_actual",
            summary=f"当前快照覆盖 {len(position_rows)} 条持仓记录，underlying 为 {'、'.join(current_underlyings)}。",
            boundary="当前读取时快照；期权仅保留 underlying，不能反推周初持仓或周内仓位路径。",
            evidence_kind="fact",
            data_status=positions_status,
        )
    ] if position_rows else []

    market = _object(root["market"], "$.market")
    market_status = _status(market.get("status"), "$.market.status")
    review_items: List[Dict[str, Any]] = []
    temperatures = _object(market.get("market_temperature"), "$.market.market_temperature")
    temperature_rows = _array(temperatures.get("rows"), "$.market.market_temperature.rows")
    if temperature_rows:
        first = _object(temperature_rows[0], "$.market.market_temperature.rows[0]")
        last = _object(temperature_rows[-1], "$.market.market_temperature.rows[-1]")
        review_items.append(
            _item(
                section="市场雷达",
                label="风险温度",
                item_kind="risk",
                summary=(
                    f"温度 {_decimal(first.get('temperature'), 'temperature.start')}→"
                    f"{_decimal(last.get('temperature'), 'temperature.end')}；情绪 "
                    f"{_decimal(first.get('sentiment'), 'sentiment.start')}→"
                    f"{_decimal(last.get('sentiment'), 'sentiment.end')}；估值 "
                    f"{_decimal(first.get('valuation'), 'valuation.start')}→"
                    f"{_decimal(last.get('valuation'), 'valuation.end')}。"
                ),
                boundary="仅为该周固定窗口的只读市场温度序列。",
                evidence_kind="fact",
                data_status=_status(temperatures.get("status"), "$.market.market_temperature.status"),
            )
        )
    quotes = _object(market.get("quotes"), "$.market.quotes")
    quote_status = _status(quotes.get("status"), "$.market.quotes.status")
    for index, raw in enumerate(_array(quotes.get("rows"), "$.market.quotes.rows")):
        path = f"$.market.quotes.rows[{index}]"
        quote = _object(raw, path)
        symbol = _text(quote.get("symbol"), f"{path}.symbol")
        review_items.append(
            _item(
                section="市场雷达",
                label=symbol,
                item_kind="risk",
                summary=(
                    f"观察值 {_decimal(quote.get('last'), f'{path}.last')}；相对前收 "
                    f"{_decimal(quote.get('change_pct'), f'{path}.change_pct')}%。"
                ),
                boundary="主行情源时间缺失；该观察值只能按部分可用处理，不能伪装为实时行情。",
                evidence_kind="fact",
                data_status=quote_status,
            )
        )
    review_items.append(
        _item(
            section="市场雷达",
            label="QQQ 标的级字段",
            item_kind="risk",
            summary="券商返回标的级 capital in/out 字段，但本周判断不把它称为全市场资金流或因果方向。",
            boundary=_text(
                _object(market.get("qqq_capital_snapshot"), "$.market.qqq_capital_snapshot").get("boundary"),
                "$.market.qqq_capital_snapshot.boundary",
            ),
            evidence_kind="interpretation",
            data_status=_status(
                _object(market.get("qqq_capital_snapshot"), "$.market.qqq_capital_snapshot").get("status"),
                "$.market.qqq_capital_snapshot.status",
            ),
        )
    )

    review_items.extend(operation_items)
    review_items.extend(position_items)

    plan = _object(root["plan"], "$.plan")
    plan_status = _status(plan.get("status"), "$.plan.status")
    plan_reason = _text(plan.get("reason"), "$.plan.reason")
    plan_boundary = _text(plan.get("boundary"), "$.plan.boundary")
    plan_hash_value = plan.get("plan_hash")
    if plan_status == "blocked":
        plan_hash: Optional[str] = None
    else:
        if plan_hash_value is None:
            raise WeeklyProjectionError("non-blocked plan requires plan_hash")
        plan_hash = state._sha256(plan_hash_value, "$.plan.plan_hash")

    raw_assessments = plan.get("episode_assessments", [])
    if not isinstance(raw_assessments, list):
        raise WeeklyProjectionError("$.plan.episode_assessments must be an array")
    execution_basis = plan.get("execution_basis")
    if execution_basis is not None and execution_basis != "instrument-episode.v1":
        raise WeeklyProjectionError("$.plan.execution_basis is unsupported")
    instrument_level = execution_basis == "instrument-episode.v1"
    contextual_rows = [isinstance(row, dict) and "trade_symbol" in row for row in raw_assessments]
    if instrument_level and not all(contextual_rows):
        raise WeeklyProjectionError("instrument-level assessments require trade_symbol on every row")
    if not instrument_level and any(contextual_rows):
        raise WeeklyProjectionError("instrument-level assessments require an explicit execution_basis")
    episode_assessments = [
        (state._normalize_instrument_episode if instrument_level else state._normalize_episode_assessment)(
            row, f"$.plan.episode_assessments[{index}]"
        ) for index, row in enumerate(raw_assessments)
    ]
    metrics_status_value = plan.get("metrics_status")
    if metrics_status_value is None:
        metrics_status = "blocked" if plan_status == "blocked" or not episode_assessments else plan_status
    else:
        metrics_status = _status(metrics_status_value, "$.plan.metrics_status")
    metrics_gap_value = plan.get("metrics_gap")
    if metrics_status in {"partial", "stale", "blocked"}:
        metrics_gap = _text(
            metrics_gap_value if metrics_gap_value is not None else plan_reason,
            "$.plan.metrics_gap",
        )
    else:
        if metrics_gap_value is not None:
            raise WeeklyProjectionError("successful execution metrics cannot include metrics_gap")
        metrics_gap = None
    if plan_status == "blocked" and episode_assessments:
        raise WeeklyProjectionError("blocked plan cannot contain factual episode assessments")
    for section, label, kind in (
        ("持仓计划", "计划覆盖", "gap"),
        ("计划复核", "计划与实际", "plan_actual"),
        ("计划复核", "纪律", "discipline"),
        ("下周草案", "待确认", "gap"),
    ):
        review_items.append(
            _item(
                section=section,
                label=label,
                item_kind=kind,
                summary=plan_reason,
                boundary=plan_boundary,
                evidence_kind="gap" if plan_status == "blocked" else "draft",
                data_status=plan_status,
            )
        )

    events = _object(root["events_next_week"], "$.events_next_week")
    events_status = _status(events.get("status"), "$.events_next_week.status")
    macro = _object(events.get("macro_star3"), "$.events_next_week.macro_star3")
    for index, raw in enumerate(_array(macro.get("rows"), "$.events_next_week.macro_star3.rows")):
        path = f"$.events_next_week.macro_star3.rows[{index}]"
        event = _object(raw, path)
        shanghai_at = _timestamp(event.get("shanghai_at"), f"{path}.shanghai_at")
        review_items.append(
            _item(
                section="下周事件",
                label=shanghai_at,
                item_kind="risk",
                summary=(
                    f"{_text(event.get('title'), f'{path}.title')}；影响通道："
                    f"{_text(event.get('impact_channel'), f'{path}.impact_channel')}。"
                ),
                boundary="按美东日期过滤的美国三星宏观事件；事件是风险窗口，不代表价格方向。",
                evidence_kind="fact",
                data_status=_status(event.get("data_status"), f"{path}.data_status"),
            )
        )
    earnings = _object(events.get("earnings"), "$.events_next_week.earnings")
    earnings_status = _status(earnings.get("status"), "$.events_next_week.earnings.status")
    if earnings_status != "complete":
        review_items.append(
            _item(
                section="下周事件",
                label="持仓财报",
                item_kind="gap",
                summary=_text(earnings.get("gap"), "$.events_next_week.earnings.gap"),
                boundary="持仓筛选返回的事件未覆盖目标周，保持部分可用。",
                evidence_kind="gap",
                data_status=earnings_status,
            )
        )

    known_gaps = _array(root["known_gaps"], "$.known_gaps")
    for index, gap in enumerate(known_gaps, start=1):
        gap_text = _text(gap, f"$.known_gaps[{index - 1}]")
        review_items.append(
            _item(
                section="数据说明",
                label=f"缺口 {index}",
                item_kind="gap",
                summary=gap_text,
                boundary="缺口保持显式，不以推断或空白替代。",
                evidence_kind="gap",
                data_status="blocked" if "计划" in gap_text else "partial",
            )
        )

    trade_status = _status(trades.get("status"), "$.trades.status")
    modules = [
        {
            "name": "trades",
            "status": trade_status,
            "requested_start": utc_start,
            "requested_end": utc_end,
            "returned_start": utc_start,
            "returned_end": utc_end,
            "error_category": None if trade_status in {"complete", "empty"} else "duplicate_execution_ambiguity",
        },
        {
            "name": "performance",
            "status": "empty",
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": None,
        },
        {
            "name": "attribution",
            "status": "empty",
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": None,
        },
        {
            "name": "cash_flow",
            "status": "empty",
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": None,
        },
        {
            "name": "positions",
            "status": positions_status,
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": None if positions_status in {"complete", "empty"} else "current_snapshot_gap",
        },
        {
            "name": "market",
            "status": market_status,
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": None if market_status in {"complete", "empty"} else "quote_source_timestamp_missing",
        },
        {
            "name": "events",
            "status": events_status,
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": None if events_status in {"complete", "empty"} else "earnings_window_gap",
        },
        {
            "name": "plan",
            "status": metrics_status,
            "requested_start": None,
            "requested_end": None,
            "returned_start": None,
            "returned_end": None,
            "error_category": (
                None
                if metrics_status in {"complete", "empty"}
                else "confirmed_plan_or_execution_evidence_missing"
            ),
        },
    ]
    overall_status = _status(root["overall_data_status"], "$.overall_data_status")
    if overall_status not in {"complete", "partial", "blocked"}:
        raise WeeklyProjectionError("weekly overall status must be complete, partial, or blocked")
    bundle = {
        "schema_version": STATE_SCHEMA_V3 if instrument_level else STATE_SCHEMA,
        "run_id": _text(root["run_id"], "$.run_id"),
        "review_key": f"weekly:{period_start}:{period_end}",
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": generated_at,
        "source_contract_version": source_contract,
        "data_status": overall_status,
        "plan_hash": plan_hash,
        "dependencies": dependencies,
        "modules": modules,
        "performance": None,
        "attributions": [],
        "cash_flow_aggregates": [],
        "review_items": review_items,
        "episode_assessments": episode_assessments,
        "execution_metrics": {
            "data_status": metrics_status,
            "gap": metrics_gap,
        },
    }
    # Validate the projection, then emit only the accepted input fields. Hashes
    # are recomputed by the store at ingestion and are never trusted from JSON.
    validated = state.normalize_weekly_review_bundle(bundle)
    validated.pop("facts_hash")
    validated.pop("dependency_hash")
    validated["execution_metrics"] = {
        "data_status": metrics_status,
        "gap": metrics_gap,
    }
    return validated


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        input_path = runner._private_path(args.input, "input", require_existing=True)
        output_path = runner._private_path(args.output, "output")
        facts = json.loads(input_path.read_text(encoding="utf-8"))
        # Run the entire source/schema/privacy projection before the database is
        # opened so malformed input cannot trigger migration or any other state
        # transition. Synthetic dependency identities are discarded.
        project_weekly_state(facts, None)
        with state.open_state_store(args.state_db) as store:
            bundle = project_weekly_state(facts, store)
        runner._write_private_json(output_path, bundle)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        state.StateStoreError,
        runner.RunnerContractError,
        WeeklyProjectionError,
    ):
        print(json.dumps({"status": "blocked", "error_category": "weekly_projection_failure"}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", "schema_version": bundle["schema_version"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
