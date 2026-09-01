#!/usr/bin/env python3
"""Refresh the fixed market radar from completed Longbridge daily closes.

The command reads only six public market proxies. It never calls account,
position, order, execution, statement, or trading capabilities. The admitted
display input and sanitized output stay in the owner-only private runtime.
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


def _run_longbridge(command: list[str], cwd: Path) -> Any:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MarketCloseRefreshError("Longbridge completed-close read failed") from exc
    if result.returncode != 0:
        raise MarketCloseRefreshError("Longbridge completed-close read failed")
    return _parse_json(result.stdout)


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


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "待补齐"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):.2f}%"


def _change(facts: Mapping[str, Optional[Mapping[str, Any]]], symbol: str) -> Optional[float]:
    fact = facts.get(symbol)
    if fact is None:
        return None
    value = float(fact["change_pct"])
    return value if math.isfinite(value) else None


def _signal_text(
    facts: Mapping[str, Optional[Mapping[str, Any]]],
    symbols: tuple[str, ...],
) -> str:
    return "，".join(f"{symbol.removesuffix('.US')} {_pct(_change(facts, symbol))}" for symbol in symbols)


def build_environment(
    facts: Mapping[str, Optional[Mapping[str, Any]]],
) -> Dict[str, Any]:
    available = {symbol for symbol in SYMBOLS if facts.get(symbol) is not None}
    signals = [
        {"label": "权益", "text": _signal_text(facts, ("SPY.US", "QQQ.US"))},
        {"label": "利率与避险代理", "text": _signal_text(facts, ("IEF.US", "GLD.US"))},
        {"label": "高波动与通胀代理", "text": _signal_text(facts, ("IBIT.US", "USO.US"))},
    ]
    spy = _change(facts, "SPY.US")
    qqq = _change(facts, "QQQ.US")
    if spy is None or qqq is None:
        return {
            "status": "partial",
            "headline": "上一交易日收盘数据尚未齐备，本次不形成市场环境判断。",
            "pricing_signals": signals,
            "cross_asset_confirmation": "两项权益基准未同时齐备，暂不能判断整体风险偏好。",
            "next_session_watch": "先补齐同一交易日的 SPY 与 QQQ 收盘，再观察其他资产是否确认。",
        }

    threshold = 0.10
    if spy > threshold and qqq > threshold:
        regime = "strong"
        headline = "权益收盘同步走强，市场风险偏好偏强，但仍需跨资产确认。"
    elif spy < -threshold and qqq < -threshold:
        regime = "weak"
        headline = "权益收盘同步走弱，市场风险偏好偏弱，但仍需跨资产确认。"
    elif abs(spy) <= threshold and abs(qqq) <= threshold:
        regime = "neutral"
        headline = "权益收盘变化有限，市场环境暂偏中性。"
    else:
        regime = "mixed"
        headline = "标普与纳指收盘信号分化，市场环境偏混合。"

    confirmation: list[str] = []
    ibit = _change(facts, "IBIT.US")
    ief = _change(facts, "IEF.US")
    uso = _change(facts, "USO.US")
    gld = _change(facts, "GLD.US")
    if regime in {"strong", "weak"} and ibit is not None:
        same_direction = (regime == "strong" and ibit > threshold) or (
            regime == "weak" and ibit < -threshold
        )
        confirmation.append(
            "IBIT 与权益同向，高波动风险偏好得到确认。"
            if same_direction
            else "IBIT 未与权益同向，高波动风险偏好尚未确认。"
        )
    if ief is not None:
        if ief > threshold:
            confirmation.append("IEF 收涨，利率代理未形成额外压制。")
        elif ief < -threshold:
            confirmation.append("IEF 收跌，利率端仍可能构成约束。")
    if uso is not None and uso > 1.0:
        confirmation.append("USO 明显上涨，需继续观察通胀定价压力。")
    elif uso is not None and uso < -1.0:
        confirmation.append("USO 明显下跌，增长与需求预期仍需观察。")
    if gld is not None and abs(gld) > 0.5 and len(confirmation) < 2:
        confirmation.append("GLD 波动较大，避险与实际利率信号并不单一。")
    if len(available) < len(SYMBOLS):
        confirmation.insert(0, "部分跨资产收盘尚未齐备，确认强度有限。")
    cross_asset = "".join(confirmation[:2]) or "跨资产变化有限，暂未形成额外确认或反例。"

    if regime == "strong":
        watch = "观察 SPY 与 QQQ 能否继续同向，并看 IBIT 是否保持确认；若 IEF 转弱且 USO 继续上行，当前偏强判断需降级。"
    elif regime == "weak":
        watch = "观察 SPY 与 QQQ 是否继续同向走弱；若权益与 IBIT 同步修复，当前偏弱判断失效。"
    else:
        watch = "先看 SPY 与 QQQ 是否重新同向；方向统一前，不把单一资产波动升级为整体市场趋势。"
    return {
        "status": "complete" if len(available) == len(SYMBOLS) else "partial",
        "headline": headline,
        "pricing_signals": signals,
        "cross_asset_confirmation": cross_asset,
        "next_session_watch": watch,
    }


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
        environment=build_environment(facts),
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
        refreshed = refresh_snapshot(admitted, facts)
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
