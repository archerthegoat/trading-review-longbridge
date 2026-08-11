#!/usr/bin/env python3
"""Create a private read-only Longbridge current-positions snapshot.

The raw Longbridge response is parsed in memory and only an allow-listed private
Markdown summary is written outside the Git worktree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_FIELDS = ("symbol", "name", "market", "quantity", "available", "currency", "cost_price")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Private Markdown output outside a Git worktree")
    parser.add_argument("--longbridge-bin", default="longbridge", help="Longbridge CLI executable")
    return parser.parse_args()


def is_in_git_worktree(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in (resolved.parent, *resolved.parents))


def run_json(binary: str, cwd: Path) -> object:
    command = [binary, "positions", "--format", "json"]
    result = subprocess.run(command, check=False, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "no stderr"
        raise RuntimeError(f"Longbridge positions read failed: exit {result.returncode}; {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Longbridge positions returned non-JSON") from error


def records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError("Unexpected positions response shape; expected an array of objects")
    return value


def shown(value: object) -> str:
    return "不可用" if value is None or value == "" else str(value)


def render_report(items: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for item in items:
        rows.append(
            "| "
            + " | ".join(shown(item.get(field)).replace("|", "\\|") for field in ALLOWED_FIELDS)
            + " |"
        )
    table = "\n".join(rows) if rows else "| 无 | 无 | 无 | 无 | 无 | 无 | 无 |"
    symbols = ", ".join(shown(item.get("symbol")) for item in items) or "无"
    return f"""# Longbridge 当前持仓只读同步｜{datetime.now().astimezone().isoformat(timespec="seconds")}

## 数据与边界

- 来源：Longbridge CLI `positions --format json`。
- 本次读取：当前 Longbridge 持仓快照。
- 未调用：Longbridge 订单、成交、资金、利润、对账单或交易接口。
- 输出位置：Git 工作树外的私有目录；不写入飞书 Wiki。
- 数据语义：读取时快照，不代表历史持仓、期初持仓或账户完整性。

## 当前标的范围

- 快照标的：{symbols}
- 持仓条数：{len(items)}

## 私有明细（不得复制进 Git/Wiki）

| symbol | name | market | quantity | available | currency | cost_price |
| --- | --- | --- | ---: | ---: | --- | ---: |
{table}

## 复盘映射待确认

- 用户口述的别名、基准标的或计划价格，不自动覆盖 Longbridge 返回的真实 symbol。
- 计划与持仓的对照需要单独记录为“用户陈述 vs. CLI 快照”，不能把当前快照解释为计划已执行。
"""


def main() -> int:
    args = parse_args()
    try:
        if is_in_git_worktree(args.output):
            raise ValueError("--output must be outside a Git worktree")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        items = records(run_json(args.longbridge_bin, args.output.parent))
        report = render_report(items)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(args.output)
        print(f"PASS: synced {len(items)} Longbridge positions to private report")
        print(f"PRIVATE_REPORT: {args.output}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
