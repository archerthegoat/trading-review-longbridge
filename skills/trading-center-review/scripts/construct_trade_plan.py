#!/usr/bin/env python3
"""Construct an auditable trading-plan draft from sanitized Longbridge daily bars.

The script never calls a broker and never confirms a plan.  It accepts a fixed
owner-only request packet, applies fail-closed OHLCV checks, computes EMA/ATR
and confirmed swing levels deterministically, and writes a draft or a blocked
result.  Actual Longbridge collection remains a separate authorized adapter.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


REQUEST_SCHEMA = "trading-plan-request.v1"
DRAFT_SCHEMA = "trading-plan-draft.v1"
PRIVATE_ROOT = Path("/private/tmp/trading-center-review-runtime").resolve()
NY_TZ = ZoneInfo("America/New_York")
MINIMUM_BARS = 319
MAXIMUM_SYMBOLS_PER_RUN = 20
MAXIMUM_CALENDAR_DAYS = 550
EMA_WINDOWS = (20, 50, 200)
ATR_WINDOW = 14
SETUPS = frozenset({"pullback", "breakout", "range", "bottom_reversal", "position_management"})
PLAN_STAGES = frozenset({"pre_entry", "position_management"})
ADJUSTMENTS = frozenset({"forward", "backward"})
ZONE_KINDS = frozenset({"observation", "entry", "add", "reduce", "exit", "invalidation"})


class PlanConstructionError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _strict_object(
    value: Any,
    allowed: Iterable[str],
    required: Iterable[str],
    path: str,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanConstructionError("schema", f"{path} must be an object")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise PlanConstructionError("schema", f"{path} contains unsupported field {unknown[0]}")
    missing = sorted(set(required) - set(value))
    if missing:
        raise PlanConstructionError("schema", f"{path} is missing field {missing[0]}")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanConstructionError("schema", f"{path} must be non-empty text")
    return value.strip()


def _integer(value: Any, path: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlanConstructionError("schema", f"{path} must be an integer >= {minimum}")
    return value


def _decimal(value: Any, path: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PlanConstructionError("schema", f"{path} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PlanConstructionError("schema", f"{path} must be a finite decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise PlanConstructionError("schema", f"{path} must be a finite positive decimal")
    return result


def _timestamp(value: Any, path: str) -> dt.datetime:
    text = _text(value, path)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanConstructionError("timezone", f"{path} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PlanConstructionError("timezone", f"{path} must include a timezone")
    return parsed


def _date(value: Any, path: str) -> dt.date:
    text = _text(value, path)
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise PlanConstructionError("schema", f"{path} must be YYYY-MM-DD") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _format_decimal(value: Decimal, places: int = 6) -> str:
    quantum = Decimal(1).scaleb(-places)
    normalized = value.quantize(quantum).normalize()
    return format(normalized, "f")


def _round_down(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _round_up(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _zone(
    kind: str,
    low: Decimal,
    high: Decimal,
    tick: Decimal,
    currency: str,
    condition: str,
    derived_from: str,
) -> Dict[str, Any]:
    if kind not in ZONE_KINDS:
        raise PlanConstructionError("schema", "unsupported plan zone")
    rounded_low = _round_down(min(low, high), tick)
    rounded_high = _round_up(max(low, high), tick)
    if rounded_low <= 0 or rounded_high <= 0:
        raise PlanConstructionError("levels", "plan zone must remain positive")
    return {
        "kind": kind,
        "low": _format_decimal(rounded_low),
        "high": _format_decimal(rounded_high),
        "currency": currency,
        "condition": condition,
        "derived_from": derived_from,
    }


def _ema(values: Sequence[Decimal], period: int) -> List[Optional[Decimal]]:
    if len(values) < period:
        raise PlanConstructionError("coverage", f"EMA{period} has insufficient bars")
    output: List[Optional[Decimal]] = [None] * len(values)
    seed = sum(values[:period], Decimal(0)) / Decimal(period)
    output[period - 1] = seed
    alpha = Decimal(2) / Decimal(period + 1)
    previous = seed
    for index in range(period, len(values)):
        previous = alpha * values[index] + (Decimal(1) - alpha) * previous
        output[index] = previous
    return output


def _atr(bars: Sequence[Mapping[str, Any]], period: int = ATR_WINDOW) -> List[Optional[Decimal]]:
    ranges: List[Decimal] = []
    previous_close: Optional[Decimal] = None
    for bar in bars:
        high = bar["high"]
        low = bar["low"]
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        ranges.append(true_range)
        previous_close = bar["close"]
    output: List[Optional[Decimal]] = [None] * len(ranges)
    seed = sum(ranges[:period], Decimal(0)) / Decimal(period)
    output[period - 1] = seed
    previous = seed
    for index in range(period, len(ranges)):
        previous = (previous * Decimal(period - 1) + ranges[index]) / Decimal(period)
        output[index] = previous
    return output


def _confirmed_swings(bars: Sequence[Mapping[str, Any]], radius: int = 2) -> List[Dict[str, Any]]:
    swings: List[Dict[str, Any]] = []
    for index in range(radius, len(bars) - radius):
        window = bars[index - radius : index + radius + 1]
        current = bars[index]
        if current["low"] == min(row["low"] for row in window) and sum(
            row["low"] == current["low"] for row in window
        ) == 1:
            swings.append({"kind": "support", "price": current["low"], "date": current["market_date"]})
        if current["high"] == max(row["high"] for row in window) and sum(
            row["high"] == current["high"] for row in window
        ) == 1:
            swings.append({"kind": "resistance", "price": current["high"], "date": current["market_date"]})
    return swings


def _cluster_levels(
    swings: Sequence[Mapping[str, Any]],
    kind: str,
    tolerance: Decimal,
    latest_close: Decimal,
    fallback_price: Decimal,
    fallback_date: str,
) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for swing in (row for row in swings if row["kind"] == kind):
        nearest: Optional[Dict[str, Any]] = None
        nearest_distance: Optional[Decimal] = None
        for cluster in clusters:
            distance = abs(swing["price"] - cluster["price"])
            if distance <= tolerance and (nearest_distance is None or distance < nearest_distance):
                nearest = cluster
                nearest_distance = distance
        if nearest is None:
            clusters.append(
                {
                    "kind": kind,
                    "price": swing["price"],
                    "touches": 1,
                    "anchor_dates": [swing["date"]],
                    "method": "confirmed_swing_cluster",
                }
            )
        else:
            touches = nearest["touches"]
            nearest["price"] = (nearest["price"] * Decimal(touches) + swing["price"]) / Decimal(touches + 1)
            nearest["touches"] = touches + 1
            nearest["anchor_dates"].append(swing["date"])

    if kind == "support":
        clusters = [row for row in clusters if row["price"] < latest_close]
    else:
        clusters = [row for row in clusters if row["price"] > latest_close]
    if not clusters:
        clusters = [
            {
                "kind": kind,
                "price": fallback_price,
                "touches": 1,
                "anchor_dates": [fallback_date],
                "method": "120d_extreme_fallback",
            }
        ]
    clusters.sort(
        key=lambda row: (
            -row["touches"],
            -dt.date.fromisoformat(max(row["anchor_dates"])).toordinal(),
            abs(row["price"] - latest_close),
        )
    )
    return clusters[:2]


def _normalize_request(value: Any) -> Dict[str, Any]:
    keys = {
        "schema_version",
        "generated_at",
        "plan_id",
        "version",
        "symbol",
        "display_name",
        "direction",
        "setup_type",
        "plan_stage",
        "holding_horizon_sessions",
        "minimum_reward_risk",
        "max_invalidation_pct",
        "tick_size",
        "currency",
        "expires_at",
        "source",
        "bars",
        "actual_buy_verified",
        "parent_plan_id",
        "parent_plan_version",
        "initial_buy_episode_key",
    }
    root = _strict_object(value, keys, keys, "$")
    if root["schema_version"] != REQUEST_SCHEMA:
        raise PlanConstructionError("schema", "unsupported request schema")
    setup = _text(root["setup_type"], "$.setup_type")
    stage = _text(root["plan_stage"], "$.plan_stage")
    if setup not in SETUPS:
        raise PlanConstructionError("schema", "unsupported setup_type")
    if stage not in PLAN_STAGES:
        raise PlanConstructionError("schema", "unsupported plan_stage")
    if (setup == "position_management") != (stage == "position_management"):
        raise PlanConstructionError("stage", "position_management setup and stage must match")
    actual_buy_verified = root["actual_buy_verified"]
    if not isinstance(actual_buy_verified, bool):
        raise PlanConstructionError("schema", "$.actual_buy_verified must be boolean")
    if stage == "pre_entry":
        if actual_buy_verified:
            raise PlanConstructionError("stage", "pre_entry cannot claim an actual buy")
        if any(root[key] is not None for key in ("parent_plan_id", "parent_plan_version", "initial_buy_episode_key")):
            raise PlanConstructionError("stage", "pre_entry cannot reference a parent or buy episode")
    else:
        if not actual_buy_verified:
            raise PlanConstructionError("stage", "position_management requires a verified actual buy")
        _text(root["parent_plan_id"], "$.parent_plan_id")
        _integer(root["parent_plan_version"], "$.parent_plan_version")
        _text(root["initial_buy_episode_key"], "$.initial_buy_episode_key")

    source_keys = {"provider", "capability", "period", "timezone", "adjustment", "requested_start", "requested_end", "as_of"}
    source = _strict_object(root["source"], source_keys, source_keys, "$.source")
    if _text(source["provider"], "$.source.provider") != "Longbridge":
        raise PlanConstructionError("provider", "plan construction is Longbridge-only")
    if _text(source["capability"], "$.source.capability") != "kline history":
        raise PlanConstructionError("provider", "unsupported Longbridge capability")
    if _text(source["period"], "$.source.period") != "1D":
        raise PlanConstructionError("period", "only completed 1D bars are supported")
    if _text(source["timezone"], "$.source.timezone") != "America/New_York":
        raise PlanConstructionError("timezone", "daily bars must use America/New_York")
    adjustment = _text(source["adjustment"], "$.source.adjustment")
    if adjustment not in ADJUSTMENTS:
        raise PlanConstructionError("adjustment", "adjustment must be explicitly forward or backward")
    requested_start = _date(source["requested_start"], "$.source.requested_start")
    requested_end = _date(source["requested_end"], "$.source.requested_end")
    as_of = _date(source["as_of"], "$.source.as_of")
    if requested_start > requested_end or (requested_end - requested_start).days > MAXIMUM_CALENDAR_DAYS:
        raise PlanConstructionError("scope", "kline request exceeds the approved 550-day window")
    if not requested_start <= as_of <= requested_end:
        raise PlanConstructionError("coverage", "as_of is outside the requested window")

    bars_value = root["bars"]
    if not isinstance(bars_value, list):
        raise PlanConstructionError("schema", "$.bars must be an array")
    bars: List[Dict[str, Any]] = []
    bar_keys = {"timestamp", "open", "high", "low", "close", "volume", "is_complete"}
    previous_at: Optional[dt.datetime] = None
    previous_date: Optional[dt.date] = None
    for index, raw in enumerate(bars_value):
        path = f"$.bars[{index}]"
        row = _strict_object(raw, bar_keys, bar_keys, path)
        timestamp = _timestamp(row["timestamp"], f"{path}.timestamp")
        if previous_at is not None and timestamp <= previous_at:
            raise PlanConstructionError("ordering", "daily bars must be strictly increasing")
        previous_at = timestamp
        market_date = timestamp.astimezone(NY_TZ).date()
        if not requested_start <= market_date <= requested_end:
            raise PlanConstructionError("scope", "daily bar is outside the requested window")
        if previous_date is not None and market_date <= previous_date:
            raise PlanConstructionError("ordering", "daily bars must have unique increasing market dates")
        previous_date = market_date
        complete = row["is_complete"]
        if not isinstance(complete, bool):
            raise PlanConstructionError("schema", f"{path}.is_complete must be boolean")
        open_value = _decimal(row["open"], f"{path}.open", positive=True)
        high = _decimal(row["high"], f"{path}.high", positive=True)
        low = _decimal(row["low"], f"{path}.low", positive=True)
        close = _decimal(row["close"], f"{path}.close", positive=True)
        volume = _decimal(row["volume"], f"{path}.volume", positive=True)
        if low > min(open_value, close) or high < max(open_value, close) or low > high:
            raise PlanConstructionError("ohlcv", f"{path} violates OHLC price boundaries")
        bars.append(
            {
                "timestamp": timestamp,
                "market_date": timestamp.astimezone(NY_TZ).date().isoformat(),
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "is_complete": complete,
            }
        )
    if bars and not bars[-1]["is_complete"]:
        bars = bars[:-1]
    if any(not row["is_complete"] for row in bars):
        raise PlanConstructionError("completion", "only one terminal incomplete daily bar may be removed")
    if len(bars) < MINIMUM_BARS:
        raise PlanConstructionError("coverage", f"at least {MINIMUM_BARS} completed daily bars are required")
    if dt.date.fromisoformat(bars[-1]["market_date"]) != as_of:
        raise PlanConstructionError("coverage", "latest completed bar must match source.as_of")

    generated_at = _timestamp(root["generated_at"], "$.generated_at")
    if generated_at < bars[-1]["timestamp"]:
        raise PlanConstructionError("completion", "completed evidence cannot be later than generated_at")
    if (generated_at.astimezone(NY_TZ).date() - as_of).days > 5:
        raise PlanConstructionError("freshness", "latest completed daily evidence is more than five calendar days old")
    expires_at = _timestamp(root["expires_at"], "$.expires_at")
    if expires_at <= generated_at:
        raise PlanConstructionError("expiry", "expires_at must be after generated_at")
    direction = _text(root["direction"], "$.direction")
    if direction not in {"long", "short", "hedge"}:
        raise PlanConstructionError("schema", "unsupported direction")
    if direction != "long":
        raise PlanConstructionError("scope", "the first price-zone implementation supports long underlying plans only")
    symbol = _text(root["symbol"], "$.symbol").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,20}\.US", symbol) or re.search(r"\d{6}[CP]\d", symbol):
        raise PlanConstructionError("scope", "plan symbol must be a US underlying, not an option contract")
    return {
        "generated_at": generated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "plan_id": _text(root["plan_id"], "$.plan_id"),
        "version": _integer(root["version"], "$.version"),
        "symbol": symbol,
        "display_name": _text(root["display_name"], "$.display_name"),
        "direction": direction,
        "setup_type": setup,
        "plan_stage": stage,
        "holding_horizon_sessions": _integer(root["holding_horizon_sessions"], "$.holding_horizon_sessions"),
        "minimum_reward_risk": _decimal(root["minimum_reward_risk"], "$.minimum_reward_risk", positive=True),
        "max_invalidation_pct": _decimal(root["max_invalidation_pct"], "$.max_invalidation_pct", positive=True),
        "tick_size": _decimal(root["tick_size"], "$.tick_size", positive=True),
        "currency": _text(root["currency"], "$.currency"),
        "source": {
            "provider": "Longbridge",
            "capability": "kline history",
            "period": "1D",
            "timezone": "America/New_York",
            "adjustment": adjustment,
            "requested_start": requested_start.isoformat(),
            "requested_end": requested_end.isoformat(),
            "as_of": as_of.isoformat(),
        },
        "bars": bars,
        "actual_buy_verified": actual_buy_verified,
        "parent_plan_id": root["parent_plan_id"],
        "parent_plan_version": root["parent_plan_version"],
        "initial_buy_episode_key": root["initial_buy_episode_key"],
    }


def _blocked_packet(value: Any, error: PlanConstructionError) -> Dict[str, Any]:
    source = value.get("source", {}) if isinstance(value, dict) else {}
    if not isinstance(source, dict):
        source = {}
    return {
        "schema_version": DRAFT_SCHEMA,
        "data_status": "blocked",
        "plan_status": "draft",
        "plan_readiness": "blocked",
        "plan_id": value.get("plan_id") if isinstance(value, dict) else None,
        "version": value.get("version") if isinstance(value, dict) else None,
        "symbol": value.get("symbol") if isinstance(value, dict) else None,
        "source": {
            "provider": source.get("provider"),
            "capability": source.get("capability"),
            "period": source.get("period"),
            "timezone": source.get("timezone"),
            "adjustment": source.get("adjustment"),
            "as_of": source.get("as_of"),
        },
        "evidence_id": None,
        "zones": [],
        "conditions": [],
        "gap": {"category": error.category, "message": str(error)},
    }


def _construct_plan(value: Any) -> Dict[str, Any]:
    try:
        request = _normalize_request(value)
    except PlanConstructionError as error:
        return _blocked_packet(value, error)

    bars = request["bars"]
    closes = [row["close"] for row in bars]
    ema_series = {window: _ema(closes, window) for window in EMA_WINDOWS}
    atr_series = _atr(bars)
    latest = bars[-1]
    atr14 = atr_series[-1]
    if atr14 is None or atr14 <= 0:
        return _blocked_packet(value, PlanConstructionError("atr", "ATR14 is unavailable"))
    ema_latest = {window: ema_series[window][-1] for window in EMA_WINDOWS}
    if any(value is None for value in ema_latest.values()):
        return _blocked_packet(value, PlanConstructionError("ema", "EMA evidence is unavailable"))

    last5_directions = {
        window: "up"
        if ema_series[window][-1] > ema_series[window][-6]
        else "down"
        if ema_series[window][-1] < ema_series[window][-6]
        else "flat"
        for window in EMA_WINDOWS
    }
    bullish = (
        latest["close"] > ema_latest[20] > ema_latest[50] > ema_latest[200]
        and all(direction == "up" for direction in last5_directions.values())
    )
    bearish = (
        latest["close"] < ema_latest[20] < ema_latest[50] < ema_latest[200]
        and all(direction == "down" for direction in last5_directions.values())
    )
    regime = "bull" if bullish else "bear" if bearish else "range"

    swings = _confirmed_swings(bars)
    recent = bars[-120:]
    recent_low = min(recent, key=lambda row: row["low"])
    recent_high = max(recent, key=lambda row: row["high"])
    tolerance = atr14 * Decimal("0.5")
    supports = _cluster_levels(
        swings,
        "support",
        tolerance,
        latest["close"],
        recent_low["low"],
        recent_low["market_date"],
    )
    resistances = _cluster_levels(
        swings,
        "resistance",
        tolerance,
        latest["close"],
        recent_high["high"],
        recent_high["market_date"],
    )
    supports.sort(key=lambda row: abs(latest["close"] - row["price"]))
    resistances.sort(key=lambda row: abs(row["price"] - latest["close"]))
    support = supports[0]
    resistance = resistances[0]
    currency = request["currency"]
    tick = request["tick_size"]
    zones: List[Dict[str, Any]] = [
        _zone(
            "observation",
            support["price"] - atr14 * Decimal("0.25"),
            support["price"] + atr14 * Decimal("0.25"),
            tick,
            currency,
            "进入该区间只开始观察，不能单独触发买入",
            f"{support['method']}:{max(support['anchor_dates'])}",
        ),
        _zone(
            "invalidation",
            support["price"] - atr14 * Decimal("0.75"),
            support["price"] - atr14 * Decimal("0.50"),
            tick,
            currency,
            "结构收盘失效或计划定义的止损条件成立",
            f"support_minus_atr:{support['method']}",
        ),
    ]
    conditions: List[str] = []
    gaps: List[str] = []
    entry_low: Optional[Decimal] = None
    entry_high: Optional[Decimal] = None
    target_low: Optional[Decimal] = None
    target_high: Optional[Decimal] = None

    previous = bars[-2]
    previous_ema20 = ema_series[20][-2]
    bottom_signal = (
        latest["low"] > previous["low"] and latest["close"] > previous["high"]
    ) or (
        previous_ema20 is not None
        and previous["close"] <= previous_ema20
        and latest["close"] > ema_latest[20]
    )
    recent_bottom_context = any(
        row["low"] <= recent_low["low"] + atr14 * Decimal("0.5")
        or row["close"] <= ema_series[20][index] - atr14 * Decimal("2")
        for index, row in enumerate(bars)
        if index >= len(bars) - 10
    )
    bottom_confirmed = recent_bottom_context and bottom_signal

    setup = request["setup_type"]
    if setup == "pullback":
        conditions.extend(["趋势不得为 bear", "回踩支撑后以已完成日线确认企稳"])
        if regime != "bear":
            entry_low = support["price"] + atr14 * Decimal("0.05")
            entry_high = support["price"] + atr14 * Decimal("0.45")
            target_low = resistance["price"] - atr14 * Decimal("0.20")
            target_high = resistance["price"] + atr14 * Decimal("0.10")
        else:
            gaps.append("bear_regime_blocks_pullback_entry")
    elif setup == "breakout":
        conditions.extend(["已完成日线收于突破区上方", "突破或回踩确认不得破坏原阻力结构"])
        if len(resistances) >= 2:
            breakout = resistances[0]
            target = resistances[1]
            entry_low = breakout["price"] + atr14 * Decimal("0.10")
            entry_high = breakout["price"] + atr14 * Decimal("0.35")
            target_low = target["price"] - atr14 * Decimal("0.20")
            target_high = target["price"] + atr14 * Decimal("0.10")
        else:
            gaps.append("second_resistance_missing")
    elif setup == "range":
        conditions.extend(["EMA 与价格结构保持 range", "支撑确认后才允许进入"])
        if regime == "range":
            entry_low = support["price"] - atr14 * Decimal("0.15")
            entry_high = support["price"] + atr14 * Decimal("0.25")
            target_low = resistance["price"] - atr14 * Decimal("0.25")
            target_high = resistance["price"] + atr14 * Decimal("0.05")
        else:
            gaps.append("non_range_regime")
    elif setup == "bottom_reversal":
        conditions.extend(["先进入长期支撑或显著偏离观察区", "必须出现右侧止跌或反转确认"])
        if bottom_confirmed:
            entry_low = latest["close"] - atr14 * Decimal("0.15")
            entry_high = latest["close"] + atr14 * Decimal("0.15")
            target_candidates = [
                price for price in (ema_latest[20], ema_latest[50], resistance["price"]) if price > latest["close"]
            ]
            if target_candidates:
                target = min(target_candidates)
                target_low = target - atr14 * Decimal("0.20")
                target_high = target + atr14 * Decimal("0.10")
            else:
                gaps.append("reversion_target_missing")
        else:
            gaps.append(
                "bottom_reversal_confirmation_missing" if recent_bottom_context
                else "long_support_or_downside_deviation_missing"
            )
    else:
        conditions.extend(["初始买入已由 Longbridge 事实验证", "原交易逻辑仍成立并出现新的有利结构"])
        favorable = regime != "bear" and latest["close"] >= ema_latest[20]
        if favorable:
            entry_low = support["price"] + atr14 * Decimal("0.05")
            entry_high = support["price"] + atr14 * Decimal("0.30")
            target_low = resistance["price"] - atr14 * Decimal("0.20")
            target_high = resistance["price"] + atr14 * Decimal("0.10")
        else:
            gaps.append("favorable_add_structure_missing")

    actionable = all(value is not None for value in (entry_low, entry_high, target_low, target_high))
    reward_risk: Optional[Decimal] = None
    invalidation_pct: Optional[Decimal] = None
    if actionable:
        # The entire quoted range must respect the risk limit. Midpoints can
        # hide an over-risk entry at the expensive edge of a rounded zone.
        worst_entry = _round_up(entry_high, tick)
        worst_stop = Decimal(zones[1]["low"])
        conservative_target = _round_down(target_low, tick)
        risk = worst_entry - worst_stop
        reward = conservative_target - worst_entry
        if risk <= 0 or reward <= 0:
            actionable = False
            gaps.append("non_positive_reward_or_risk")
        else:
            reward_risk = reward / risk
            invalidation_pct = risk / worst_entry * Decimal(100)
            if reward_risk < request["minimum_reward_risk"]:
                actionable = False
                gaps.append("reward_risk_below_user_threshold")
            if invalidation_pct > request["max_invalidation_pct"]:
                actionable = False
                gaps.append("invalidation_exceeds_user_limit")

    if actionable and entry_low is not None and entry_high is not None:
        entry_kind = "add" if request["plan_stage"] == "position_management" else "entry"
        zones.append(
            _zone(
                entry_kind,
                entry_low,
                entry_high,
                tick,
                currency,
                "；".join(conditions + ["仅在全部条件满足且计划版本已确认后有效"]),
                f"{setup}:ema_atr_swing",
            )
        )
        zones.append(
            _zone(
                "reduce",
                target_low,
                target_high,
                tick,
                currency,
                "到达目标区后按已确认计划评估减仓，不构成无条件卖出",
                f"resistance_or_ema:{resistance['method']}",
            )
        )

    if request["plan_stage"] == "pre_entry" and any(zone["kind"] == "add" for zone in zones):
        return _blocked_packet(value, PlanConstructionError("stage", "pre_entry cannot contain an add zone"))

    serialized_bars = [
        {
            "timestamp": row["timestamp"].isoformat(),
            "open": _format_decimal(row["open"]),
            "high": _format_decimal(row["high"]),
            "low": _format_decimal(row["low"]),
            "close": _format_decimal(row["close"]),
            "volume": _format_decimal(row["volume"]),
        }
        for row in bars
    ]
    evidence_id = _hash(
        {
            "source": request["source"],
            "symbol": request["symbol"],
            "bars": serialized_bars,
            "contract": "longbridge-ema-atr-swing.v1",
        }
    )
    level_rows = []
    for row in supports + resistances:
        level_rows.append(
            {
                "kind": row["kind"],
                "price": _format_decimal(row["price"]),
                "method": row["method"],
                "touches": row["touches"],
                "anchor_dates": row["anchor_dates"],
            }
        )
    result: Dict[str, Any] = {
        "schema_version": DRAFT_SCHEMA,
        "data_status": "complete" if actionable else "partial",
        "plan_status": "draft",
        "plan_readiness": "ready_for_confirmation" if actionable else "observation_only",
        "plan_id": request["plan_id"],
        "version": request["version"],
        "symbol": request["symbol"],
        "display_name": request["display_name"],
        "direction": request["direction"],
        "setup_type": setup,
        "plan_stage": request["plan_stage"],
        "generated_at": request["generated_at"],
        "expires_at": request["expires_at"],
        "parent_plan_id": request["parent_plan_id"],
        "parent_plan_version": request["parent_plan_version"],
        "initial_buy_episode_key": request["initial_buy_episode_key"],
        "constraints": {
            "holding_horizon_sessions": request["holding_horizon_sessions"],
            "minimum_reward_risk": _format_decimal(request["minimum_reward_risk"]),
            "max_invalidation_pct": _format_decimal(request["max_invalidation_pct"]),
            "tick_size": _format_decimal(tick),
        },
        "source": request["source"],
        "evidence_id": evidence_id,
        "evidence": {
            "contract_version": "longbridge-ema-atr-swing.v1",
            "bars_used": len(bars),
            "latest_close": _format_decimal(latest["close"]),
            "ema20": _format_decimal(ema_latest[20]),
            "ema50": _format_decimal(ema_latest[50]),
            "ema200": _format_decimal(ema_latest[200]),
            "ema_5d_direction": {str(key): value for key, value in last5_directions.items()},
            "atr14": _format_decimal(atr14),
            "regime": regime,
            "bottom_reversal_confirmed": bottom_confirmed,
            "bottom_context_present": recent_bottom_context,
            "levels": level_rows,
            "reward_risk": None if reward_risk is None else _format_decimal(reward_risk),
            "invalidation_pct": None if invalidation_pct is None else _format_decimal(invalidation_pct),
        },
        "zones": zones,
        "conditions": conditions,
        "gaps": gaps,
        "boundary": "条件式计划草案；不构成下单或无条件买卖指令。区间只基于已完成 Longbridge 日线。",
    }
    result["content_hash"] = _hash(result)
    return result


def construct_plan(value: Any) -> Dict[str, Any]:
    try:
        return _construct_plan(value)
    except PlanConstructionError as error:
        return _blocked_packet(value, error)


def _private_path(path: Path, label: str, *, require_existing: bool = False) -> Path:
    from run_incremental_review import RunnerContractError, _private_path as checked_path
    try:
        return checked_path(path, label, require_existing=require_existing)
    except RunnerContractError as exc:
        raise PlanConstructionError("path", str(exc)) from exc


def _write_owner_only(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        input_path = _private_path(args.input, "input", require_existing=True)
        output_path = _private_path(args.output, "output")
        if input_path == output_path:
            raise PlanConstructionError("path", "draft output must not overwrite source evidence")
        request = json.loads(input_path.read_text(encoding="utf-8"))
        result = construct_plan(request)
        _write_owner_only(output_path, result)
    except (PlanConstructionError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "schema_version": DRAFT_SCHEMA,
                    "error_category": getattr(error, "category", "plan_construction_failure"),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result["data_status"],
                "schema_version": DRAFT_SCHEMA,
                "plan_readiness": result["plan_readiness"],
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["data_status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
