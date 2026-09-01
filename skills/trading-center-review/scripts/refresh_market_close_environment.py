#!/usr/bin/env python3
"""Refresh the fixed market radar and one LongbridgeAI close judgement.

The command reads six public market proxies, then invokes the approved
LongbridgeAI public analysis capability once when all six closes are complete.
It never calls account, position, order, execution, statement, or trading
capabilities. The admitted display input and sanitized output stay in the
owner-only private runtime.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

import render_trade_review_dashboard_v2 as dashboard
from private_runtime_io import prepare_private_output, write_owner_only_text


SYMBOLS = ("SPY.US", "QQQ.US", "IEF.US", "GLD.US", "USO.US", "IBIT.US")
NY_TZ = ZoneInfo("America/New_York")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LOOKBACK_DAYS = 14
LIMIT = 8 * 1024 * 1024
AGENT_TIMEOUT_SECONDS = 180
AGENT_UID = "chatbot"


class MarketCloseRefreshError(RuntimeError):
    """The fixed completed-close refresh could not be admitted."""


def _unique_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MarketCloseRefreshError("duplicate JSON key")
        result[key] = value
    return result


def _parse_json(text: str) -> Any:
    def invalid(_: str) -> None:
        raise MarketCloseRefreshError("non-finite JSON value")

    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=invalid)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MarketCloseRefreshError("invalid JSON input") from exc


def _records(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "list", "candles", "klines"):
            if key in value:
                return _records(value[key])
    return []


def _timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise MarketCloseRefreshError("daily bar time is missing")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketCloseRefreshError("daily bar time is invalid") from exc
    if parsed.tzinfo is None:
        raise MarketCloseRefreshError("daily bar time must include a timezone")
    return parsed


def _positive_decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketCloseRefreshError("daily close is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MarketCloseRefreshError("daily close must be positive")
    return parsed


def completed_close_fact(
    response: Any,
    symbol: str,
    review_date: str,
) -> Optional[Dict[str, Any]]:
    """Return the review-date close and prior completed close, or None."""

    by_date: Dict[str, tuple[dt.datetime, Decimal]] = {}
    for row in _records(response):
        if "time" not in row or "close" not in row:
            continue
        instant = _timestamp(row["time"])
        market_date = instant.astimezone(NY_TZ).date().isoformat()
        if market_date > review_date:
            continue
        close = _positive_decimal(row["close"])
        if market_date in by_date:
            raise MarketCloseRefreshError("duplicate daily bar for one market date")
        by_date[market_date] = (instant, close)
    if review_date not in by_date:
        return None
    prior_dates = sorted(value for value in by_date if value < review_date)
    if not prior_dates:
        return None
    instant, close = by_date[review_date]
    previous_close = by_date[prior_dates[-1]][1]
    change_pct = (close / previous_close - Decimal("1")) * Decimal("100")
    as_of = instant.isoformat().replace("+00:00", "Z")
    return {
        "symbol": symbol,
        "market_date": review_date,
        "as_of": as_of,
        "close": float(close),
        "previous_close": float(previous_close),
        "change_pct": float(change_pct),
    }


def _run_longbridge(
    command: list[str], cwd: Path, *, timeout: int = 30
) -> Any:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MarketCloseRefreshError("Longbridge completed-close read failed") from exc
    if result.returncode != 0:
        raise MarketCloseRefreshError("Longbridge completed-close read failed")
    return _parse_json(result.stdout)


def _analysis_prompt(review_date: str) -> str:
    return (
        f"请仅基于美国市场最近一个已完成交易日（{review_date}）的收盘数据，"
        "使用 LongbridgeAI 的公开市场分析能力，给出一个非常简短的整体市场环境判断，"
        "用于私人交易看板。不要读取或讨论账户、持仓、订单或资金，也不要生成买卖指令。"
        "只返回一个 JSON 对象，不要 Markdown、不要解释、不要代码围栏。"
        "格式必须严格为："
        f'{{"market_date":"{review_date}","conclusion":"一句整体市场环境判断",'
        '"evidence":["支持事实1","支持事实2","支持事实3"],'
        '"next_session_watch":"下一交易日需要验证的一个条件"}。'
        "evidence 最多三条；如果收盘数据不足，conclusion 必须明确写数据不足。"
    )


def _run_longbridge_agent(
    review_date: str,
    *,
    longbridge_bin: str,
    cwd: Path,
) -> Any:
    return _run_longbridge(
        [
            longbridge_bin,
            "agent",
            "chat",
            AGENT_UID,
            _analysis_prompt(review_date),
            "--format",
            "json",
            "--lang",
            "zh-CN",
        ],
        cwd,
        timeout=AGENT_TIMEOUT_SECONDS,
    )


def _agent_text(value: Any, path: str) -> str:
    try:
        return dashboard.validate_market_environment_text(value, path)
    except dashboard.DashboardRenderError as exc:
        raise MarketCloseRefreshError(f"{path} is not admissible") from exc


def parse_agent_environment(response: Any, review_date: str) -> Dict[str, Any]:
    """Project one LongbridgeAI JSON answer into the display environment shape."""

    if not isinstance(response, dict) or response.get("status") != "succeeded":
        raise MarketCloseRefreshError("LongbridgeAI close analysis failed")
    answer = response.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise MarketCloseRefreshError("LongbridgeAI close analysis is empty")
    candidate = answer.strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise MarketCloseRefreshError("LongbridgeAI close analysis is not JSON")
    payload = _parse_json(candidate[start : end + 1])
    if not isinstance(payload, dict):
        raise MarketCloseRefreshError("LongbridgeAI close analysis must be an object")
    required = {"market_date", "conclusion", "evidence", "next_session_watch"}
    if set(payload) != required:
        raise MarketCloseRefreshError("LongbridgeAI close analysis fields are invalid")
    if payload["market_date"] != review_date:
        raise MarketCloseRefreshError("LongbridgeAI close analysis date mismatch")
    evidence = payload["evidence"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
        raise MarketCloseRefreshError("LongbridgeAI evidence must contain one to three items")
    return {
        "status": "complete",
        "headline": _agent_text(payload["conclusion"], "analysis.conclusion"),
        "evidence": [
            _agent_text(value, f"analysis.evidence[{index}]")
            for index, value in enumerate(evidence)
        ],
        "next_session_watch": _agent_text(
            payload["next_session_watch"], "analysis.next_session_watch"
        ),
    }


def partial_environment(review_date: str) -> Dict[str, Any]:
    """Keep the display explicit when the close evidence cannot support analysis."""

    return {
        "status": "partial",
        "headline": "上一交易日收盘数据尚未齐备，本次不形成市场环境判断。",
        "evidence": [],
        "next_session_watch": "先补齐同一收盘日的六个市场代理，再重新生成收盘判断。",
    }


def collect_close_facts(
    review_date: str,
    *,
    longbridge_bin: str,
    cwd: Path,
) -> Dict[str, Optional[Dict[str, Any]]]:
    review_day = dt.date.fromisoformat(review_date)
    start = review_day - dt.timedelta(days=LOOKBACK_DAYS)
    facts: Dict[str, Optional[Dict[str, Any]]] = {}
    for symbol in SYMBOLS:
        response = _run_longbridge(
            [
                longbridge_bin,
                "kline",
                "history",
                symbol,
                "--period",
                "day",
                "--adjust",
                "none",
                "--session",
                "intraday",
                "--start",
                start.isoformat(),
                "--end",
                review_date,
                "--format",
                "json",
            ],
            cwd,
        )
        facts[symbol] = completed_close_fact(response, symbol, review_date)
    return facts


def _ny_midnight(review_date: str) -> str:
    value = dt.datetime.combine(
        dt.date.fromisoformat(review_date), dt.time.min, tzinfo=NY_TZ
    )
    return value.isoformat()


def _next_generated_at(existing: str, now: Optional[dt.datetime]) -> str:
    current = now or dt.datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        raise MarketCloseRefreshError("generated time must include a timezone")
    current = current.astimezone(SHANGHAI_TZ).replace(microsecond=0)
    previous = dt.datetime.fromisoformat(existing.replace("Z", "+00:00"))
    if current <= previous:
        current = previous.astimezone(SHANGHAI_TZ) + dt.timedelta(seconds=1)
    return current.isoformat()


def refresh_snapshot(
    snapshot: Any,
    facts: Mapping[str, Optional[Mapping[str, Any]]],
    *,
    agent_response: Any,
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    admitted = dashboard.validate_display_snapshot(snapshot)
    view = copy.deepcopy(admitted)
    market = view["daily"]["market"]
    rows = market["items"]
    if len(rows) != len(SYMBOLS) or {row["symbol"].upper() for row in rows} != set(SYMBOLS):
        raise MarketCloseRefreshError("display does not contain the fixed six-symbol market set")
    if set(facts) != set(SYMBOLS):
        raise MarketCloseRefreshError("close facts do not match the fixed six-symbol market set")

    review_date = view["daily"]["meta"]["review_date"]
    all_closes_complete = all(
        facts[symbol] is not None and facts[symbol].get("market_date") == review_date
        for symbol in SYMBOLS
    )
    if all_closes_complete:
        if agent_response is None:
            raise MarketCloseRefreshError("LongbridgeAI close analysis is missing")
        environment = parse_agent_environment(agent_response, review_date)
    else:
        environment = partial_environment(review_date)
    complete_times: list[str] = []
    for row in rows:
        symbol = row["symbol"].upper()
        fact = facts[symbol]
        row.pop("capital_flow", None)
        row.pop("unavailable_reason", None)
        if fact is None or fact.get("market_date") != review_date:
            row.update(
                value=None,
                change_pct=None,
                direction="flat",
                strength=0,
                state="收盘数据待补齐",
                session="收盘",
                as_of=_ny_midnight(review_date),
                risk_note="该交易日已完成收盘数据尚未齐备。",
                data_status="partial",
                unavailable_reason="该交易日已完成收盘数据尚未齐备",
            )
            continue
        change = float(fact["change_pct"])
        if not math.isfinite(change):
            raise MarketCloseRefreshError("close change must be finite")
        direction = "up" if change > 0 else "down" if change < 0 else "flat"
        magnitude = abs(change)
        strength = 0 if magnitude < 0.05 else 1 if magnitude < 0.5 else 2 if magnitude < 1 else 3
        row.update(
            value=float(fact["close"]),
            change_pct=change,
            direction=direction,
            strength=strength,
            state="已完成收盘",
            session="收盘",
            as_of=str(fact["as_of"]),
            risk_note="仅作收盘市场环境背景，不构成单一标的触发。",
            data_status="complete",
        )
        complete_times.append(str(fact["as_of"]))

    complete = len(complete_times) == len(SYMBOLS)
    market.update(
        status="complete" if complete else "partial",
        title="市场风险雷达（收盘口径）",
        source_scope="六个既有市场代理的已完成日线收盘；不混用盘前或夜盘报价。",
        note="仅反映上一交易日收盘定价，不构成单一标的自动触发。" if complete else "部分跨资产收盘尚未齐备。",
        basis="completed_close",
        market_date=review_date,
        environment=environment,
    )
    meta = view["daily"]["meta"]
    meta["market_as_of"] = max(complete_times) if complete_times else _ny_midnight(review_date)
    meta["generated_at"] = _next_generated_at(meta["generated_at"], now)
    if not complete:
        meta["overall_status"] = "partial"
    return dashboard.validate_display_snapshot(view)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--longbridge-bin", default="longbridge")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        input_path = prepare_private_output(args.display_input)
        output_path = prepare_private_output(args.output)
        if input_path == output_path:
            raise MarketCloseRefreshError("input and output must be different files")
        if output_path.exists():
            raise MarketCloseRefreshError("output must be a new private artifact")
        raw = input_path.read_bytes()
        if len(raw) > LIMIT:
            raise MarketCloseRefreshError("display input exceeds limit")
        snapshot = _parse_json(raw.decode("utf-8"))
        admitted = dashboard.validate_display_snapshot(snapshot)
        review_date = admitted["daily"]["meta"]["review_date"]
        facts = collect_close_facts(
            review_date,
            longbridge_bin=args.longbridge_bin,
            cwd=output_path.parent,
        )
        agent_response = None
        if all(
            facts[symbol] is not None
            and facts[symbol].get("market_date") == review_date
            for symbol in SYMBOLS
        ):
            agent_response = _run_longbridge_agent(
                review_date,
                longbridge_bin=args.longbridge_bin,
                cwd=output_path.parent,
            )
        refreshed = refresh_snapshot(
            admitted,
            facts,
            agent_response=agent_response,
        )
        write_owner_only_text(
            output_path,
            json.dumps(
                refreshed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n",
        )
        print(json.dumps({
            "status": refreshed["daily"]["market"]["status"],
            "review_date": review_date,
            "output": str(output_path),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, MarketCloseRefreshError):
        sys.stderr.write("market_close_refresh_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
