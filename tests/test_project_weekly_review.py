from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "trading-center-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))

STATE_SPEC = importlib.util.spec_from_file_location(
    "trading_review_state", SCRIPTS / "trading_review_state.py"
)
if STATE_SPEC is None or STATE_SPEC.loader is None:
    raise RuntimeError("could not load trading_review_state")
STATE = importlib.util.module_from_spec(STATE_SPEC)
STATE_SPEC.loader.exec_module(STATE)
sys.modules["trading_review_state"] = STATE

PROJECT_SPEC = importlib.util.spec_from_file_location(
    "project_weekly_review", SCRIPTS / "project_weekly_review.py"
)
if PROJECT_SPEC is None or PROJECT_SPEC.loader is None:
    raise RuntimeError("could not load project_weekly_review")
PROJECT = importlib.util.module_from_spec(PROJECT_SPEC)
PROJECT_SPEC.loader.exec_module(PROJECT)


GENERATED_AT = "2026-08-30T08:00:00+08:00"


def trade_row(quantity: str = "1"):
    return {
        "market_date": "2026-08-28",
        "symbol": "DEMO.US:OPTION",
        "side": "buy",
        "order_count": 1,
        "execution_count": 1,
        "executed_quantity": quantity,
        "data_status": "complete",
    }


def private_facts():
    return {
        "schema_version": "trading-review-weekly-private-facts.v1",
        "run_id": "weekly-run",
        "generated_at": GENERATED_AT,
        "authorization": {"status": "confirmed_read_only", "prohibitions": []},
        "source": {
            "provider": "Longbridge",
            "cli_version": "1.0",
            "contract_version": "source.v1",
        },
        "period": {
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "timezone": "America/New_York",
            "utc_start": "2026-08-28T04:00:00Z",
            "utc_end": "2026-08-29T04:00:00Z",
            "expected_trade_dates": ["2026-08-28"],
        },
        "account_current": {
            "status": "partial",
            "snapshot_at": GENERATED_AT,
            "currency": "USD",
            "total_asset": "77777.77",
            "total_cash": "11111.11",
            "buying_power": "22222.22",
        },
        "positions_current": {
            "status": "complete",
            "rows": [
                {
                    "snapshot_at": GENERATED_AT,
                    "symbol": "DEMO.US:OPTION",
                    "underlying": "DEMO.US",
                    "instrument_type": "option",
                    "quantity": "1",
                    "data_status": "complete",
                }
            ],
        },
        "trades": {
            "status": "complete",
            "daily": [
                {
                    "market_date": "2026-08-28",
                    "status": "complete",
                    "rows": [trade_row()],
                    "order_count": 1,
                    "execution_count": 1,
                    "duplicate_execution_row_count": 0,
                }
            ],
        },
        "profit_analysis": {
            "status": "complete",
            "currency": "USD",
            "initial_asset_value": "1000",
            "ending_asset_value": "980",
            "sum_profit": "-20",
            "sum_profit_rate": "-2",
            "time_weighted_return": "-2",
            "invest_amount": "0",
            "mechanical_asset_change": "-20",
            "mechanical_reconciliation_residual": "0",
            "requested_utc_start": "2026-08-28T04:00:00Z",
            "requested_utc_end": "2026-08-29T04:00:00Z",
            "returned_utc_start": "2026-08-28T04:00:00Z",
            "returned_utc_end": "2026-08-29T04:00:00Z",
        },
        "profit_analysis_by_market": {
            "status": "partial",
            "currency": "USD",
            "requested_utc_start": "2026-08-28T04:00:00Z",
            "requested_utc_end": "2026-08-29T04:00:00Z",
            "returned_utc_start": "2026-08-28T04:00:00Z",
            "returned_utc_end": "2026-08-29T00:00:00Z",
            "rows": [
                {
                    "symbol": "DEMO.US",
                    "underlying": "DEMO.US",
                    "name": "示例标的",
                    "profit": "-20",
                    "underlying_profit": "-15",
                    "derivatives_profit": "-5",
                }
            ],
        },
        "cash_flow": {
            "status": "partial",
            "requested_utc_start": "2026-08-28T04:00:00Z",
            "requested_utc_end": "2026-08-29T04:00:00Z",
            "groups": [
                {
                    "business_type": "Unknown",
                    "flow_name": "Option Purchase Transaction",
                    "currency": "USD",
                    "balance": "-100",
                    "count": 1,
                },
                {
                    "business_type": "Unknown",
                    "flow_name": "IPO Financing Amount(CR)",
                    "currency": "HKD",
                    "balance": "200",
                    "count": 1,
                },
                {
                    "business_type": "Unknown",
                    "flow_name": "IPO Financing Amount(DR)",
                    "currency": "HKD",
                    "balance": "-200",
                    "count": 1,
                },
            ],
        },
        "market": {
            "status": "partial",
            "market_temperature": {
                "status": "complete",
                "rows": [
                    {"temperature": "55", "sentiment": "40", "valuation": "70"},
                    {"temperature": "54", "sentiment": "39", "valuation": "69"},
                ],
            },
            "quotes": {
                "status": "partial",
                "rows": [{"symbol": "QQQ.US", "last": "500", "change_pct": "-1"}],
            },
            "qqq_capital_snapshot": {
                "status": "complete",
                "boundary": "仅为标的级字段，不代表全市场资金流",
            },
        },
        "events_next_week": {
            "status": "partial",
            "macro_star3": {
                "rows": [
                    {
                        "shanghai_at": "2026-09-01T20:30:00+08:00",
                        "title": "示例宏观事件",
                        "impact_channel": "增长与利率预期",
                        "data_status": "complete",
                    }
                ]
            },
            "earnings": {"status": "partial", "gap": "目标周持仓财报覆盖不完整"},
        },
        "plan": {
            "status": "blocked",
            "reason": "未找到已确认计划",
            "boundary": "不从持仓或成交反推计划",
        },
        "known_gaps": ["计划权威缺失", "报价主来源时间缺失"],
        "overall_data_status": "partial",
    }


class WeeklyProjectorTests(unittest.TestCase):
    def test_successful_empty_trades_and_positions_are_not_blocked_or_fabricated(self):
        facts = private_facts()
        facts["trades"]["status"] = "empty"
        facts["trades"]["daily"][0].update(status="empty", rows=[], order_count=0, execution_count=0)
        facts["positions_current"].update(status="empty", rows=[])
        result = PROJECT.project_weekly_state(facts, None)
        modules = {row["name"]: row["status"] for row in result["modules"]}
        self.assertEqual(modules["trades"], "empty")
        self.assertEqual(modules["positions"], "empty")
        self.assertEqual(result["execution_metrics"]["data_status"], "blocked")

    def test_v2_private_input_excludes_account_and_pnl_modules(self):
        facts = private_facts()
        facts["schema_version"] = PROJECT.PRIVATE_FACTS_SCHEMA
        for key in ("account_current", "profit_analysis", "profit_analysis_by_market", "cash_flow"):
            facts.pop(key)
        result = PROJECT.project_weekly_state(facts, None)
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["schema_version"], "trading-review-weekly-state.v2")
        self.assertIsNone(result["performance"])
        self.assertEqual(result["attributions"], [])
        self.assertEqual(result["cash_flow_aggregates"], [])
        self.assertNotIn("initial_asset_value", encoded)
        self.assertNotIn("time_weighted_return", encoded)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="weekly-projector-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.db_path = self.root / "review.sqlite3"
        self.store = STATE.open_state_store(self.db_path, test_root=self.root)
        self.store.ingest_partition(
            dataset="trades",
            period_start="2026-08-28",
            period_end="2026-08-28",
            contract_version="source.v1:trades",
            status="complete",
            collected_at=GENERATED_AT,
            payload=[trade_row()],
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_projects_underlying_only_and_omits_private_account_totals(self):
        projected = PROJECT.project_weekly_state(private_facts(), self.store)
        encoded = json.dumps(projected, ensure_ascii=False)
        self.assertEqual(projected["schema_version"], PROJECT.STATE_SCHEMA)
        self.assertEqual(len(projected["dependencies"]), 1)
        self.assertEqual(projected["dependencies"][0]["dataset"], "trades")
        self.assertNotIn(":OPTION", encoded)
        self.assertNotIn("77777.77", encoded)
        self.assertNotIn("11111.11", encoded)
        self.assertNotIn("22222.22", encoded)
        self.assertNotIn("facts_hash", projected)
        self.assertNotIn("dependency_hash", projected)
        self.assertIsNone(projected["performance"])
        self.assertEqual(projected["attributions"], [])
        self.assertEqual(projected["cash_flow_aggregates"], [])
        self.assertEqual(projected["episode_assessments"], [])
        self.assertEqual(projected["execution_metrics"]["data_status"], "blocked")
        self.assertNotIn("-20", encoded)
        self.assertNotIn("-100", encoded)

    def test_partition_hash_mismatch_fails_closed(self):
        facts = private_facts()
        facts["trades"]["daily"][0]["rows"][0]["executed_quantity"] = "2"
        with self.assertRaisesRegex(PROJECT.WeeklyProjectionError, "do not match"):
            PROJECT.project_weekly_state(facts, self.store)

    def test_irrelevant_cash_flow_labels_are_not_projected(self):
        facts = private_facts()
        facts["cash_flow"]["groups"][0]["flow_name"] = "Commission"
        projected = PROJECT.project_weekly_state(facts, self.store)
        self.assertEqual(projected["cash_flow_aggregates"], [])
        self.assertNotIn("Commission", json.dumps(projected, ensure_ascii=False))

    def test_non_longbridge_source_fails_closed(self):
        facts = private_facts()
        facts["source"]["provider"] = "synthetic"
        with self.assertRaisesRegex(PROJECT.WeeklyProjectionError, "Longbridge-only"):
            PROJECT.project_weekly_state(facts, self.store)


if __name__ == "__main__":
    unittest.main()
