#!/usr/bin/env python3
"""Knowledge-owned, explicit one-way Obsidian receiver. No trading DB imports."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from review_bridge_io import PRODUCER_ROOT, RECEIVER_ROOT, VAULT, PrivateTree, create_exclusive, identity, no_links, read_file
from review_journal_contract import JournalError, canonical, digest, exact, managed_body, new_note, parse_json, relative_path, split_managed, text_hash, validate_payload

JS = Path(__file__).with_name("obsidian_managed_update.js")


class ObsidianCLI:
    def __init__(self, vault: Path = VAULT):
        self.vault = vault

    def _call(self, args: list[str]):
        try:
            result = subprocess.run(["/usr/local/bin/obsidian", "vault=Mars知识库vault", *args], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JournalError("obsidian_cli_unavailable") from exc
        if result.returncode:
            raise JournalError("obsidian_cli_failed")
        return result.stdout

    def _eval(self, request: dict) -> dict:
        script = JS.read_text(encoding="utf-8")
        if script.count("/*__REQUEST__*/") != 1:
            raise JournalError("bundled_obsidian_script_invalid")
        code = script.replace("/*__REQUEST__*/", json.dumps({"vault": str(self.vault), **request}, ensure_ascii=True, separators=(",", ":")))
        output = self._call(["eval", "code=" + code])
        # Obsidian CLI formats eval's result with =>. Do not parse arbitrary logs.
        lines = [line[3:] for line in output.splitlines() if line.startswith("=> ")]
        if len(lines) != 1:
            raise JournalError("obsidian_eval_result_unavailable")
        try:
            result = json.loads(lines[0])
            if isinstance(result, str):
                result = json.loads(result)
        except ValueError as exc:
            raise JournalError("obsidian_eval_result_invalid") from exc
        if not isinstance(result, dict) or result.get("status") not in {"ready", "written", "deferred", "conflict"}:
            raise JournalError("obsidian_eval_result_invalid")
        return result

    def probe(self, relative: str) -> dict:
        return self._eval({"operation": "probe", "path": relative})

    def update(self, relative: str, before: str, after: str, block: str, file_identity: dict) -> dict:
        return self._eval({"operation": "update", "path": relative, "before_hash": text_hash(before), "after_hash": text_hash(after), "managed_body": block, "identity": file_identity})

    def readback(self, relative: str, expected: str) -> bool:
        actual = self._call(["read", "path=" + relative])
        # read's terminal printer may append exactly one newline; content isn't
        # stripped or searched as a substring, so a wrong note cannot pass.
        return actual == expected or actual == expected + "\n"


INTENT_KEYS = {"schema_version", "review_key", "confirmation_version", "payload_hash", "target", "before_hash", "after_hash", "before_identity", "managed_hash"}
WRITE_PROOF_KEYS = {"schema_version", "review_key", "confirmation_version", "payload_hash", "target", "after_hash", "managed_hash", "intent_hash", "file_identity"}
RECEIPT_KEYS = {"schema_version", "review_key", "confirmation_version", "payload_hash", "target", "managed_hash", "file_identity", "synced_at"}


class Receiver:
    def __init__(self, outbox: PrivateTree, receipts: PrivateTree, *, vault: Path = VAULT, adapter=None, test_root: Path | None = None, fault=None):
        no_links(vault)
        if (test_root is None and vault != VAULT) or (test_root is not None and test_root not in vault.parents):
            raise JournalError("unauthorized_vault")
        if not vault.is_dir() or vault.stat().st_uid != os.getuid():
            raise JournalError("vault_unavailable")
        self.outbox, self.receipts, self.vault = outbox, receipts, vault
        self.adapter = adapter or ObsidianCLI(vault)
        self.fault = fault or (lambda phase: None)

    def _target(self, relative: str) -> Path:
        target = self.vault / relative
        no_links(target)
        if self.vault not in target.parents or len(target.relative_to(self.vault).parts) != 3:
            raise JournalError("unsafe_journal_target")
        cursor = self.vault
        for part in target.relative_to(self.vault).parts[:-1]:
            cursor /= part
            if not cursor.exists():
                cursor.mkdir(mode=0o700)
            info = cursor.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise JournalError("unsafe_journal_directory")
        return target

    def _latest_receipt(self, key_hash: str):
        directory = self.receipts.path(f"receipts/{key_hash}/placeholder").parent
        if not directory.exists():
            return None
        versions = sorted(p.name for p in directory.iterdir() if re.fullmatch(r"v\d{10}\.json", p.name))
        if not versions:
            return None
        row = parse_json(self.receipts.read(f"receipts/{key_hash}/{versions[-1]}"))
        exact(row, RECEIPT_KEYS, "receipt_fields_invalid")
        if row["schema_version"] != "journal-sync-receipt.v1" or digest(row["review_key"]) != key_hash or versions[-1] != f'v{row["confirmation_version"]:010d}.json':
            raise JournalError("receipt_identity_invalid")
        return row

    def _read_intent(self, key_hash: str, version: int):
        directory = self.receipts.path(f"intents/{key_hash}/placeholder").parent
        if not directory.exists():
            return None
        prefix = f"v{version:010d}"
        candidates = [p.name for p in directory.iterdir() if p.name.startswith(prefix)]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise JournalError("write_intent_version_conflict")
        match = re.fullmatch(prefix + r"-([0-9a-f]{64})\.json", candidates[0])
        if match is None:
            raise JournalError("write_intent_filename_invalid")
        raw = self.receipts.read(f"intents/{key_hash}/{candidates[0]}")
        row = parse_json(raw)
        if raw != canonical(row) or digest(row) != match[1]:
            raise JournalError("write_intent_content_mismatch")
        return exact(row, INTENT_KEYS, "write_intent_fields_invalid")

    def _read_write_proof(self, key_hash: str, version: int):
        relative = f"writes/{key_hash}/v{version:010d}.json"
        path = self.receipts.path(relative)
        if not path.exists():
            return None
        raw = self.receipts.read(relative)
        row = parse_json(raw)
        if raw != canonical(row):
            raise JournalError("write_proof_content_mismatch")
        return exact(row, WRITE_PROOF_KEYS, "write_proof_fields_invalid")

    def sync(self, payload_path: Path) -> dict:
        try:
            relative_input = payload_path.relative_to(self.outbox.root).as_posix()
        except ValueError as exc:
            raise JournalError("payload_outside_fixed_outbox") from exc
        raw = self.outbox.read(relative_input)
        payload = validate_payload(parse_json(raw))
        key_hash, version = digest(payload["review_key"]), payload["confirmation_version"]
        expected_input = f"outbox/{key_hash}/v{version:010d}.json"
        if relative_input != expected_input or raw != canonical(payload):
            raise JournalError("outbox_identity_mismatch")
        # A newer enqueued version must not be bypassed just because its note
        # has not yet been written. Never scan outside this review's directory.
        siblings = self.outbox.path(expected_input).parent.iterdir()
        if any(re.fullmatch(r"v\d{10}\.json", p.name) and int(p.stem[1:]) > version for p in siblings):
            raise JournalError("outbox_version_superseded")
        with self.receipts.lock(key_hash):
            return self._sync(payload, key_hash)

    def _sync(self, payload: dict, key_hash: str) -> dict:
        version, relative = payload["confirmation_version"], relative_path(payload)
        probe = self.adapter.probe(relative)
        if probe.get("status") != "ready":
            return probe
        target = self._target(relative)
        receipt = self._latest_receipt(key_hash)
        if receipt:
            if receipt["target"] != relative or receipt["confirmation_version"] > version:
                raise JournalError("receipt_target_or_version_conflict")
            if receipt["confirmation_version"] == version:
                if receipt["payload_hash"] != payload["payload_hash"] or receipt["managed_hash"] != text_hash(managed_body(payload)):
                    raise JournalError("same_version_different_payload")
                current = read_file(target).decode("utf-8")
                if text_hash(split_managed(current)[1]) != receipt["managed_hash"] or identity(target) != receipt["file_identity"]:
                    raise JournalError("managed_note_was_edited")
                return self._readback(relative, current, reused=True)
        expected_previous = receipt["confirmation_version"] if receipt else None
        if payload["supersedes_confirmation_version"] != expected_previous:
            raise JournalError("receiver_confirmation_chain_missing")
        block = managed_body(payload)
        intent = self._read_intent(key_hash, version)
        if intent is not None:
            if intent["schema_version"] != "journal-write-intent.v1" or any(intent[k] != payload[k] for k in ("review_key", "confirmation_version", "payload_hash")) or intent["target"] != relative or intent["managed_hash"] != text_hash(block):
                raise JournalError("write_intent_identity_conflict")
        else:
            if receipt:
                before = read_file(target).decode("utf-8")
                prefix, old_block, suffix = split_managed(before)
                if text_hash(old_block) != receipt["managed_hash"] or identity(target) != receipt["file_identity"]:
                    raise JournalError("managed_note_was_edited")
                after = prefix + block + suffix
                file_identity = identity(target)
            else:
                if target.exists():
                    raise JournalError("existing_note_without_receipt")
                before, after, file_identity = None, new_note(payload), None
            intent = {"schema_version": "journal-write-intent.v1", "review_key": payload["review_key"], "confirmation_version": version,
                      "payload_hash": payload["payload_hash"], "target": relative, "before_hash": None if before is None else text_hash(before),
                      "after_hash": text_hash(after), "before_identity": file_identity, "managed_hash": text_hash(block)}
            intent_name = f'intents/{key_hash}/v{version:010d}-{digest(intent)}.json'
            self.receipts.write_once(intent_name, canonical(intent))
        if intent["before_identity"] != (receipt["file_identity"] if receipt else None):
            raise JournalError("write_intent_previous_identity_conflict")
        if not receipt and (intent["before_hash"] is not None or intent["after_hash"] != text_hash(new_note(payload))):
            raise JournalError("write_intent_creation_conflict")
        self.fault("after_intent")
        proof = self._read_write_proof(key_hash, version)
        current = read_file(target).decode("utf-8") if target.exists() else None
        current_hash = text_hash(current) if current is not None else None
        wrote = False
        if proof is not None:
            if (
                proof["schema_version"] != "journal-write-proof.v1"
                or any(proof[k] != payload[k] for k in ("review_key", "confirmation_version", "payload_hash"))
                or proof["target"] != relative
                or proof["after_hash"] != intent["after_hash"]
                or proof["managed_hash"] != intent["managed_hash"]
                or proof["intent_hash"] != digest(intent)
            ):
                raise JournalError("write_proof_identity_conflict")
            if current is None or current_hash != intent["after_hash"] or identity(target) != proof["file_identity"]:
                raise JournalError("write_proof_file_identity_changed")
            if split_managed(current)[1] != block:
                raise JournalError("managed_note_was_edited")
            after = current
        elif current_hash == intent["after_hash"]:
            # Identical bytes are not authorship evidence. Without a proof made
            # immediately after our write, a concurrent creator/replacement wins.
            raise JournalError("matching_note_without_write_proof")
        elif current_hash == intent["before_hash"]:
            if current is None:
                if intent["before_identity"] is not None:
                    raise JournalError("previous_journal_disappeared")
                after = new_note(payload)
                if text_hash(after) != intent["after_hash"]:
                    raise JournalError("pending_renderer_changed")
                if not create_exclusive(target, after.encode("utf-8")):
                    raise JournalError("journal_appeared_during_creation")
                wrote = True
            else:
                if identity(target) != intent["before_identity"]:
                    raise JournalError("journal_identity_changed")
                prefix, old_block, suffix = split_managed(current)
                if receipt is None or text_hash(old_block) != receipt["managed_hash"]:
                    raise JournalError("managed_note_was_edited")
                after = prefix + block + suffix
                if text_hash(after) != intent["after_hash"]:
                    raise JournalError("pending_renderer_changed")
                result = self.adapter.update(relative, current, after, block, identity(target))
                if result.get("status") != "written":
                    return result
                wrote = True
        else:
            raise JournalError("journal_changed_since_write_intent")
        if wrote:
            if read_file(target).decode("utf-8") != after:
                raise JournalError("journal_changed_after_write")
            proof = {
                "schema_version": "journal-write-proof.v1",
                "review_key": payload["review_key"],
                "confirmation_version": version,
                "payload_hash": payload["payload_hash"],
                "target": relative,
                "after_hash": intent["after_hash"],
                "managed_hash": intent["managed_hash"],
                "intent_hash": digest(intent),
                "file_identity": identity(target),
            }
            self.receipts.write_once(f"writes/{key_hash}/v{version:010d}.json", canonical(proof))
        self.fault("after_write")
        if read_file(target).decode("utf-8") != after or identity(target) != proof["file_identity"]:
            raise JournalError("journal_changed_after_write")
        result = self._readback(relative, after)
        if result["status"] != "synced":
            return result
        self.fault("after_readback")
        if identity(target) != proof["file_identity"]:
            raise JournalError("journal_identity_changed_after_readback")
        record = {"schema_version": "journal-sync-receipt.v1", "review_key": payload["review_key"], "confirmation_version": version,
                  "payload_hash": payload["payload_hash"], "target": relative, "managed_hash": intent["managed_hash"], "file_identity": proof["file_identity"],
                  "synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.receipts.write_once(f"receipts/{key_hash}/v{version:010d}.json", canonical(record))
        return result

    def _readback(self, relative: str, expected: str, *, reused: bool = False) -> dict:
        try:
            if not self.adapter.readback(relative, expected):
                return {"status": "written_pending_readback", "reason": "independent_readback_mismatch"}
            target = self.vault / relative
            if read_file(target).decode("utf-8") != expected:
                return {"status": "written_pending_readback", "reason": "note_changed_during_readback"}
        except JournalError:
            return {"status": "written_pending_readback", "reason": "obsidian_readback_unavailable"}
        return {"status": "synced", "reused": reused, "target": relative}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync"); sync.add_argument("--payload", type=Path, required=True)
    recover = sub.add_parser("recover-lock"); recover.add_argument("--review-key-hash", required=True); recover.add_argument("--expected-pid", type=int, required=True)
    args = parser.parse_args()
    try:
        incoming, receipts = PrivateTree(PRODUCER_ROOT, kind="producer"), PrivateTree(RECEIVER_ROOT, kind="receiver")
        if args.command == "recover-lock":
            if not re.fullmatch(r"[0-9a-f]{64}", args.review_key_hash):
                raise JournalError("invalid_lock_identity")
            result = {"status": receipts.recover_lock(args.review_key_hash, args.expected_pid)}
        else:
            result = Receiver(incoming, receipts).sync(args.payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"synced", "dead_lock_recovered"} else 2
    except (JournalError, OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"status": "conflict", "error_category": str(error) if isinstance(error, JournalError) else "private_io_or_encoding_failure"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
