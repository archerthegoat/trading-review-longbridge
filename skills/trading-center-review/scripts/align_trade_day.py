#!/usr/bin/env python3
"""Align one authorized Longbridge trade day with holdings and completed market bars."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


US_EASTERN = ZoneInfo("America/New_York")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Trade date, YYYY-MM-DD")
    parser.add_argument("--output", required=True, type=Path, help="Private Markdown output outside a Git worktree")
    parser.add_argument("--longbridge-bin", default="longbridge")
    return parser.parse_args()


def is_in_git_worktree(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in (resolved.parent, *resolved.parents))


def us_eastern_window(trade_date: str) -> tuple[str, str]:
    """Return the US-equity calendar day as an explicit UTC half-open window.

    The CLI accepts RFC 3339 timestamps.  Date-only values are local-time
    inputs, so use America/New_York midnight boundaries and convert them to
    UTC.  This preserves daylight-saving transitions and avoids treating a
    Shanghai or UTC calendar day as a US trading day.
    """
    parsed = datetime.strptime(trade_date, "%Y-%m-%d").date()
    start = datetime.combine(parsed, datetime.min.time(), tzinfo=US_EASTERN)
    end = start + timedelta(days=1)
    return (
        start.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        end.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
    )


def market_symbol(symbol: str) -> str:
    """Map an option-like Longbridge symbol to its underlying US stock symbol."""
    upper = symbol.upper()
    if not upper.endswith(".US"):
        return upper
    body = upper[:-3]
    match = re.match(r"^([A-Z]+)(?=\d)", body)
    return f"{match.group(1)}.US" if match else upper


def run_json(command: list[str], cwd: Path) -> object:
    result = subprocess.run(command, check=False, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge read failed ({command[1]}): exit {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Longbridge returned non-JSON for {command[1]}") from error


def records(value: object, source: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"Unexpected {source} response shape; expected an array of objects")
    return value


def value(item: dict[str, object], key: str) -> str:
    current = item.get(key)
    return "不可用" if current is None or current == "" else str(current)


def market_rows(value_object: object) -> list[dict[str, object]]:
    if isinstance(value_object, list):
        return [item for item in value_object if isinstance(item, dict)]
    if isinstance(value_object, dict):
        for key in ("data", "items", "candles", "klines", "list"):
            if key in value_object:
                return market_rows(value_object[key])
    return []


def compact_order(item: dict[str, object]) -> str:
    return (
        f"{value(item, 'submitted_at')} | {value(item, 'symbol')} | "
        f"{value(item, 'side')} | qty={value(item, 'quantity')} | "
        f"filled={value(item, 'filled')} | price={value(item, 'price')} | status={value(item, 'status')}"
    )


def compact_execution(item: dict[str, object], order_by_id: dict[str, dict[str, object]]) -> str:
    order = order_by_id.get(value(item, "order_id"), {})
    side = value(order, "side") if order else "未从订单映射"
    return (
        f"{value(item, 'time')} | {value(item, 'symbol')} | {side} | "
        f"qty={value(item, 'quantity')} | price={value(item, 'price')}"
    )


def compact_position(item: dict[str, object]) -> str:
    return (
        f"{value(item, 'symbol')} | qty={value(item, 'quantity')} | "
        f"available={value(item, 'available')} | cost={value(item, 'cost_price')} | currency={value(item, 'currency')}"
    )


def compact_bar(item: dict[str, object]) -> str:
    return " | ".join(
        f"{key}={value(item, key)}" for key in ("timestamp", "time", "open", "high", "low", "close", "volume", "turnover") if key in item
    ) or json.dumps(item, ensure_ascii=False, sort_keys=True)


def render_report(date: str, start_ts: str, end_ts: str, orders: list[dict[str, object]], executions: list[dict[str, object]], positions: list[dict[str, object]], bars: dict[str, list[dict[str, object]]]) -> str:
    order_by_id = {value(item, "order_id"): item for item in orders if value(item, "order_id") != "不可用"}
    symbols = sorted({value(item, "symbol") for item in orders + executions if value(item, "symbol") != "不可用"})
    be_symbols = [symbol for symbol in symbols if symbol.upper().startswith("BE.") or symbol.upper() == "BE"]
    position_symbols = {value(item, "symbol") for item in positions}
    be_in_positions = sorted(symbol for symbol in position_symbols if symbol.upper().startswith("BE.") or symbol.upper() == "BE")
    side_counts = Counter(value(item, "side") for item in orders)
    status_counts = Counter(value(item, "status") for item in orders)
    market_symbols = sorted({market_symbol(symbol) for symbol in symbols})
    market_missing = sorted(symbol for symbol in market_symbols if not bars.get(symbol))
    status = "已对齐（标的级）" if not market_missing else "partial_data"

    order_lines = "\n".join(f"- {compact_order(item)}" for item in orders) or "- 周五没有返回历史订单。"
    execution_lines = "\n".join(f"- {compact_execution(item, order_by_id)}" for item in executions) or "- 周五没有返回历史成交。"
    position_lines = "\n".join(f"- {compact_position(item)}" for item in positions) or "- 当前持仓接口没有返回记录。"
    market_lines = []
    for symbol in market_symbols:
        rows = bars.get(symbol, [])
        market_lines.append(f"### {symbol}")
        market_lines.extend(f"- {compact_bar(row)}" for row in rows) if rows else market_lines.append("- 未返回周五日线。")
    market_text = "\n".join(market_lines) or "- 周五订单/成交没有可用于市场对齐的标的。"

    return f"""# 周五实际成交对齐｜{date}

## 数据与授权状态

- 授权：本线程授权的 Longbridge 只读连接；未读取凭据内容。
- 数据源：Longbridge CLI 历史订单/成交、当前持仓、`kline history` 完成日线。
- 对齐状态：{status}。
- 时间范围：订单与成交按美东（America/New_York）`{date}` 的半开日界查询，对应 UTC `{start_ts}` 至 `{end_ts}`；持仓为读取时快照；市场 K 线为 {date} 的完成日线。
- Wiki：未写入；本报告位于 Git 工作树外。

## BE 核对

- 周五订单/成交中识别到的 BE 标的：{', '.join(be_symbols) if be_symbols else '未识别到'}
- BE 是否有成交记录：{'是' if any(symbol in be_symbols for symbol in [value(item, 'symbol') for item in executions]) else '否/未返回'}
- 当前持仓快照中是否有 BE：{'是' if be_in_positions else '否/未返回'}
- 注意：当前持仓快照不能证明周五收盘时是否持有 BE。

## 周五订单（原始明细的私有摘要）

- 订单总数：{len(orders)}
- 方向计数：{dict(sorted(side_counts.items()))}
- 状态计数：{dict(sorted(status_counts.items()))}
{order_lines}

## 周五成交（原始明细的私有摘要）

- 成交总数：{len(executions)}
{execution_lines}

## 当前持仓快照（私有摘要）

- 持仓条数：{len(positions)}
{position_lines}

## {date} 市场实际数据

{market_text}

## 与当前交易想法的对齐

- 本周对齐范围：仅 {date} 的成交、读取时持仓与完成日线。
- 项目内当前没有已确认的交易想法卡；本次只保留用户口述的三条现有想法为“待更新”，不自动推断其内容。
- BE 若存在实际成交但没有对应想法卡，应标记为“已执行、想法记录缺口”，而不是事后补写成原先已有计划。
- 需要用户确认：BE 的开仓理由、原计划/失效条件、实际执行是否符合计划。

## 未验证与下一步

- 下周对齐计划：先补齐已确认的三条交易想法卡，再将下一交易日的市场事实与实际成交继续对齐。
- 市场 K 线只覆盖周五成交标的，不代表整个市场或全部持仓的相对表现。
- 未做周五收盘持仓历史还原；当前持仓只是查询时快照。
- 未进行 Wiki 写入、AI 摘要或交易建议。
"""


def main() -> int:
    args = parse_args()
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
        if is_in_git_worktree(args.output):
            raise ValueError("--output must be outside a Git worktree")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        base = args.longbridge_bin
        start_ts, end_ts = us_eastern_window(args.date)
        orders = records(run_json([base, "order", "--history", "--start", start_ts, "--end", end_ts, "--format", "json"], args.output.parent), "orders")
        executions = records(run_json([base, "order", "executions", "--history", "--start", start_ts, "--end", end_ts, "--format", "json"], args.output.parent), "executions")
        positions = records(run_json([base, "positions", "--format", "json"], args.output.parent), "positions")
        symbols = sorted({value(item, "symbol") for item in orders + executions if value(item, "symbol") != "不可用"})
        bars = {}
        for symbol in sorted({market_symbol(symbol) for symbol in symbols}):
            bars[symbol] = market_rows(run_json([base, "kline", "history", symbol, "--period", "day", "--start", args.date, "--end", args.date, "--format", "json"], args.output.parent))
        report = render_report(args.date, start_ts, end_ts, orders, executions, positions, bars)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(args.output)
        print(f"PASS: aligned {args.date} orders, executions, positions, and market bars")
        print(f"PRIVATE_REPORT: {args.output}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
