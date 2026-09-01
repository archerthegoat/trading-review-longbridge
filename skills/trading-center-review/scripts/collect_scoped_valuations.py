#!/usr/bin/env python3
"""Read-only, explicitly scoped annual-ROE / PE collection. No peer discovery."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from private_runtime_io import prepare_private_output, write_owner_only_text
from render_trade_review_dashboard_v2 import validate_display_snapshot
from trading_review_valuation import calculate_pr, decimal, project_annual_roe, validate_valuation


def collect(display: dict, output: Path, *, funds: set[str]) -> dict:
    view = validate_display_snapshot(display)
    symbols = sorted({r["symbol"] for r in view["daily"]["positions_plans"]["items"] if r["tab"] in {"holdings", "plan"}})
    if not funds.issubset(symbols):
        raise ValueError("funds_must_be_in_the_explicit_scope")
    if not symbols:
        return {"schema_version": "scoped-valuations.v1", "items": []}
    prepare_private_output(output)
    observed = dt.datetime.now(dt.timezone.utc).isoformat()

    def query(key: str, args: list[str]):
        try:
            result = subprocess.run(["/usr/local/bin/longbridge", *args, "--format", "json"], cwd=output.parent, capture_output=True, text=True, timeout=40)
            write_owner_only_text(output.parent / "raw" / f"{key}.stdout", result.stdout)
            write_owner_only_text(output.parent / "raw" / f"{key}.stderr", result.stderr)
            if result.returncode:
                return None
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    companies = [s for s in symbols if s not in funds]
    pe_values = {}
    # A one-symbol compare auto-expands peers. Official calc-index maps `pe`
    # to PE (TTM) and accepts an explicit symbol list without peer discovery.
    # https://open.longbridge.com/docs/cli/market-data/calc-index
    for start in range(0, len(companies), 5):
        group = companies[start:start + 5]
        if len(group) == 1:
            raw = query(f"pe-{start}", ["calc-index", group[0], "--fields", "pe"])
            # Unknown result shapes are unavailable, not a guessed PE.
            rows = raw if isinstance(raw, list) else raw.get("list", []) if isinstance(raw, dict) else []
        else:
            raw = query(f"pe-{start}", ["compare", *group, "--currency", "USD"])
            rows = raw.get("list", []) if isinstance(raw, dict) else []
        for row in rows:
            if row.get("symbol") in group and isinstance(row.get("pe"), str):
                try:
                    decimal(row["pe"])
                    pe_values[row["symbol"]] = row["pe"]
                except ValueError:
                    pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        # CLI --latest switches to a latest-period summary (possibly H1/Q),
        # overriding the annual shape. Request af history and select a dated FY.
        annuals = dict(zip(companies, pool.map(lambda symbol: query(f"annual-{symbol}", ["financial-report", symbol, "--kind", "IS", "--report", "af"]), companies)))
    items = []
    for symbol in symbols:
        row = {"symbol": symbol, "instrument_type": "fund" if symbol in funds else "company", "as_of": observed, "pe_ttm": None,
               "roe_pct": None, "roe_period_end": None, "roe_period_label": None, "roe_basis": None, "roe_quality": "unverified",
               "pr": None, "status": "unavailable", "gap": "缺少可核对的年度估值数据", "source": "Longbridge"}
        if symbol in funds:
            row.update(status="not_applicable", roe_quality="not_applicable", gap="ETF 不适用企业市赚率")
        else:
            row["pe_ttm"] = pe_values.get(symbol)
            try:
                if annuals[symbol] is not None:
                    roe = project_annual_roe(annuals[symbol], symbol, observed)
                    if roe:
                        row.update(roe)
                if row["roe_quality"] == "nonpositive" or row["pe_ttm"] is not None and decimal(row["pe_ttm"]) <= 0:
                    row.update(status="not_applicable", gap="利润或权益回报非正，市赚率不适用")
                elif row["pe_ttm"] is not None and row["roe_quality"] == "positive_income_equity":
                    if (dt.datetime.fromisoformat(observed).date() - dt.date.fromisoformat(row["roe_period_end"])).days > 550:
                        row.update(status="stale", gap="可核对的年度报告已较旧")
                    else:
                        row.update(pr=calculate_pr(row["pe_ttm"], row["roe_pct"]), status="available", gap="")
            except (ValueError, KeyError, TypeError):
                row.update(pr=None, status="unavailable", gap="年度估值口径需进一步核对")
        items.append(validate_valuation(row))
    result = {"schema_version": "scoped-valuations.v1", "items": items}
    write_owner_only_text(output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fund", action="append", default=[])
    args = parser.parse_args()
    # Both private input and output must pass the same fixed runtime boundary.
    source = prepare_private_output(args.display_input)
    payload = json.loads(source.read_text(encoding="utf-8"))
    result = collect(payload, args.output, funds=set(args.fund))
    print(json.dumps({"status": "collected", "symbols": len(result["items"]), "available": sum(r["status"] == "available" for r in result["items"]), "raw_output": "private_only"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
