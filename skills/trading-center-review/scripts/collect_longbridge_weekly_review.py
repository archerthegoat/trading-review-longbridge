#!/usr/bin/env python3
"""Create an L1-only weekly review draft from authorized Longbridge CLI reads."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from private_runtime_io import PrivateRuntimeError, prepare_private_output, write_owner_only_text


US_EASTERN = ZoneInfo("America/New_York")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Period start, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Period end, YYYY-MM-DD")
    parser.add_argument("--output", required=True, type=Path, help="Private Markdown output path outside a Git worktree")
    parser.add_argument("--longbridge-bin", default="longbridge", help="Longbridge CLI executable")
    parser.add_argument("--dry-run", action="store_true", help="Print read-only commands without invoking Longbridge")
    return parser.parse_args()


def validate_dates(start: str, end: str) -> None:
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()
    if start_date > end_date:
        raise ValueError("--start must be on or before --end")


def us_eastern_window(start: str, end: str) -> tuple[str, str]:
    """Convert inclusive US-equity dates into an explicit UTC half-open window."""
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date() + timedelta(days=1)
    start_ts = datetime.combine(start_date, time.min, tzinfo=US_EASTERN)
    end_ts = datetime.combine(end_date, time.min, tzinfo=US_EASTERN)
    return (
        start_ts.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        end_ts.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
    )


def command_set(binary: str, start: str, end: str) -> dict[str, list[str]]:
    start_ts, end_ts = us_eastern_window(start, end)
    return {
        "positions": [binary, "positions", "--format", "json"],
        "orders": [binary, "order", "--history", "--start", start_ts, "--end", end_ts, "--format", "json"],
        "executions": [binary, "order", "executions", "--history", "--start", start_ts, "--end", end_ts, "--format", "json"],
    }


def run_json(command: list[str], working_directory: Path) -> object:
    result = subprocess.run(command, check=False, capture_output=True, text=True, cwd=working_directory)
    if result.returncode != 0:
        raise RuntimeError(f"Longbridge read failed ({command[1]}): exit {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Longbridge returned non-JSON for {command[1]}") from error


def string_value(value: object) -> str:
    if value is None or value == "":
        return "不可用"
    return str(value)


def as_records(value: object, source: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"Unexpected {source} response shape; expected an array of objects")
    return value


def render_report(start: str, end: str, results: dict[str, object]) -> str:
    start_ts, end_ts = us_eastern_window(start, end)
    positions = as_records(results["positions"], "positions")
    orders = as_records(results["orders"], "orders")
    executions = as_records(results["executions"], "executions")

    side_counts = Counter(string_value(item.get("side")) for item in orders)
    status_counts = Counter(string_value(item.get("status")) for item in orders)
    side_summary = "；".join(f"{name}: {count}" for name, count in sorted(side_counts.items())) or "无"
    status_summary = "；".join(f"{name}: {count}" for name, count in sorted(status_counts.items())) or "无"

    return f"""# Longbridge 周度持仓与操作汇总｜{start} 至 {end}

## 数据与授权状态

- 来源：Longbridge CLI 只读聚合
- 数据状态：partial_data（当前持仓为快照，不能还原期初持仓或完整周度变动）
- Wiki 记录级别：L1 聚合摘要；L2 标的、数量、成本、价格、订单/成交 ID 均未写入
- 查询窗口：订单/成交按美东（America/New_York）`{start}` 至 `{end}` 的半开日界查询，对应 UTC `{start_ts}` 至 `{end_ts}`；持仓为读取时快照
- 生成时间：{datetime.now().astimezone().isoformat(timespec="seconds")}

## 当前持仓与本周操作聚合

- 当前股票持仓条数：{len(positions)}（快照，不代表期初持仓或完整周度变动）
- 历史订单条数：{len(orders)}
- 订单方向计数：{side_summary}
- 订单状态计数：{status_summary}
- 历史成交条数：{len(executions)}

## 未验证或缺失

- 未读取资金、收益/盈亏、对账单、账户标识或凭据；本报告不能用于账户价值或收益变化结论。
- 当前持仓仅为读取时快照，不能单独推导本周起点持仓变化。
- 未写入任何标的、数量、成本、价格、订单 ID、成交 ID 或原始响应。

## 本周讨论输入

- 需要用户解释的计划变化：
- 需要核对的操作与规则：
- 后续分析只使用固定脱敏事实包，并遵守当前分析缓存与确认门。

## 下周待讨论

- 继承或修订的交易想法与周度计划版本：
- 待验证窗口、触发规则与失效条件：
"""


def main() -> int:
    args = parse_args()
    try:
        validate_dates(args.start, args.end)
        commands = command_set(args.longbridge_bin, args.start, args.end)
        if args.dry_run:
            for name, command in commands.items():
                print(f"{name}: {' '.join(command)}")
            return 0
        output_path = prepare_private_output(args.output)
        results = {name: run_json(command, output_path.parent) for name, command in commands.items()}
        report = render_report(args.start, args.end, results)
        write_owner_only_text(output_path, report)
        print(f"PASS: wrote L1-only review draft to {output_path}")
        return 0
    except (OSError, PrivateRuntimeError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
