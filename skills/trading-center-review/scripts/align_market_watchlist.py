#!/usr/bin/env python3
"""Align public Longbridge market bars, EMAs, and event feeds for a watchlist.

This script deliberately does not call account, positions, orders, executions, or
statement endpoints.  Its output is a private Markdown report outside the Git
worktree; the script itself contains no account data.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_SYMBOLS = ("QQQ.US", "VOO.US", "SOXX.US", "DRAM.US")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Market date, YYYY-MM-DD")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help="Public symbols in CODE.MARKET form (default: QQQ.US VOO.US SOXX.US DRAM.US)",
    )
    parser.add_argument(
        "--history-start",
        help="Start of EMA history, YYYY-MM-DD (default: one year before --date)",
    )
    parser.add_argument(
        "--event-end",
        help="Last event/news date to retain, YYYY-MM-DD (default: same as --date)",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Private Markdown output path outside a Git worktree",
    )
    parser.add_argument("--longbridge-bin", default="longbridge", help="Longbridge CLI executable")
    return parser.parse_args()


def is_in_git_worktree(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in (resolved.parent, *resolved.parents))


def validate_date(value: str, option: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"{option} must be YYYY-MM-DD") from error


def run_json(command: list[str], cwd: Path) -> object:
    result = subprocess.run(command, check=False, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "no stderr"
        raise RuntimeError(f"Longbridge read failed ({' '.join(command[1:3])}): exit {result.returncode}; {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Longbridge returned non-JSON for {' '.join(command[1:3])}") from error


def string_value(value: object, fallback: str = "不可用") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def list_records(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "list", "candles", "klines"):
            if key in value:
                return list_records(value[key])
    return []


def market_rows(value: object) -> list[dict[str, Any]]:
    return list_records(value)


def close_series(rows: list[dict[str, Any]]) -> list[float]:
    ordered = sorted(rows, key=lambda row: string_value(row.get("time"), ""))
    closes: list[float] = []
    for row in ordered:
        try:
            closes.append(float(row["close"]))
        except (KeyError, TypeError, ValueError):
            continue
    return closes


def ema(rows: list[dict[str, Any]], period: int) -> float | None:
    closes = close_series(rows)
    if not closes:
        return None
    alpha = 2.0 / (period + 1)
    current = closes[0]
    for close in closes[1:]:
        current = alpha * close + (1 - alpha) * current
    return current if len(closes) >= period else None


def event_groups(value: object) -> list[dict[str, Any]]:
    """Flatten Longbridge calendar responses while preserving the group date.

    Longbridge's JSON envelope places calendar groups under ``data.list``.
    Some CLI versions expose the inner object directly, so accept both shapes
    instead of silently treating a valid calendar response as empty.
    """
    if isinstance(value, dict) and isinstance(value.get("data"), (dict, list)):
        return event_groups(value["data"])
    if isinstance(value, list):
        groups = value
    elif isinstance(value, dict):
        groups = value.get("list")
    else:
        return []
    if not isinstance(groups, list):
        return []
    result: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_date = group.get("date")
        infos = group.get("infos")
        if isinstance(infos, list):
            for info in infos:
                if isinstance(info, dict):
                    result.append({"event_date": group_date, **info})
        else:
            result.append({"event_date": group_date, **group})
    return result


def in_date_window(value: object, start: date, end: date) -> bool:
    try:
        current = datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return start <= current <= end


def compact_data_kv(item: dict[str, Any]) -> str:
    values = item.get("data_kv")
    if not isinstance(values, list):
        return ""
    parts: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        key = string_value(value.get("key"), "")
        shown = string_value(value.get("value"), "")
        if key and shown:
            parts.append(f"{key}={shown}")
    return "；".join(parts)


def quote(value: object) -> str:
    return string_value(value).replace("|", "\\|").replace("\n", " ")


def percent_change(row: dict[str, Any]) -> str:
    try:
        open_price = float(row["open"])
        close_price = float(row["close"])
        return f"{(close_price / open_price - 1) * 100:.2f}%"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return "不可用"


def render_report(
    as_of: str,
    history_start: str,
    event_end: str,
    symbols: list[str],
    static_rows: dict[str, dict[str, Any]],
    bars: dict[str, list[dict[str, Any]]],
    earnings: list[dict[str, Any]],
    macro: list[dict[str, Any]],
    news: dict[str, list[dict[str, Any]]],
    optional_errors: list[str],
) -> str:
    table_rows: list[str] = []
    for symbol in symbols:
        rows = bars.get(symbol, [])
        row = next((item for item in rows if str(item.get("time", "")).startswith(as_of)), rows[-1] if rows else {})
        name = string_value(static_rows.get(symbol, {}).get("name"))
        ema20 = ema(rows, 20)
        ema100 = ema(rows, 100)
        close = row.get("close")
        close_number = float(close) if close not in (None, "") else None
        checks: list[str] = []
        if close_number is not None and ema20 is not None:
            checks.append(f"收盘{'高于' if close_number >= ema20 else '低于'} EMA20")
        if close_number is not None and ema100 is not None:
            checks.append(f"收盘{'高于' if close_number >= ema100 else '低于'} EMA100")
        table_rows.append(
            "| "
            + " | ".join(
                [
                    quote(symbol),
                    quote(name),
                    quote(row.get("open")),
                    quote(row.get("high")),
                    quote(row.get("low")),
                    quote(row.get("close")),
                    percent_change(row),
                    f"{ema20:.3f}" if ema20 is not None else "不可用（样本不足）",
                    f"{ema100:.3f}" if ema100 is not None else "不可用（样本不足）",
                    "；".join(checks) or "不可用",
                ]
            )
            + " |"
        )

    event_lines: list[str] = []
    for item in earnings:
        event_lines.append(
            f"- {quote(item.get('event_date'))}｜财报｜{quote(item.get('name') or item.get('title') or item.get('symbol'))}"
        )
    for item in macro:
        detail = compact_data_kv(item)
        event_lines.append(
            f"- {quote(item.get('event_date'))} {quote(item.get('date'))}｜宏观｜{quote(item.get('content') or item.get('name'))}"
            + (f"｜{detail}" if detail else "")
        )
    if not event_lines:
        event_lines.append("- 该日期窗口未返回财报或 3 星宏观事件。")

    news_lines: list[str] = []
    for symbol in symbols:
        retained = news.get(symbol, [])
        if not retained:
            news_lines.append(f"- {symbol}：窗口内未返回新闻。")
            continue
        news_lines.append(f"- {symbol}：")
        for item in retained[:3]:
            news_lines.append(
                f"  - {quote(item.get('published_at'))}｜{quote(item.get('title'))}｜{quote(item.get('url'))}"
            )

    optional_error_text = "\n".join(f"- {error}" for error in optional_errors) or "- 无"
    return f"""# Longbridge 市场观察池对齐｜{as_of}

## 范围与授权

- 数据源：Longbridge CLI 的公开标的信息、完成日线、财报日历、美国宏观日历和标的新闻。
- 本次只读范围：{', '.join(symbols)}；未调用 Longbridge 账户、持仓、订单、成交、对账单或资金接口。
- 账户数据：本次未读取或同步任何账户持仓事实。
- 市场日线日期：{as_of}；EMA 历史样本起点：{history_start}。
- 事件/新闻保留窗口：{as_of} 至 {event_end}。Longbridge 的宏观接口可能超出 `--end` 返回，脚本按日期二次过滤。
- “695”标的：暂按 QQQ.US 做数值候选校验，尚未得到用户明确 ticker 确认。

## 日线与 EMA（公开市场事实）

| 标的 | 名称 | 开 | 高 | 低 | 收 | 日内变化 | EMA20 | EMA100 | 机械检查 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
{chr(10).join(table_rows) if table_rows else '| 无 | | | | | | | | | |'}

## 对用户口述点位的机械对照

- QQQ.US 候选：周五最高价是否落在 695 附近可由日线确认；日线 OHLC 不能证明“多次测试失败”，也不能证明你说的 705/710 是 50% 反弹或止损聚集区。
- VOO.US：收盘是否高于 685 可由日线直接检查；是否高于 20EMA 由上表 EMA 检查，未读取任何 VOO 持仓。
- SOXX.US：周五最高价是否进入 532–533 区间可由日线检查；冲高回落可由开/高/收的关系描述，但“相对指数更弱”需要选择比较基准和同一收益口径，不能仅凭单根 K 线断言。
- DRAM.US：本次只对齐价格与公开事件，不读取你的成本约 60，也不推断你的仓位；50% 反弹/多空分界点的具体数值仍待你提供或确认。

## 事件与新闻（事实层，不作因果归因）

### 财报与宏观日历

{chr(10).join(event_lines)}

### 新闻馈送（窗口内最多每标的三条）

{chr(10).join(news_lines)}

新闻标题是 Longbridge 新闻馈送的公开数据，不等于已验证的价格驱动事件；因果解释仍需单独核对原始公告/公司披露。

## 状态

- 已完成：{len(symbols)} 个公开标的的周五日线、EMA 机械计算、事件窗口过滤和新闻标题抓取。
- 待用户确认：695 对应的真实 ticker；是否把宏观事件/新闻纳入每日复盘；VOO/SOXX/DRAM 的比较基准与具体分界点。
- 未验证：任何账户持仓、成本、仓位、历史减仓计划执行情况；没有进行账户对账。
- 可选接口异常：
{optional_error_text}
"""


def main() -> int:
    args = parse_args()
    try:
        as_of_date = validate_date(args.date, "--date")
        history_start_date = validate_date(args.history_start, "--history-start") if args.history_start else as_of_date - timedelta(days=365)
        event_end_date = validate_date(args.event_end, "--event-end") if args.event_end else as_of_date
        if history_start_date > as_of_date:
            raise ValueError("--history-start must be on or before --date")
        if event_end_date < as_of_date:
            raise ValueError("--event-end must be on or after --date")
        if is_in_git_worktree(args.output):
            raise ValueError("--output must be outside a Git worktree")
        symbols = list(dict.fromkeys(symbol.upper() for symbol in args.symbols))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        base = args.longbridge_bin
        working_directory = args.output.parent
        static_rows = {
            string_value(item.get("symbol"), "").upper(): item
            for item in list_records(run_json([base, "static", *symbols, "--format", "json", "--lang", "zh-CN"], working_directory))
            if item.get("symbol")
        }
        bars = {
            symbol: market_rows(
                run_json(
                    [
                        base,
                        "kline",
                        "history",
                        symbol,
                        "--period",
                        "day",
                        "--start",
                        history_start_date.isoformat(),
                        "--end",
                        as_of_date.isoformat(),
                        "--format",
                        "json",
                    ],
                    working_directory,
                )
            )
            for symbol in symbols
        }

        optional_errors: list[str] = []
        try:
            earnings_response = run_json(
                [
                    base,
                    "finance-calendar",
                    "report",
                    *sum((["--symbol", symbol] for symbol in symbols), []),
                    "--market",
                    "US",
                    "--start",
                    as_of_date.isoformat(),
                    "--end",
                    event_end_date.isoformat(),
                    "--count",
                    "100",
                    "--format",
                    "json",
                    "--lang",
                    "zh-CN",
                ],
                working_directory,
            )
            earnings = [item for item in event_groups(earnings_response) if in_date_window(item.get("event_date"), as_of_date, event_end_date)]
        except RuntimeError as error:
            earnings = []
            optional_errors.append(f"财报日历：{error}")

        try:
            macro_response = run_json(
                [
                    base,
                    "finance-calendar",
                    "macrodata",
                    "--market",
                    "US",
                    "--start",
                    as_of_date.isoformat(),
                    "--end",
                    event_end_date.isoformat(),
                    "--count",
                    "100",
                    "--star",
                    "3",
                    "--format",
                    "json",
                    "--lang",
                    "zh-CN",
                ],
                working_directory,
            )
            macro = [item for item in event_groups(macro_response) if in_date_window(item.get("event_date"), as_of_date, event_end_date)]
        except RuntimeError as error:
            macro = []
            optional_errors.append(f"宏观日历：{error}")

        news: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            try:
                response = run_json([base, "news", symbol, "--count", "20", "--format", "json", "--lang", "zh-CN"], working_directory)
                news[symbol] = [
                    item
                    for item in list_records(response)
                    if in_date_window(string_value(item.get("published_at"), "")[:10], as_of_date, event_end_date)
                ]
            except RuntimeError as error:
                news[symbol] = []
                optional_errors.append(f"{symbol} 新闻：{error}")

        report = render_report(
            as_of_date.isoformat(),
            history_start_date.isoformat(),
            event_end_date.isoformat(),
            symbols,
            static_rows,
            bars,
            earnings,
            macro,
            news,
            optional_errors,
        )
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(args.output)
        print(f"PASS: wrote private market alignment to {args.output}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
