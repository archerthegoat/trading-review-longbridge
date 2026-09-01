from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "trading-center-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

SCRIPT = SCRIPTS / "refresh_market_close_environment.py"
SPEC = importlib.util.spec_from_file_location("refresh_market_close_environment", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load market close refresh")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

from test_render_trade_review_dashboard_v2 import completed_close_packet  # noqa: E402


def facts():
    changes = {
        "SPY.US": 0.50,
        "QQQ.US": 0.80,
        "IEF.US": 0.20,
        "GLD.US": -0.10,
        "USO.US": 0.30,
        "IBIT.US": 1.10,
    }
    return {
        symbol: {
            "symbol": symbol,
            "market_date": "2026-08-28",
            "as_of": "2026-08-28T04:00:00Z",
            "close": 100 + change,
            "previous_close": 100,
            "change_pct": change,
        }
        for symbol, change in changes.items()
    }


def display_snapshot():
    return MODULE.dashboard.project_display_snapshot(completed_close_packet(), None)


class MarketCloseEnvironmentTests(unittest.TestCase):
    def test_completed_bar_uses_previous_completed_close(self):
        response = {
            "data": [
                {"time": "2026-08-27T04:00:00Z", "close": "100"},
                {"time": "2026-08-28T04:00:00Z", "close": "101.5"},
            ]
        }
        fact = MODULE.completed_close_fact(response, "SPY.US", "2026-08-28")
        self.assertIsNotNone(fact)
        self.assertEqual(fact["close"], 101.5)
        self.assertAlmostEqual(fact["change_pct"], 1.5)

    def test_refresh_replaces_night_quotes_with_fixed_close_environment(self):
        result = MODULE.refresh_snapshot(
            display_snapshot(),
            facts(),
            now=dt.datetime(2026, 9, 1, 21, 0, tzinfo=MODULE.SHANGHAI_TZ),
        )
        market = result["daily"]["market"]
        self.assertEqual(market["basis"], "completed_close")
        self.assertEqual(market["market_date"], "2026-08-28")
        self.assertEqual(market["environment"]["status"], "complete")
        self.assertTrue(all(row["session"] == "收盘" for row in market["items"]))

    def test_missing_equity_close_suppresses_the_judgement(self):
        partial = facts()
        partial["QQQ.US"] = None
        result = MODULE.refresh_snapshot(
            display_snapshot(),
            partial,
            now=dt.datetime(2026, 9, 1, 21, 0, tzinfo=MODULE.SHANGHAI_TZ),
        )
        environment = result["daily"]["market"]["environment"]
        self.assertEqual(environment["status"], "partial")
        self.assertEqual(
            environment["headline"],
            "上一交易日收盘数据尚未齐备，本次不形成市场环境判断。",
        )


if __name__ == "__main__":
    unittest.main()
