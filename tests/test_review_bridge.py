from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/trading-center-review/scripts"
sys.path.insert(0, str(SCRIPTS))
import trading_review_state as state
from run_incremental_review import process_daily_bundle
from review_bridge_io import PrivateTree, create_exclusive, identity, read_file
from review_bridge_producer import prepare, confirm, enqueue, seal, outbox_relative
from review_bridge_receiver import Receiver, ObsidianCLI
from review_journal_contract import JournalError, SECTIONS, canonical, digest, managed_body, new_note, parse_json, relative_path, split_managed, text_hash, validate_payload
from review_journal_state import source_for, insert_confirmation
from test_incremental_review_runner import daily_bundle
from test_trading_review_valuation import valuation
from trading_review_portfolio import put_valuations, put_management_intent


def journal_text():
    return {"sections": {key: ["已核对成交与事前计划。"] for key in SECTIONS}, "gap_categories": []}


class FakeObsidian:
    def __init__(self, root):
        self.root, self.open, self.fail_read = root, False, False
        self.writes, self.reads = 0, 0
        self.before_write = None

    def probe(self, relative):
        return {"status": "deferred", "reason": "note_open"} if self.open else {"status": "ready"}

    def update(self, relative, before, after, block, file_identity):
        target = self.root / relative
        if self.before_write:
            self.before_write(target)
        if self.open:
            return {"status": "deferred", "reason": "note_open"}
        if target.read_text() != before or identity(target) != file_identity:
            return {"status": "conflict", "reason": "content_changed"}
        target.write_text(after)
        self.writes += 1
        return {"status": "written"}

    def readback(self, relative, expected):
        self.reads += 1
        return not self.fail_read and (self.root / relative).read_text() == expected


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="journal-bridge-test-")
        self.root = Path(self.tmp.name).resolve()
        self.store = state.open_state_store(self.root / "review.sqlite3", test_root=self.root)
        process_daily_bundle(self.store, daily_bundle())
        self.producer = PrivateTree(self.root / "producer", kind="producer", test_root=self.root)
        self.receipts = PrivateTree(self.root / "receiver", kind="receiver", test_root=self.root)
        self.vault = self.root / "vault"; self.vault.mkdir(mode=0o700)
        self.adapter = FakeObsidian(self.vault)
        self.receiver = Receiver(self.producer, self.receipts, vault=self.vault, adapter=self.adapter, test_root=self.root)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def prepare(self, version=1, text=None):
        return prepare(self.store, self.producer, review_key="daily:2026-08-28", text=text or journal_text(), generated_at=f"2026-08-29T0{version}:00:00Z")

    def approved(self, version=1, text=None):
        draft = self.prepare(version, text)
        return confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="复盘完成", confirmed_at=f"2026-08-29T0{version}:30:00Z")

    def enqueue(self, payload):
        with state.read_state_store(self.store.path, test_root=self.root) as readonly:
            return Path(enqueue(readonly, self.producer, payload["payload_hash"])["path"])

    def test_no_weak_confirmation_draft_or_altered_text_can_enqueue(self):
        draft = self.prepare()
        self.assertEqual(draft["confirmation_status"], "pending")
        self.assertEqual(self.store.table_count("confirmations"), 0)
        with self.assertRaises(JournalError):
            validate_payload(draft)
        with self.assertRaises(JournalError):
            confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="持仓原则确认", confirmed_at="2026-08-29T02:00:00Z")
        self.store.confirm("daily:2026-08-28", 1, "confirmed", "2026-08-29T02:00:00Z", draft["facts_hash"])
        with self.assertRaisesRegex(JournalError, "legacy_confirmation_chain_unbound"):
            confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="复盘完成", confirmed_at="2026-08-29T02:00:00Z")
        self.assertFalse(self.producer.path("outbox/placeholder").parent.exists())

    def test_confirmed_package_is_immutable_and_read_only_enqueue(self):
        payload = self.approved()
        before = self.store.connection.total_changes
        queued = self.enqueue(payload)
        self.assertEqual(self.store.connection.total_changes, before)
        self.assertEqual(parse_json(read_file(queued)), payload)
        self.assertEqual(self.enqueue(payload), queued)
        changed = copy.deepcopy(payload); changed["sections"]["facts"] = ["新解释，尚未另行确认。"]
        changed = seal(changed)
        self.producer.write_once(f'confirmed/{changed["payload_hash"]}.json', canonical(changed))
        with self.assertRaises(JournalError):
            self.enqueue(changed)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("DELETE FROM journal_confirmation_bindings")

    def test_retry_of_confirmation_recovers_original_exact_envelope(self):
        draft = self.prepare()
        first = confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="复盘完成", confirmed_at="2026-08-29T01:30:00Z")
        retry = confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="复盘完成", confirmed_at="2026-08-29T02:30:00Z")
        self.assertEqual(retry, first)
        self.assertEqual(self.store.table_count("confirmations"), 1)

    def test_pending_preview_preserves_user_text_that_matches_status_labels(self):
        text = journal_text()
        prose = "请保留已确认版本：及确认时间：这两个标签。"
        text["sections"]["lessons"] = [prose]
        draft = self.prepare(text=text)
        preview = self.producer.read(f'drafts/{draft["payload_hash"]}.md').decode("utf-8")
        self.assertIn(prose, preview)
        self.assertIn("- 待确认版本：", preview)
        self.assertIn("- 草稿生成时间：", preview)
        payload = confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="复盘完成", confirmed_at="2026-08-29T01:30:00Z")
        self.assertEqual(payload["sections"], draft["sections"])
        self.assertIn(prose, managed_body(payload))

    def test_source_changes_block_confirmation_and_enqueue(self):
        payload = self.approved()
        self.store.ingest_partition(dataset="trades", period_start="2026-08-28", period_end="2026-08-28", contract_version="source.v1:trades", status="complete", collected_at="2026-08-29T03:00:00Z", payload=[{**daily_bundle()["modules"]["trades"]["payload"][0], "executed_quantity": "3"}])
        with self.assertRaisesRegex(JournalError, "dependencies_changed"):
            self.enqueue(payload)
        with self.assertRaises(JournalError):
            insert_confirmation(self.store, payload)

    def test_strict_confirmation_rechecks_inside_immediate_transaction(self):
        payload = self.approved()
        import review_journal_state as js
        original = js.verify_source
        checks = []
        def check(store, envelope):
            checks.append(store.connection.in_transaction)
            original(store, envelope)
        with mock.patch.object(js, "verify_source", check):
            self.assertEqual(insert_confirmation(self.store, payload), "reused")
        self.assertEqual(checks, [True])

    def test_first_sync_idempotency_and_preserve_manual_bytes(self):
        p1 = self.approved(); path = self.enqueue(p1)
        result = self.receiver.sync(path)
        self.assertEqual(result["status"], "synced")
        target = self.vault / relative_path(p1)
        first = target.read_text()
        self.assertNotIn("周度执行质量", first)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.receiver.sync(path)["reused"])
        prefix, block, suffix = split_managed(first)
        suffix += "\n这是我的手工补充，保持原样。\n"
        target.write_text(prefix + block + suffix)
        text = journal_text(); text["sections"]["lessons"] = ["等待确认条件，避免计划外行动。"]
        p2 = self.approved(2, text); path2 = self.enqueue(p2)
        self.assertEqual(self.receiver.sync(path2)["status"], "synced")
        got_prefix, got_block, got_suffix = split_managed(target.read_text())
        self.assertEqual((got_prefix, got_suffix), (prefix, suffix))
        self.assertIn("等待确认条件", got_block)
        with self.assertRaisesRegex(JournalError, "outbox_version_superseded"):
            self.receiver.sync(path)

    def test_open_note_and_managed_human_changes_never_get_overwritten(self):
        p1 = self.approved(); path = self.enqueue(p1)
        self.adapter.open = True
        self.assertEqual(self.receiver.sync(path)["status"], "deferred")
        self.assertFalse((self.vault / relative_path(p1)).exists())
        self.adapter.open = False; self.receiver.sync(path)
        target = self.vault / relative_path(p1)
        target.write_text(target.read_text().replace("已核对成交与事前计划。", "用户改写了受管正文。"))
        before = target.read_bytes()
        p2 = self.approved(2); path2 = self.enqueue(p2)
        with self.assertRaisesRegex(JournalError, "edited"):
            self.receiver.sync(path2)
        self.assertEqual(target.read_bytes(), before)

    def test_write_then_crash_or_cli_failure_recovers_without_second_write(self):
        p1 = self.approved(); path = self.enqueue(p1)
        self.adapter.fail_read = True
        self.assertEqual(self.receiver.sync(path)["status"], "written_pending_readback")
        target = self.vault / relative_path(p1)
        original_inode = target.stat().st_ino
        self.adapter.fail_read = False
        self.assertEqual(self.receiver.sync(path)["status"], "synced")
        self.assertEqual(target.stat().st_ino, original_inode)
        p2 = self.approved(2); path2 = self.enqueue(p2)
        def crash(phase):
            if phase == "after_write":
                raise RuntimeError("simulated crash")
        self.receiver.fault = crash
        with self.assertRaises(RuntimeError):
            self.receiver.sync(path2)
        self.assertEqual(self.adapter.writes, 1)
        self.receiver.fault = lambda _: None
        self.assertEqual(self.receiver.sync(path2)["status"], "synced")
        self.assertEqual(self.adapter.writes, 1)

    def test_pending_write_does_not_claim_same_content_with_a_new_inode(self):
        first = self.approved(); self.receiver.sync(self.enqueue(first))
        second = self.approved(2); queued = self.enqueue(second)
        self.receiver.fault = lambda phase: (_ for _ in ()).throw(RuntimeError("simulated crash")) if phase == "after_write" else None
        with self.assertRaises(RuntimeError):
            self.receiver.sync(queued)
        target = self.vault / relative_path(second)
        expected = target.read_text()
        replacement = target.with_suffix(".replacement")
        replacement.write_text(expected); replacement.chmod(0o600)
        os.replace(replacement, target)
        self.receiver.fault = lambda _: None
        with self.assertRaisesRegex(JournalError, "identity"):
            self.receiver.sync(queued)
        self.assertEqual(target.read_text(), expected)
        self.assertEqual(self.adapter.writes, 1)

    def test_pending_creation_does_not_claim_an_identical_external_file(self):
        payload = self.approved(); queued = self.enqueue(payload)
        self.receiver.fault = lambda phase: (_ for _ in ()).throw(RuntimeError("simulated crash")) if phase == "after_intent" else None
        with self.assertRaises(RuntimeError):
            self.receiver.sync(queued)
        target = self.receiver._target(relative_path(payload))
        target.write_text(new_note(payload)); target.chmod(0o600)
        self.receiver.fault = lambda _: None
        with self.assertRaisesRegex(JournalError, "write_proof"):
            self.receiver.sync(queued)
        self.assertEqual(target.read_text(), new_note(payload))

    def test_existing_unmanaged_and_concurrent_creation_are_preserved(self):
        payload = self.approved(); path = self.enqueue(payload)
        target = self.receiver._target(relative_path(payload))
        target.write_text("旧的手工日记"); target.chmod(0o600)
        with self.assertRaisesRegex(JournalError, "without_receipt"):
            self.receiver.sync(path)
        self.assertEqual(target.read_text(), "旧的手工日记")
        other = target.parent / "isolated-race-fixture.md"
        original_link = os.link
        def raced_link(src, dst, **kwargs):
            Path(dst).write_text("concurrent human content")
            return original_link(src, dst, **kwargs)
        with mock.patch("review_bridge_io.os.link", side_effect=raced_link):
            self.assertFalse(create_exclusive(other, b"automated body"))
        self.assertEqual(other.read_text(), "concurrent human content")

    def test_corrupt_intent_and_concurrent_edit_are_conflicts(self):
        payload = self.approved(); path = self.enqueue(payload)
        key_hash = digest(payload["review_key"])
        self.receipts.write_once(f"intents/{key_hash}/v0000000001.json", b"{")
        with self.assertRaises(JournalError):
            self.receiver.sync(path)
        self.assertFalse((self.vault / relative_path(payload)).exists())

    def test_semantically_valid_intent_edit_cannot_claim_modified_note_synced(self):
        payload = self.approved(); queued = self.enqueue(payload)
        self.adapter.fail_read = True
        self.assertEqual(self.receiver.sync(queued)["status"], "written_pending_readback")
        target = self.vault / relative_path(payload)
        target.write_text(target.read_text().replace("已核对成交与事前计划。", "用户改写了受管正文。"))
        preserved = target.read_bytes()
        key_hash = digest(payload["review_key"])
        intent_file = next(self.receipts.path(f"intents/{key_hash}/placeholder").parent.glob("v0000000001-*.json"))
        intent = parse_json(read_file(intent_file))
        intent["after_hash"] = text_hash(target.read_text())
        intent_file.write_bytes(canonical(intent))
        self.adapter.fail_read = False
        with self.assertRaisesRegex(JournalError, "write_intent_content_mismatch"):
            self.receiver.sync(queued)
        self.assertEqual(target.read_bytes(), preserved)
        self.assertIsNone(self.receiver._latest_receipt(key_hash))

    def test_pending_update_rechecks_previous_managed_text_even_if_intent_is_rebound(self):
        first = self.approved(); self.receiver.sync(self.enqueue(first))
        second = self.approved(2); queued = self.enqueue(second)
        def crash(phase):
            if phase == "after_intent":
                raise RuntimeError("simulated pre-write crash")
        self.receiver.fault = crash
        with self.assertRaises(RuntimeError):
            self.receiver.sync(queued)
        self.receiver.fault = lambda _: None
        target = self.vault / relative_path(first)
        target.write_text(target.read_text().replace("已核对成交与事前计划。", "用户改写了受管正文。"))
        before = target.read_text()
        prefix, _, suffix = split_managed(before)
        key_hash = digest(first["review_key"])
        intent_file = next(self.receipts.path(f"intents/{key_hash}/placeholder").parent.glob("v0000000002-*.json"))
        intent = parse_json(read_file(intent_file))
        intent.update(before_hash=text_hash(before), before_identity=identity(target), after_hash=text_hash(prefix + managed_body(second) + suffix))
        intent_file.write_bytes(canonical(intent))
        with self.assertRaisesRegex(JournalError, "write_intent_content_mismatch"):
            self.receiver.sync(queued)
        # Even replacing the content-addressed filename is not authority to
        # overwrite a managed block that no longer matches the prior receipt.
        intent_file.rename(intent_file.with_name(f'v0000000002-{digest(intent)}.json'))
        with self.assertRaisesRegex(JournalError, "managed_note_was_edited"):
            self.receiver.sync(queued)
        self.assertEqual(target.read_text(), before)
        self.assertEqual(self.adapter.writes, 0)
        self.assertEqual(self.receiver._latest_receipt(key_hash)["confirmation_version"], 1)

    def test_private_paths_hardlinks_and_live_locks_reject(self):
        file = self.producer.write_once("sample.json", b"{}")
        linked = file.with_name("linked.json"); os.link(file, linked)
        with self.assertRaises(JournalError):
            self.producer.read("sample.json")
        with self.receipts.lock("test"):
            with self.assertRaises(JournalError):
                self.receipts.recover_lock("test", os.getpid())
        with self.assertRaises(JournalError):
            self.producer.path("../leak")

    def test_scoped_valuation_and_principles_are_not_executable_plans(self):
        self.assertEqual(put_valuations(self.store, [valuation()], allowed_symbols={"DEMO.US"}), 1)
        self.assertEqual(put_valuations(self.store, [valuation()], allowed_symbols={"DEMO.US"}), 0)
        with self.assertRaisesRegex(RuntimeError, "valuation_outside_explicit_portfolio_scope"):
            put_valuations(self.store, [valuation()], allowed_symbols={"OTHER.US"})
        intent = {"underlying": "DEMO.US", "confirmed_at": "2026-08-29T02:00:00Z", "thesis": "用户认为适合长期持有", "holding_policy": "暂时保持原仓位", "add_policy": "暂不加仓", "review_price": "90", "possible_add_price": "95", "trigger_basis": "unconfirmed", "execution_authorized": False}
        put_management_intent(self.store, intent, user_confirmed=True)
        self.assertEqual(self.store.table_count("plan_versions"), 0)
        with self.assertRaisesRegex(RuntimeError, "management_principles_are_not_execution_authority"):
            put_management_intent(self.store, {**intent, "execution_authorized": True}, user_confirmed=True)
        with self.assertRaisesRegex(RuntimeError, "option contract identity"):
            put_management_intent(
                self.store,
                {**intent, "confirmed_at": "2026-08-29T03:00:00Z", "thesis": "误记 DEMO260101C00100000.US"},
                user_confirmed=True,
            )
        compact = "DEMO260101C00100000.US"
        with self.assertRaises(RuntimeError):
            put_valuations(self.store, [{**valuation(), "symbol": compact}], allowed_symbols={compact})
        with self.assertRaises(RuntimeError):
            put_management_intent(self.store, {**intent, "underlying": compact}, user_confirmed=True)

    def test_management_confirmation_orders_instants_not_timestamp_strings(self):
        intent = {"underlying": "DEMO.US", "confirmed_at": "2026-08-29T10:00:00+08:00", "thesis": "用户认为适合长期持有", "holding_policy": "暂时保持原仓位", "add_policy": "暂不加仓", "review_price": None, "possible_add_price": None, "trigger_basis": "unconfirmed", "execution_authorized": False}
        put_management_intent(self.store, intent, user_confirmed=True)
        later = put_management_intent(self.store, {**intent, "confirmed_at": "2026-08-29T03:00:00Z"}, user_confirmed=True)
        self.assertEqual(later["version"], 2)
        with self.assertRaisesRegex(RuntimeError, "management_confirmation_must_advance"):
            put_management_intent(self.store, {**intent, "confirmed_at": "2026-08-29T11:00:00+08:00"}, user_confirmed=True)
        self.assertEqual(self.store.table_count("holding_management_intents"), 2)

    def test_weekly_uses_bound_metrics_and_never_fills_unknown_plan_hash(self):
        from test_trading_review_state import weekly_v2_bundle
        p = self.store._latest_partition("trades", "2026-08-28", "2026-08-28", "source.v1:trades")
        dep = {"dataset": "trades", "period_start": "2026-08-28", "period_end": "2026-08-28", "contract_version": "source.v1:trades", "partition_revision": p["revision"], "payload_hash": p["payload_hash"]}
        bundle = weekly_v2_bundle(dep, blocked=True)
        self.store.start_run(run_id="weekly-run", mode="weekly", period_start=bundle["period_start"], period_end=bundle["period_end"], started_at=bundle["generated_at"], data_status="partial", source_contract_version="source.v1")
        self.store.ingest_weekly_review(bundle)
        self.store.finish_run("weekly-run", bundle["generated_at"], "partial")
        with self.assertRaisesRegex(JournalError, "plan_or_window_missing"):
            source_for(self.store, bundle["review_key"])
        bundle["plan_hash"] = "b" * 64
        self.store.ingest_weekly_review(bundle)
        text = journal_text(); text["gap_categories"] = ["事前计划与执行证据不足"]
        draft = prepare(self.store, self.producer, review_key=bundle["review_key"], text=text, generated_at="2026-08-30T01:00:00Z")
        payload = confirm(self.store, self.producer, approved_draft_hash=draft["payload_hash"], confirmation_text="复盘完成", confirmed_at="2026-08-30T02:00:00Z")
        result = self.receiver.sync(self.enqueue(payload))
        self.assertEqual(result["status"], "synced")
        note = (self.vault / relative_path(payload)).read_text()
        self.assertIn("## 周度执行质量", note)
        self.assertIn("暂不可计算", note)
        self.assertNotIn("0.0%", note)
        changed = copy.deepcopy(payload); changed["weekly_metrics"]["coverage_rate"] = 0
        with self.assertRaises(JournalError):
            validate_payload(seal(changed))

    def test_concurrent_manual_save_between_preflight_and_atomic_callback_is_kept(self):
        first = self.approved(); self.receiver.sync(self.enqueue(first))
        second = self.approved(2); queued = self.enqueue(second)
        target = self.vault / relative_path(second)
        def edit(path):
            path.write_text(path.read_text() + "\n最后一刻的手工保存。\n")
        self.adapter.before_write = edit
        result = self.receiver.sync(queued)
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(target.read_text().endswith("最后一刻的手工保存。\n"))
        self.assertEqual(self.adapter.writes, 0)


class ContractTests(unittest.TestCase):
    def test_privacy_never_silently_redacts(self):
        from review_journal_contract import safe_text
        for text in ("买入 10 股", "加仓位在四百五十五", "亏损五百美元", "订单编号：secret", "买入价 USD 23.4", "投入１００元", "[打开](https://example.org)", "<img src=x>", "成本四百", "数量十", "金额一万", "误记 demo260101c00100000.us"):
            with self.subTest(text=text), self.assertRaises(JournalError):
                safe_text(text)
        self.assertEqual(safe_text("长期持有，当前暂不加仓。"), "长期持有，当前暂不加仓。")
        with self.assertRaises(JournalError):
            parse_json(b'{"x":1,"x":2}')

    def test_independent_cli_read_requires_full_match(self):
        adapter = ObsidianCLI()
        with mock.patch.object(adapter, "_call", return_value="expected\n"):
            self.assertTrue(adapter.readback("irrelevant", "expected"))
        with mock.patch.object(adapter, "_call", return_value="wrong note expected"):
            self.assertFalse(adapter.readback("irrelevant", "expected"))

    def test_v3_migration_keeps_old_rows_and_backup_and_enforces_append_only(self):
        with tempfile.TemporaryDirectory(prefix="journal-v4-migration-") as name:
            root = Path(name).resolve(); database = root / "state.sqlite3"
            con = sqlite3.connect(database, isolation_level=None)
            database.chmod(0o600)
            con.execute("BEGIN IMMEDIATE")
            state._apply_migration_v1(con); state._apply_migration_v2(con); state._apply_migration_v3(con)
            con.execute("PRAGMA user_version=3")
            con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", ("legacy", "daily", "2026-08-28", "2026-08-28", "2026-08-29T00:00:00Z", "2026-08-29T00:00:00Z", "partial", "pending", "source.v1"))
            before = con.execute("SELECT * FROM runs").fetchall()
            con.commit(); con.close()
            with state.open_state_store(database, test_root=root) as migrated:
                self.assertEqual([tuple(r) for r in migrated.connection.execute("SELECT * FROM runs")], before)
                self.assertEqual(migrated.table_count("daily_review_sources"), 0)
                self.assertEqual(migrated.table_count("journal_confirmation_bindings"), 0)
                self.assertEqual(migrated.connection.execute("PRAGMA user_version").fetchone()[0], state.SCHEMA_VERSION)
            backups = list(root.glob("*.backup-*"))
            if not backups:
                backups = [p for p in root.iterdir() if "backup" in p.name]
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            saved = sqlite3.connect(backups[0])
            self.assertEqual(saved.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(saved.execute("SELECT * FROM runs").fetchall(), before)
            saved.close()

    def test_receiver_installation_is_owner_only_pinned_and_outside_vault(self):
        from install_review_bridge import install, verify, FILES
        with tempfile.TemporaryDirectory(prefix="bridge-install-test-") as name:
            root = Path(name).resolve()
            tree = PrivateTree(root / "knowledge", kind="receiver", test_root=root)
            info = install(tree)
            self.assertEqual(info, verify(tree))
            self.assertEqual(info, install(tree))
            for file in FILES:
                self.assertEqual(tree.path(f'code/{info["code_id"]}/{file}').stat().st_mode & 0o777, 0o600)
            script = tree.path(f'code/{info["code_id"]}/review_bridge_receiver.py')
            script.write_text("tampered")
            with self.assertRaisesRegex(JournalError, "integrity_failed"):
                verify(tree)


if __name__ == "__main__":
    unittest.main()
