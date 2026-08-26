from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    ROOT
    / "skills"
    / "trading-center-review"
    / "scripts"
    / "render_trade_review_dashboard.py"
)
TEMPLATE_PATH = (
    ROOT
    / "skills"
    / "trading-center-review"
    / "assets"
    / "trade-review-dashboard-standalone.html"
)
SPEC = importlib.util.spec_from_file_location("render_trade_review_dashboard", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def dashboard_packet() -> dict[str, object]:
    return {
        "schema_version": "trading-review-dashboard.v1",
        "eyebrow": "交易研究中心 · 示例周",
        "title": "每日盘前与周度复盘",
        "subtitle": "事实、组合逻辑、标的计划和市场事件分栏呈现。",
        "badges": [
            {"label": "周度", "value": "示例窗口", "tone": "blue"},
            {"label": "权限", "value": "只读", "tone": "green"},
        ],
        "status": {
            "title": "已纳入 · 示例证据可用",
            "detail": "这是无真实账户数据的渲染测试。",
            "tone": "green",
        },
        "summary_cards": [
            {
                "kicker": "市场基调",
                "title": "示例组合判断",
                "text": "组合级逻辑不进入单一标的计划卡。",
                "tone": "blue",
            },
            {
                "kicker": "风险风格",
                "title": "控制集中暴露",
                "text": "这里只陈述整体风险管理方向。",
                "tone": "amber",
            },
        ],
        "summary_note": "示例判断未绑定真实账户。",
        "account": {
            "metrics": [
                {"label": "周期净变动", "value": "+100.00", "meta": "示例数字", "tone": "green"},
                {"label": "成交子行", "value": "2", "meta": "方向已返回", "tone": "neutral"},
            ],
            "evidence": [
                {"label": "证据边界", "value": "示例机械聚合；不包含资金流水"},
                {"label": "损益归属", "value": "每个 ticker 独立归属"},
            ],
            "note": "缺失字段不补成事实。",
            "pnl": [
                {"symbol": "DEMOA", "value": 125.5},
                {"symbol": "DEMOB", "value": -25.5},
            ],
            "pnl_note": "示例合计 +100.00。",
        },
        "review_cards": [
            {
                "kicker": "执行纪律",
                "title": "按计划处理",
                "text": "复盘执行与计划之间的差异。",
                "meta": ["示例标签"],
                "tone": "amber",
            }
        ],
        "plan_callout": "候选席位和持仓角色保持分开。",
        "plans": [
            {
                "symbol": "DEMOA",
                "name": "示例标的计划",
                "subtitle": "观察对象",
                "state": "观察",
                "state_tone": "amber",
                "open": True,
                "blocks": [
                    {"label": "触发", "value": "示例确认条件", "full": False},
                    {"label": "失效", "value": "示例失效条件", "full": False},
                    {"label": "边界", "value": "计划不等于执行", "full": True},
                ],
            }
        ],
        "excluded": [{"symbol": "DEMOB", "reason": "不在当前观察列表"}],
        "event_groups": [
            {
                "label": "下周",
                "range": "示例日期",
                "events": [
                    {
                        "date": "周三",
                        "time": "20:30 / 08:30 ET",
                        "title": "示例宏观数据",
                        "meta": "与示例持仓相关的美国宏观事件",
                        "kind": "macro",
                        "tag": "宏观 · 预期",
                        "source": "公开日历示例",
                        "status": "预期",
                        "impact": "利率预期与风险偏好",
                        "open": True,
                    }
                ],
            }
        ],
        "event_note": "事件不构成交易计划。",
        "footer": "无真实账户、持仓、成交或计划数据。",
    }


class DashboardRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_template_is_direct_standalone_document(self) -> None:
        self.assertEqual(self.template.count(MODULE.BODY_MARKER), 1)
        self.assertIn("<!doctype html>", self.template.lower())
        self.assertNotIn("<iframe", self.template.lower())
        self.assertNotIn("document.write", self.template)
        self.assertNotIn("<script", self.template.lower())
        self.assertNotIn("https://", self.template)

    def test_render_preserves_required_information_order(self) -> None:
        rendered = MODULE.render_dashboard(dashboard_packet(), self.template)

        summary_index = rendered.index("交易风格与整体逻辑")
        evidence_index = rendered.index("账户与交易证据")
        pnl_index = rendered.index("成交与损益概览")
        reviews_index = rendered.index("本周损益与执行复盘")
        plans_index = rendered.index("当前有效的标的交易计划")
        events_index = rendered.index("持仓与计划相关重要事件")

        self.assertLess(summary_index, evidence_index)
        self.assertLess(evidence_index, pnl_index)
        self.assertLess(pnl_index, reviews_index)
        self.assertLess(reviews_index, plans_index)
        self.assertLess(plans_index, events_index)
        self.assertNotIn("资金流水", rendered[plans_index:events_index])
        self.assertIn("全量日历私存；只展示相关美股财报与明确风险通道", rendered)
        self.assertNotIn("不按持仓池替代事件筛选", rendered)

    def test_daily_layout_uses_holdings_and_plan_tabs(self) -> None:
        packet = dashboard_packet()
        packet["summary_cards"] = []
        packet["summary_note"] = ""
        packet["review_cards"] = []
        packet["account"]["pnl"] = []  # type: ignore[index]
        packet["plans"][0]["tab"] = "holdings"  # type: ignore[index]
        packet["plans"].append(  # type: ignore[union-attr]
            {
                "symbol": "DEMOC",
                "tab": "plan",
                "name": "示例 Plan",
                "subtitle": "抄底",
                "state": "观察",
                "state_tone": "blue",
                "open": False,
                "blocks": [
                    {"label": "边界", "value": "计划不等于执行", "full": True},
                ],
            }
        )

        rendered = MODULE.render_dashboard(packet, self.template)

        self.assertNotIn("交易风格与整体逻辑", rendered)
        self.assertNotIn("本周损益与执行复盘", rendered)
        self.assertNotIn("当前有效的标的交易计划", rendered)
        self.assertIn("交易计划", rendered)
        self.assertIn("当前持仓", rendered)
        self.assertIn(">Plan<", rendered)
        self.assertLess(rendered.index("账户与交易证据"), rendered.index("交易计划"))
        self.assertLess(rendered.index("交易计划"), rendered.index("持仓与计划相关重要事件"))
        self.assertIn("trc-plan-tab-panel", rendered)

    def test_operations_first_layout_hides_account_backend_section(self) -> None:
        packet = dashboard_packet()
        packet["dashboard_mode"] = "operations-first"
        packet["summary_cards"] = [
            {
                "kicker": "用户确认",
                "title": "示例昨日操作",
                "text": "按计划完成一项操作，执行证据边界保留在摘要脚注。",
                "tone": "amber",
            }
        ]
        packet["summary_note"] = "昨日窗口与快照净变化的最小边界。"
        packet["review_cards"] = []
        packet["plans"][0]["tab"] = "holdings"  # type: ignore[index]
        packet["plans"].append(  # type: ignore[union-attr]
            {
                "symbol": "DEMOC",
                "tab": "plan",
                "name": "示例 Plan",
                "subtitle": "观察",
                "state": "有效",
                "state_tone": "blue",
                "open": False,
                "blocks": [
                    {"label": "边界", "value": "计划不等于执行", "full": True},
                ],
            }
        )

        rendered = MODULE.render_dashboard(packet, self.template)

        self.assertIn("昨日操作摘要", rendered)
        self.assertNotIn("账户与交易证据", rendered)
        self.assertNotIn("成交与损益概览", rendered)
        self.assertNotIn("本周损益与执行复盘", rendered)
        self.assertIn('class="trc-operations-list"', rendered)
        self.assertNotIn('class="trc-operation-kicker"', rendered)
        self.assertEqual(rendered.count('class="trc-summary-card'), 0)
        self.assertLess(rendered.index("昨日操作摘要"), rendered.index("交易计划"))
        self.assertLess(rendered.index("交易计划"), rendered.index("持仓与计划相关重要事件"))
        self.assertIn("当前持仓", rendered)
        self.assertIn(">Plan<", rendered)

    def test_operations_first_focus_follows_events(self) -> None:
        packet = dashboard_packet()
        packet["dashboard_mode"] = "operations-first"
        packet["summary_cards"] = [
            {
                "kicker": "操作",
                "title": "示例昨日操作",
                "text": "只记录已确认的操作摘要。",
                "tone": "amber",
            }
        ]
        packet["review_cards"] = []
        packet["plans"][0]["tab"] = "holdings"  # type: ignore[index]
        packet["focus_items"] = [
            {"title": "当天窗口", "text": "只沿用已确认事件。", "tone": "blue"},
            {"title": "数据缺口", "text": "执行映射仍待核验。", "tone": "amber"},
        ]
        packet["focus_note"] = "确认版本边界。"

        rendered = MODULE.render_dashboard(packet, self.template)

        self.assertIn("Wiki 写入后的盘中关注点", rendered)
        self.assertIn("当天窗口", rendered)
        self.assertIn("确认版本边界。", rendered)
        self.assertLess(rendered.index("持仓与计划相关重要事件"), rendered.index("Wiki 写入后的盘中关注点"))
        self.assertNotIn('class="trc-operation-kicker"', rendered)

    def test_render_computes_symbol_level_pnl_and_bar_widths(self) -> None:
        rendered = MODULE.render_dashboard(dashboard_packet(), self.template)

        self.assertIn("+125.50", rendered)
        self.assertIn("−25.50", rendered)
        self.assertIn("width:100%", rendered)
        self.assertIn("width:20%", rendered)
        self.assertNotIn("或其他", rendered)

    def test_render_escapes_untrusted_text(self) -> None:
        packet = dashboard_packet()
        packet["subtitle"] = '<script>alert("private")</script>'

        rendered = MODULE.render_dashboard(packet, self.template)

        self.assertNotIn('<script>alert("private")</script>', rendered)
        self.assertIn("&lt;script&gt;alert(&quot;private&quot;)&lt;/script&gt;", rendered)

    def test_unknown_fields_are_rejected(self) -> None:
        packet = dashboard_packet()
        packet["account"]["cash_flow"] = []  # type: ignore[index]

        with self.assertRaisesRegex(MODULE.DashboardRenderError, "unsupported field"):
            MODULE.render_dashboard(packet, self.template)

    def test_plan_without_ticker_is_rejected(self) -> None:
        packet = dashboard_packet()
        del packet["plans"][0]["symbol"]  # type: ignore[index]

        with self.assertRaisesRegex(MODULE.DashboardRenderError, "must be a string"):
            MODULE.render_dashboard(packet, self.template)

    def test_real_review_values_are_not_bundled_in_template(self) -> None:
        for private_value in (
            "78,925.76",
            "1,359.62",
            "GOOGL",
            "AAPL",
            "MRVL",
            "Longbridge News Search",
        ):
            self.assertNotIn(private_value, self.template)

    def test_cli_writes_owner_only_private_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            input_path = directory_path / "dashboard.json"
            output_path = directory_path / "dashboard.html"
            input_path.write_text(json.dumps(dashboard_packet(), ensure_ascii=False), encoding="utf-8")

            exit_code = MODULE.main(["--input", str(input_path), "--output", str(output_path)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertIn("示例标的计划", output_path.read_text(encoding="utf-8"))

    def test_cli_rejects_private_input_inside_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "dashboard.html"

            exit_code = MODULE.main(
                ["--input", str(ROOT / "private-dashboard.json"), "--output", str(output_path)]
            )

            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())

    def test_cli_rejects_private_output_inside_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "dashboard.json"
            input_path.write_text(json.dumps(dashboard_packet(), ensure_ascii=False), encoding="utf-8")

            exit_code = MODULE.main(
                ["--input", str(input_path), "--output", str(ROOT / "private-dashboard.html")]
            )

            self.assertEqual(exit_code, 2)
            self.assertFalse((ROOT / "private-dashboard.html").exists())

    def test_cli_rejects_nonexistent_output_directory_inside_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "dashboard.json"
            input_path.write_text(json.dumps(dashboard_packet(), ensure_ascii=False), encoding="utf-8")
            output_path = ROOT / "not-created-private-output" / "dashboard.html"

            exit_code = MODULE.main(["--input", str(input_path), "--output", str(output_path)])

            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())
            self.assertFalse(output_path.parent.exists())


if __name__ == "__main__":
    unittest.main()
