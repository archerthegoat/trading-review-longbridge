from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "trading-center-review"
    / "scripts"
    / "align_market_watchlist.py"
)
SPEC = importlib.util.spec_from_file_location("align_market_watchlist", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

VALIDATOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "trading-center-review"
    / "scripts"
    / "validate_review_draft.py"
)
VALIDATOR_SPEC = importlib.util.spec_from_file_location("validate_review_draft", VALIDATOR_PATH)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)


class EventParsingTests(unittest.TestCase):
    def test_event_groups_unwraps_longbridge_data_list(self) -> None:
        response = {
            "code": 0,
            "data": {
                "date": "2026-08-12",
                "list": [
                    {
                        "date": "2026-08-12",
                        "infos": [
                            {
                                "content": "美国, CPI",
                                "date": "20:30",
                                "type": "macrodata",
                            }
                        ],
                    }
                ],
            },
        }

        events = MODULE.event_groups(response)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_date"], "2026-08-12")
        self.assertEqual(events[0]["content"], "美国, CPI")

    def test_render_report_keeps_verified_event_visible(self) -> None:
        response = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "date": "2026-08-12",
                        "infos": [{"content": "美国, CPI", "date": "20:30"}],
                    }
                ]
            },
        }
        macro = MODULE.event_groups(response)

        report = MODULE.render_report(
            as_of="2026-08-12",
            history_start="2025-08-12",
            event_end="2026-08-12",
            symbols=["QQQ.US"],
            static_rows={},
            bars={"QQQ.US": []},
            earnings=[],
            macro=macro,
            news={"QQQ.US": []},
            optional_errors=[],
        )

        self.assertIn("美国, CPI", report)
        self.assertNotIn("该日期窗口未返回财报或 3 星宏观事件", report)

    def test_review_validator_rejects_daily_draft_without_event_table(self) -> None:
        draft = """# 交易中心每日盘前复盘 · 2026-08-11

## 数据与授权状态
已完成；本周计划已读取；下周待确认。

## 当天交易日重要事件
事件缺失。

## 下一美股交易日重要事件
事件缺失。
"""

        errors = VALIDATOR.validate_draft_text(draft)

        self.assertTrue(any("missing event table header" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
