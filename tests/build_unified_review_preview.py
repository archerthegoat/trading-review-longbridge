#!/usr/bin/env python3
"""Build explicitly synthetic UI QA pages; never reads the real state database."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills" / "trading-center-review" / "scripts"))
from tests.test_render_trade_review_dashboard_v2 import fixture, weekly_packet, plan_detail
from tests.test_trading_review_state import weekly_v2_bundle
import render_trade_review_dashboard_v2 as renderer
import trading_review_state as state
from private_runtime_io import write_owner_only_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    template = (ROOT / "skills/trading-center-review/assets/trade-review-dashboard-v2-standalone.html").read_text()
    weekly = weekly_packet()
    weekly["meta"]["review_label"] = "周度执行 · 合成示例"
    bundle = weekly_v2_bundle({})
    metrics = state._normalize_execution_metrics(bundle["execution_metrics"], bundle["episode_assessments"], "$metrics")
    for name in ("coverage_rate", "execution_rate", "plan_win_rate"):
        metrics[name] = None if metrics[name] is None else float(metrics[name])
    weekly["execution_metrics"] = metrics
    weekly["review_episodes"] = [row for row in bundle["episode_assessments"] if renderer._episode_needs_review(row)]
    for name in ("plan_review", "next_week", "data_note"):
        weekly["sections"][name] = []
    daily = fixture("complete")
    daily["meta"]["review_label"] = "盘前复盘 · 合成示例（非真实行情）"
    rows = daily["positions_plans"]["items"]
    confirmed = plan_detail(status="confirmed", setup="pullback", actionable=True)
    confirmed["version"] = 2
    rows[0]["plan_detail"] = confirmed
    observation = plan_detail()
    observation["plan_id"] = "demo-bottom"
    bottom = copy.deepcopy(rows[0])
    bottom.update(symbol="BOTTOM.US", display_name="抄底观察示例", tab="plan", plan_detail=observation,
                  plan_coverage="仅观察，尚未确认", has_gap=True, gap="右侧确认条件未齐", near_trigger=False)
    rows.append(bottom)
    management = copy.deepcopy(rows[0])
    management.update(symbol="MANAGE.US", display_name="买入后管理示例", tab="plan",
                      plan_detail=plan_detail(stage="position_management", setup="position_management", actionable=True),
                      plan_coverage="加仓草案待单独确认", has_gap=True, gap="需单独确认加仓草案", near_trigger=True)
    rows.append(management)
    pages = {
        "trade-review-dashboard.html": (daily, weekly),
        "daily-only.html": (daily, None),
        "blocked-metrics.html": (daily, weekly_packet()),
    }
    managed = copy.deepcopy(daily)
    managed_row = managed["positions_plans"]["items"][-1]
    managed_row["plan_detail"]["plan_status"] = "confirmed"
    managed_row["plan_detail"]["version"] = 2
    managed_row.update(plan_coverage="已确认持仓管理计划（合成示例）", has_gap=False, gap="")
    pages["management-confirmed.html"] = (managed, weekly)
    quoted = copy.deepcopy(daily)
    quoted["positions_plans"]["items"][0]["plan_detail"]["quote_relation"] = "inside"
    pages["quote-inside.html"] = (quoted, weekly)
    for name in ("empty", "partial", "stale"):
        other = fixture(name)
        other["meta"]["review_label"] = "盘前复盘 · 合成示例（非真实行情）"
        increment = copy.deepcopy(weekly)
        if name == "stale":
            increment["meta"]["freshness"] = "stale"
        pages[name + ".html"] = (other, increment)
    for filename, (day, week) in pages.items():
        html = renderer.render_unified_dashboard(daily_packet=day, weekly_packet=week, template=template)
        write_owner_only_text(args.output_dir / filename, html)
    write_owner_only_text(args.output_dir / "daily-synthetic.json", json.dumps(daily, ensure_ascii=False, indent=2))
    write_owner_only_text(args.output_dir / "weekly-synthetic.json", json.dumps(weekly, ensure_ascii=False, indent=2))
    print(json.dumps({"status": "built", "data_kind": "synthetic_only", "pages": sorted(pages), "output_dir": str(args.output_dir)}))


if __name__ == "__main__":
    main()
