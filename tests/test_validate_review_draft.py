from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "skills" / "trading-center-review" / "scripts" / "validate_review_draft.py"
SPEC = importlib.util.spec_from_file_location("validate_review_draft", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load review draft validator")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


EVENT_HEADER = "| Asia/Shanghai 时间 | 美东时间 | 事件 | 状态 | 来源与数据状态 |"


def daily_draft(event_row: str = "| 2026-08-29 20:30 | 2026-08-29 08:30 | 相关事件 | 已发生 | 来源 · complete |") -> str:
    return "\n".join(
        (
            "# 交易中心每日盘前复盘 · 2026-08-29",
            "## 数据与授权状态",
            "Longbridge 只读窗口已确认。",
            "## 复盘阶段",
            "每日盘前。",
            "## 前一美股交易日订单与成交",
            "订单与成交只覆盖已确认窗口。",
            "## 昨日参考持仓与当前持仓",
            "当前持仓是读取时快照。",
            "## 快照净变化",
            "| 项目 | 昨日参考 | 当前快照 | 净变化 | 证据与限制 |",
            "| --- | --- | --- | --- | --- |",
            "| 组合摘要 | 仅作参考 | 当前快照 | — | 非事件表 |",
            "## 当天交易日重要事件",
            EVENT_HEADER,
            event_row,
            "## 下一美股交易日重要事件",
            EVENT_HEADER,
            "| 2026-08-30 20:30 | 2026-08-30 08:30 | 无已确认事件（相关筛选后） | 已发生 | 相关筛选已完成并返回空 |",
            "## 事件对当前持仓/计划的主要影响",
            "| 事件 | 影响对象/风险通道 | 主要影响 | 证据与边界 |",
            "| --- | --- | --- | --- |",
            "| 相关事件 | 组合 | 条件式风险 | 只基于已确认事实 |",
            "## 当周最新计划与计划 vs 实际",
            "已确认计划与实际已对照。",
            "## 过程复盘",
            "事实、解释与缺口分开。",
            "## 明日缺口与行动",
            "保留条件式检查。",
            "## Wiki 写入分类与确认门",
            "当前 run 未确认，不写入。",
            "## 最终状态",
            "数据状态：complete。",
        )
    )


class ReviewDraftValidatorTests(unittest.TestCase):
    def test_complete_daily_draft_without_unverified_marker_passes(self):
        errors = MODULE.validate_draft_text(daily_draft())
        self.assertEqual(errors, [])

    def test_missing_event_section_fails_closed(self):
        text = "\n".join(
            (
                "# 交易中心每日盘前复盘 · 2026-08-29",
                "## 数据与授权状态",
                "## 本周计划",
                "## 下周计划",
            )
        )
        errors = MODULE.validate_draft_text(text)
        self.assertTrue(any("event" in error for error in errors))

    def test_non_empty_announcement_cannot_be_marked_as_expected(self):
        row = "| 2026-08-29 20:30 | 2026-08-29 08:30 | 相关事件；公告：1.0 | 预期 | 来源 · complete |"
        errors = MODULE.validate_draft_text(daily_draft(row))
        self.assertTrue(any("announcement" in error for error in errors))

    def test_missing_event_impact_summary_fails_closed(self):
        text = daily_draft().replace(
            "## 事件对当前持仓/计划的主要影响",
            "## 事件影响摘要缺失",
        )
        errors = MODULE.validate_draft_text(text)
        self.assertTrue(any("event impact summary" in error for error in errors))

    def test_sensitive_credential_pattern_fails(self):
        text = daily_draft().replace("Longbridge 只读窗口已确认。", "Authorization: Bearer synthetic-value")
        errors = MODULE.validate_draft_text(text)
        self.assertTrue(any("credential" in error for error in errors))

    def test_success_empty_event_row_requires_filter_completion_semantics(self):
        valid = daily_draft(
            "|  |  | 无已确认事件（相关筛选后） | 已发生 | 相关筛选已完成并返回空 |"
        )
        self.assertEqual(MODULE.validate_draft_text(valid), [])

        wrong_status = daily_draft(
            "|  |  | 无已确认事件（相关筛选后） | 未公布 | 相关筛选已完成并返回空 |"
        )
        self.assertTrue(
            any("success-empty event row" in error for error in MODULE.validate_draft_text(wrong_status))
        )

        wrong_source = daily_draft(
            "|  |  | 无已确认事件（相关筛选后） | 已发生 | 相关筛选成功为空 |"
        )
        self.assertTrue(
            any("success-empty event row" in error for error in MODULE.validate_draft_text(wrong_source))
        )

        source_without_empty_title = daily_draft(
            "|  |  | 相关事件 | 已发生 | 相关筛选已完成并返回空 |"
        )
        self.assertTrue(
            any(
                "exact empty event name" in error
                for error in MODULE.validate_draft_text(source_without_empty_title)
            )
        )

    def test_sensitive_value_variants_fail_closed(self):
        variants = (
            "Authorization=synthetic",
            "Authorization：synthetic",
            "access_token",
            "refresh_token",
            "client_secret",
            "api_key",
            "账户编号：synthetic",
            "账户标识＝synthetic",
            "订单ID: synthetic",
            "成交ID＝synthetic",
            "凭据：synthetic",
            "Bearer token",
        )
        for value in variants:
            text = daily_draft().replace("Longbridge 只读窗口已确认。", value)
            with self.subTest(value=value):
                self.assertTrue(
                    any("credential" in error for error in MODULE.validate_draft_text(text))
                )

    def test_normal_codex_product_text_is_not_a_sensitive_value(self):
        text = daily_draft().replace("Longbridge 只读窗口已确认。", "Codex 判断已按事实与条件式检查分开。")
        self.assertEqual(MODULE.validate_draft_text(text), [])


if __name__ == "__main__":
    unittest.main()
