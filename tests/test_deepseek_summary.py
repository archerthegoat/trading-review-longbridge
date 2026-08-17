from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "trading-center-review"
    / "scripts"
    / "deepseek_summary.py"
)
SPEC = importlib.util.spec_from_file_location("deepseek_summary", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fact_packet() -> dict[str, object]:
    return {
        "schema_version": "trading-center-summary.v1",
        "as_of": "2026-08-13",
        "timezone": "Asia/Shanghai",
        "data_status": "已完成；事件状态按公告字段机械判定",
        "authorization_status": "本次为已授权的只读窗口",
        "positions": [
            {"symbol": "MSFT.US", "name": "Microsoft", "quantity": 10, "price": 486.0},
        ],
        "executions": [
            {"symbol": "NOW.US", "side": "卖出", "quantity": 10, "price": 900.0, "date": "2026-08-12"},
        ],
        "events": [
            {"event": "CPI", "status": "已发生", "actual": "3.1", "source": "机械读取"},
        ],
        "plan_items": [
            {"symbol": "MSFT.US", "role": "软件概念持有", "plan_status": "观察"},
        ],
    }


class DeepSeekSummaryTests(unittest.TestCase):
    def test_sanitize_projects_only_allowlisted_record_fields(self) -> None:
        payload = fact_packet()
        payload["positions"] = [{"symbol": "MSFT.US", "quantity": 10, "internal_debug": "drop me"}]

        sanitized = MODULE.sanitize_payload(payload)

        self.assertEqual(sanitized["positions"], [{"symbol": "MSFT.US", "quantity": 10}])

    def test_sanitize_rejects_forbidden_fields_before_network_call(self) -> None:
        payload = fact_packet()
        payload["executions"] = [{"symbol": "NOW.US", "order_id": "private-id"}]

        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "forbidden field"):
            MODULE.sanitize_payload(payload)

    def test_sanitize_rejects_camel_case_sensitive_fields(self) -> None:
        payload = fact_packet()
        payload["executions"] = [{"symbol": "NOW.US", "orderId": "private-id"}]

        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "forbidden field"):
            MODULE.sanitize_payload(payload)

    def test_sanitize_rejects_unknown_top_level_fields(self) -> None:
        payload = fact_packet()
        payload["free_text"] = "not part of the schema"

        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "unsupported top-level field"):
            MODULE.sanitize_payload(payload)

    def test_sanitize_rejects_private_identifier_in_fact_field(self) -> None:
        payload = fact_packet()
        payload["data_status"] = "order_id=private-id"

        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "sensitive value"):
            MODULE.sanitize_payload(payload)

    def test_summarize_requires_api_key(self) -> None:
        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "DEEPSEEK_API_KEY"):
            MODULE.summarize(fact_packet(), api_key="")

    def test_build_request_disables_thinking_for_summary_role(self) -> None:
        request = MODULE.build_request(MODULE.sanitize_payload(fact_packet()), "deepseek-v4-flash")

        self.assertEqual(request["model"], "deepseek-v4-flash")
        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertEqual(request["response_format"], {"type": "json_object"})
        system_content = request["messages"][0]["content"]
        self.assertIn('"facts":["string"]', system_content)
        self.assertIn("其余四个字段必须是只含字符串的 JSON 数组", system_content)
        user_content = request["messages"][1]["content"]
        self.assertNotIn("order_id", user_content)

    def test_summarize_accepts_only_expected_json_response(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "NOW 已从执行记录中移除；MSFT 保留为观察对象。",
                                "facts": ["NOW 卖出记录已提供"],
                                "position_impact": ["软件暴露集中到 MSFT 的计划事实"],
                                "event_impact": ["CPI 已发生，实际值来自输入"],
                                "unresolved": [],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

        with patch.object(MODULE, "_call_api", return_value=MODULE._parse_summary_response(response)):
            result = MODULE.summarize(fact_packet(), api_key="test-key")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["summary"]["unresolved"], [])

    def test_summary_output_rejects_private_identifier(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "order_id=private-id",
                                "facts": [],
                                "position_impact": [],
                                "event_impact": [],
                                "unresolved": [],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "sensitive value"):
            MODULE._parse_summary_response(response)

    def test_summary_output_rejects_extra_fields(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "结构正确但带有额外字段。",
                                "facts": [],
                                "position_impact": [],
                                "event_impact": [],
                                "unresolved": [],
                                "trade_instruction": "买入",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "unsupported fields"):
            MODULE._parse_summary_response(response)

    def test_write_result_rejects_project_path(self) -> None:
        with self.assertRaisesRegex(MODULE.DeepSeekSummaryError, "outside the Git worktree"):
            MODULE.write_result(Path.cwd() / "deepseek-result.json", {"status": "completed"})

    def test_cli_rejects_input_inside_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "output.json"

            exit_code = MODULE.main(
                ["--input", str(Path.cwd() / "never-read.json"), "--output", str(output_path)]
            )

            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())

    def test_cli_without_key_is_blocked_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            input_path = directory_path / "input.json"
            output_path = directory_path / "output.json"
            input_path.write_text(json.dumps(fact_packet(), ensure_ascii=False), encoding="utf-8")

            with patch.dict(MODULE.os.environ, {}, clear=True):
                exit_code = MODULE.main(["--input", str(input_path), "--output", str(output_path)])

            self.assertEqual(exit_code, 2)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
