from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "trading-center-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import trade_plan_lifecycle as LIFE
from tests.test_construct_trade_plan import request
from tests.test_render_trade_review_dashboard_v2 import fixture, weekly_packet


class TradePlanLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="plan-lifecycle-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.store = LIFE.state.open_state_store(self.root / "state" / "plans.sqlite3", test_root=self.root)
        payload = request()
        # Synthetic dates shifted by whole weeks; never presented as broker data.
        for row in payload["bars"]:
            stamp = dt.datetime.fromisoformat(row["timestamp"]) + dt.timedelta(days=140)
            row["timestamp"] = stamp.isoformat()
        payload["source"]["requested_start"] = payload["bars"][0]["timestamp"][:10]
        for key in ("requested_end", "as_of"):
            payload["source"][key] = payload["bars"][-1]["timestamp"][:10]
        for key in ("generated_at", "expires_at"):
            payload[key] = (dt.datetime.fromisoformat(payload[key]) + dt.timedelta(days=140)).isoformat()
        self.draft = LIFE.constructor.construct_plan(payload)
        self.assertEqual(self.draft["plan_readiness"], "ready_for_confirmation")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def confirm(self):
        return LIFE.confirm_draft(
            self.store, plan_id=self.draft["plan_id"], draft_version=1,
            expected_hash=self.draft["content_hash"],
            confirmed_at=(dt.datetime.fromisoformat(self.draft["generated_at"]) + dt.timedelta(hours=1)).isoformat(),
            user_confirmed=True,
        )

    def test_construct_save_confirm_enrich_single_ui_public_seam(self):
        projection = LIFE.project_draft(self.draft)
        self.store.put_plan_version(projection)
        first = self.confirm()
        self.assertEqual(first.action, "written")
        self.assertEqual(self.confirm().action, "reused")
        stored = self.store.get_plan_version(self.draft["plan_id"], 2)
        self.assertEqual(stored["content_hash"], self.draft["content_hash"])
        daily = fixture("complete")
        daily["positions_plans"]["items"][0]["symbol"] = stored["underlying"]
        enriched = LIFE.enrich_daily(daily, stored)
        self.assertEqual(enriched["positions_plans"]["items"][0]["plan_detail"]["version"], 2)
        template = (SCRIPTS.parent / "assets" / "trade-review-dashboard-v2-standalone.html").read_text()
        html = LIFE.dashboard.render_unified_dashboard(daily_packet=enriched, weekly_packet=weekly_packet(), template=template)
        self.assertEqual(html.count("<main"), 1)
        self.assertIn("EMA20/50/200", html)
        self.assertNotIn('data-zone-kind="add"', html)
        self.assertEqual(self.store.table_count("weekly_reviews"), 0)

    def test_forged_hash_and_confirmation_without_specific_approval_fail_closed(self):
        forged = copy.deepcopy(self.draft)
        forged["zones"][0]["high"] = "999"
        with self.assertRaises(LIFE.state.StateContractError):
            LIFE.project_draft(forged)
        self.store.put_plan_version(LIFE.project_draft(self.draft))
        with self.assertRaises(LIFE.state.StateContractError):
            LIFE.confirm_draft(
                self.store, plan_id=self.draft["plan_id"], draft_version=1,
                expected_hash=self.draft["content_hash"], confirmed_at=self.draft["generated_at"],
                user_confirmed=False,
            )
        self.assertEqual(self.store.table_count("plan_versions"), 1)

    def test_quotes_change_relationship_only_and_stale_quote_does_not_move_zones(self):
        projection = LIFE.project_draft(self.draft)
        before = copy.deepcopy(projection["zones"])
        self.store.put_plan_version(projection)
        self.confirm()
        plan = self.store.get_plan_version(self.draft["plan_id"], 2)
        as_of = "2026-08-29T08:00:00+08:00"
        quote = {"source": "Longbridge", "price": "1", "as_of": as_of, "data_status": "complete"}
        self.assertEqual(LIFE.dashboard_detail(plan, as_of=as_of, quote=quote)["quote_relation"], "below")
        quote.update(price="999", data_status="stale")
        self.assertEqual(LIFE.dashboard_detail(plan, as_of=as_of, quote=quote)["quote_relation"], "stale")
        self.assertEqual(self.store.get_plan_version(plan["plan_id"], 2)["zones"], before)

    def test_expiry_is_display_only_and_future_plan_cannot_enrich_old_daily(self):
        self.store.put_plan_version(LIFE.project_draft(self.draft))
        self.confirm()
        plan = self.store.get_plan_version(self.draft["plan_id"], 2)
        detail = LIFE.dashboard_detail(plan, as_of="2026-12-01T00:00:00Z")
        self.assertEqual(detail["plan_status"], "expired")
        self.assertEqual(self.store.get_plan_version(plan["plan_id"], 2)["plan_status"], "confirmed")
        with self.assertRaises(LIFE.state.StateContractError):
            LIFE.dashboard_detail(plan, as_of="2026-01-01T00:00:00Z")

    def test_draft_does_not_claim_daily_plan_coverage(self):
        plan = LIFE.project_draft(self.draft)
        daily = fixture("complete")
        daily["positions_plans"]["items"][0]["symbol"] = plan["underlying"]
        enriched = LIFE.enrich_daily(daily, plan)
        row = enriched["positions_plans"]["items"][0]
        self.assertIn("不计入覆盖率", row["plan_coverage"])
        self.assertTrue(row["has_gap"])
        self.assertEqual(row["plan_detail"]["plan_status"], "draft")

    def test_cli_missing_confirmation_fails_before_database_creation(self):
        LIFE.runner.PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=LIFE.runner.PRIVATE_ROOT, prefix="plan-cli-") as path:
            result = LIFE.main([
                "confirm", "--plan-id", "demo", "--draft-version", "1",
                "--content-hash", "a" * 64, "--confirmed-at", "2026-08-29T08:00:00+08:00",
                "--state-db", str(self.root / "not-created.sqlite3"),
                "--output", str(Path(path) / "confirmation.json"),
            ])
            self.assertEqual(result, 2)
            self.assertFalse((self.root / "not-created.sqlite3").exists())

    def test_new_confirmation_cannot_be_backdated_but_exact_replay_is_idempotent(self):
        with mock.patch.object(LIFE.state, "utc_now", return_value="2026-08-31T10:00:00Z"):
            LIFE.validate_confirmation_clock("2026-08-31T09:59:30Z")
            with self.assertRaises(LIFE.state.StateContractError):
                LIFE.validate_confirmation_clock("2026-08-20T10:00:00Z")
            with self.assertRaises(LIFE.state.StateContractError):
                LIFE.validate_confirmation_clock("2026-09-01T10:00:00Z")
            LIFE.validate_confirmation_clock("2026-08-20T10:00:00Z", exact_replay=True)


if __name__ == "__main__":
    unittest.main()
