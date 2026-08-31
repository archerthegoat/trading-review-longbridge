from __future__ import annotations

import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/trading-center-review/scripts"))
import trading_review_display as adapter
import render_trade_review_dashboard_v2 as dashboard
import trading_review_state as state
from test_render_trade_review_dashboard_v2 import fixture, weekly_packet


class DisplayBoundaryTests(unittest.TestCase):
    def test_projection_keeps_ui_but_not_account_diagnostics_or_orders(self):
        template = dashboard.DEFAULT_TEMPLATE.read_text()
        for name in ("complete", "partial", "empty", "stale"):
            daily, weekly = fixture(name), weekly_packet()
            view = adapter.adapt("project", {"daily": daily, "weekly": weekly})
            self.assertEqual(dashboard.render_unified_dashboard(daily_packet=daily, weekly_packet=weekly, template=template), dashboard.render_display_snapshot(view, template))
            for key in ("account", "data_note"):
                self.assertNotIn(key, view["daily"])
            for key in ("account_label", "account_snapshot_at"):
                self.assertNotIn(key, view["daily"]["meta"])
            self.assertNotIn("orders", view["daily"]["operations"])
            self.assertEqual([], view["weekly"]["sections"]["operations"])

    def test_original_private_schema_is_checked_before_projection(self):
        daily = fixture("complete")
        daily["account"]["unknown"] = "extra"
        with self.assertRaises(dashboard.DashboardRenderError):
            adapter.adapt("project", {"daily": daily, "weekly": None})

    def test_display_cannot_reintroduce_private_or_unknown_fields(self):
        view = dashboard.project_display_snapshot(fixture("complete"))
        for key in ("account", "extra"):
            bad = copy.deepcopy(view)
            bad["daily"][key] = {}
            with self.assertRaises(dashboard.DashboardRenderError):
                adapter.adapt("validate", bad)

    def test_strict_parser_rejects_duplicates_and_nonfinite(self):
        for value in ('{"a":1,"a":2}', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError):
                adapter.parse(value)

    def test_read_only_state_never_creates_migrates_or_writes(self):
        with tempfile.TemporaryDirectory(prefix="read-state-test-") as directory:
            root = Path(directory).resolve()
            db = root / "state.sqlite3"
            with state.open_state_store(db, test_root=root):
                pass
            before = db.read_bytes()
            with state.read_state_store(db, test_root=root) as reader:
                self.assertEqual(0, reader.table_count("plan_versions"))
                with self.assertRaises(sqlite3.OperationalError):
                    reader.connection.execute("DELETE FROM plan_versions")
            self.assertEqual(before, db.read_bytes())
            with self.assertRaises(state.StateStoreError):
                with state.read_state_store(root / "missing.sqlite3", test_root=root):
                    pass
            self.assertFalse((root / "missing.sqlite3").exists())
            with sqlite3.connect(str(db)) as connection:
                connection.execute("PRAGMA user_version=2")
            before = db.read_bytes()
            with self.assertRaises(state.StateMigrationError):
                with state.read_state_store(db, test_root=root):
                    pass
            self.assertEqual(before, db.read_bytes())


if __name__ == "__main__":
    unittest.main()
