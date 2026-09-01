from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/trading-center-review/scripts"))
from trading_review_valuation import ValuationError, calculate_pr, project_annual_roe, validate_valuation
import collect_scoped_valuations as collector
import private_runtime_io
import render_trade_review_dashboard_v2 as dashboard
import trading_review_portfolio as portfolio
import trading_review_state as state
from tests.test_render_trade_review_dashboard_v2 import fixture, weekly_packet


def valuation():
    return {"symbol": "DEMO.US", "instrument_type": "company", "as_of": "2026-08-31T10:00:00Z",
            "pe_ttm": "20", "roe_pct": "20", "roe_period_end": "2025-12-31", "roe_period_label": "FY 2025",
            "roe_basis": "annual", "roe_quality": "positive_income_equity", "pr": "1.00000000", "status": "available", "gap": "", "source": "Longbridge"}


class ValuationTests(unittest.TestCase):
    def test_display_can_be_rebuilt_from_persisted_valuation_without_temp_page(self):
        daily = fixture("complete")
        daily["positions_plans"]["items"][0]["symbol"] = "DEMO.US"
        display = dashboard.project_display_snapshot(daily, weekly_packet())
        with tempfile.TemporaryDirectory(prefix="valuation-display-") as name:
            root = Path(name).resolve(); root.chmod(0o700)
            with state.open_state_store(root / "state.sqlite3", test_root=root) as store:
                portfolio.put_valuations(store, [valuation()], allowed_symbols={"DEMO.US"})
                enriched = dashboard.enrich_display_from_state(display, store)
                with self.assertRaises(state.StateContractError):
                    portfolio.latest_valuations(store, {"DEMO260101C00100000.US"})
        self.assertEqual(enriched["daily"]["positions_plans"]["items"][0]["valuation"]["pr"], "1.00000000")
        self.assertEqual(enriched["daily"]["meta"]["market_as_of"], display["daily"]["meta"]["market_as_of"])

    def test_percentage_point_denominator_and_strict_projection(self):
        self.assertEqual(calculate_pr("20", "20"), "1.00000000")
        self.assertEqual(validate_valuation(valuation())["pr"], "1.00000000")
        for mutation in ({"pr": "100"}, {"roe_basis": "quarterly"}, {"roe_quality": "nonpositive"}, {"extra": "unadmitted"}, {"symbol": "DEMO.HK"}, {"symbol": "DEMO260101C00100000.US"}, {"pe_ttm": "NaN"}, {"roe_period_end": "2027-01-01"}):
            row = {**valuation(), **mutation}
            with self.assertRaises(ValuationError):
                validate_valuation(row)
        with self.assertRaises(ValuationError):
            validate_valuation(valuation(), symbol="OTHER.US")
        leaked = valuation(); leaked.update(status="unavailable", pr=None, gap="缺少 DEMO260101C00100000.US 数据")
        with self.assertRaisesRegex(ValuationError, "valuation_gap_invalid"):
            validate_valuation(leaked)

    def test_no_corporate_pr_for_funds_negative_missing_and_stale(self):
        for value in ("0", "-1"):
            with self.assertRaises(ValuationError):
                calculate_pr(value, "20")
            with self.assertRaises(ValuationError):
                calculate_pr("20", value)
        row = valuation(); row.update(instrument_type="fund")
        with self.assertRaises(ValuationError):
            validate_valuation(row)
        row.update(status="not_applicable", pe_ttm=None, roe_pct=None, roe_period_end=None, roe_period_label=None, roe_basis=None, roe_quality="not_applicable", pr=None, gap="ETF 不适用企业市赚率")
        self.assertEqual(validate_valuation(row)["status"], "not_applicable")
        row = valuation(); row["roe_period_end"] = "2023-12-31"
        with self.assertRaises(ValuationError):
            validate_valuation(row)
        row.update(status="stale", pr=None, gap="年度报告较旧")
        self.assertIsNone(validate_valuation(row)["pr"])

    def test_report_identity_period_and_income_quality(self):
        end = "1767139200"
        point = {"period": "FY 2025", "fp_end": end, "value": "20"}
        raw = {"symbol": "DEMO.US", "report": "af", "list": {"IS": {"indicators": [{"accounts": [
            {"field": "ROE", "percent": True, "values": [point]},
            {"field": "NetProfit", "percent": False, "values": [{**point, "value": "100"}]},
        ]}]}}}
        got = project_annual_roe(raw, "DEMO.US", valuation()["as_of"])
        self.assertEqual(got["roe_quality"], "positive_income_equity")
        nested = copy.deepcopy(raw); nested["symbol"] = ""
        nested["list"]["IS"]["indicators"][0]["entry"] = {"symbol": "DEMO.US"}
        self.assertEqual(project_annual_roe(nested, "DEMO.US", valuation()["as_of"]), got)
        nested["list"]["IS"]["indicators"][0]["entry"]["symbol"] = "OTHER.US"
        with self.assertRaises(ValuationError):
            project_annual_roe(nested, "DEMO.US", valuation()["as_of"])
        negative = copy.deepcopy(raw)
        negative["list"]["IS"]["indicators"][0]["accounts"][1]["values"][0]["value"] = "-100"
        self.assertEqual(project_annual_roe(negative, "DEMO.US", valuation()["as_of"])["roe_quality"], "nonpositive")
        with self.assertRaises(ValuationError):
            project_annual_roe(raw, "OTHER.US", valuation()["as_of"])
        raw["report"] = "qf"
        with self.assertRaises(ValuationError):
            project_annual_roe(raw, "DEMO.US", valuation()["as_of"])

    def test_single_symbol_collection_uses_explicit_calc_index_and_missing_cli_is_unavailable(self):
        display = dashboard.project_display_snapshot(fixture("complete"), weekly_packet())
        row = display["daily"]["positions_plans"]["items"][0]
        row.update(symbol="DEMO.US", tab="holdings")
        display["daily"]["positions_plans"]["items"] = [row]
        point = {"period": "FY 2025", "fp_end": "1767139200", "value": "20"}
        annual = {"symbol": "DEMO.US", "report": "af", "list": {"IS": {"indicators": [{"accounts": [
            {"field": "ROE", "percent": True, "values": [point]},
            {"field": "NetProfit", "percent": False, "values": [{**point, "value": "100"}]},
        ]}]}}}
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            payload = [{"symbol": "DEMO.US", "pe": "20"}] if command[1] == "calc-index" else annual
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        private_runtime_io.PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_runtime_io.PRIVATE_ROOT.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="valuation-collector-", dir=private_runtime_io.PRIVATE_ROOT) as name:
            root = Path(name).resolve(); root.chmod(0o700)
            with mock.patch.object(collector.subprocess, "run", side_effect=run):
                result = collector.collect(display, root / "valuation.json", funds=set())
            self.assertEqual(result["items"][0]["status"], "available")
            self.assertIn(
                ["/usr/local/bin/longbridge", "calc-index", "DEMO.US", "--fields", "pe", "--format", "json"],
                calls,
            )
        with tempfile.TemporaryDirectory(prefix="valuation-collector-missing-", dir=private_runtime_io.PRIVATE_ROOT) as name:
            root = Path(name).resolve(); root.chmod(0o700)
            with mock.patch.object(collector.subprocess, "run", side_effect=FileNotFoundError()):
                result = collector.collect(display, root / "valuation.json", funds=set())
            self.assertEqual(result["items"][0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
