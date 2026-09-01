#!/usr/bin/env python3
"""Prepare/confirm a journal draft, then read-only enqueue its exact bound bytes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import trading_review_state as state
from review_bridge_io import PRODUCER_ROOT, PrivateTree, read_file
from review_journal_contract import HASH, SCHEMA, JournalError, canonical, digest, exact, managed_body, parse_json, validate_payload
from review_journal_state import insert_confirmation, source_for, verify_confirmation, verify_source


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def seal(row: dict) -> dict:
    result = dict(row)
    result["payload_hash"] = digest({k: v for k, v in row.items() if k != "payload_hash"})
    return result


def prepare(store, tree: PrivateTree, *, review_key: str, text: dict, generated_at: str) -> dict:
    exact(text, {"sections", "gap_categories"}, "draft_text_fields_mismatch")
    source = source_for(store, review_key)
    previous = store.connection.execute("SELECT c.confirmation_version,b.payload_hash FROM confirmations c LEFT JOIN journal_confirmation_bindings b USING(review_key,confirmation_version) WHERE c.review_key=? ORDER BY c.confirmation_version DESC LIMIT 1", (review_key,)).fetchone()
    if previous and previous["payload_hash"] is None:
        raise JournalError("legacy_confirmation_chain_unbound")
    version = previous["confirmation_version"] + 1 if previous else 1
    draft = seal({**{k: v for k, v in source.items() if k != "source_generated_at"}, "schema_version": "investment-review-draft.v1", "generated_at": generated_at,
                  "confirmation_status": "pending", "confirmation_version": version, "supersedes_confirmation_version": version - 1 if previous else None, "confirmed_at": None, **text})
    validate_payload(draft, draft=True)
    verify_source(store, draft)
    tree.write_once(f'drafts/{draft["payload_hash"]}.json', canonical(draft))
    # The preview is explicitly pending; it is not a confirmed envelope or outbox.
    preview = managed_body(draft, draft=True)
    tree.write_once(f'drafts/{draft["payload_hash"]}.md', ("# 脱敏复盘草稿 · 待确认\n\n> 仅供核对；尚未确认，不会同步日记。\n\n" + preview + "\n").encode("utf-8"))
    return draft


def confirm(store, tree: PrivateTree, *, approved_draft_hash: str, confirmation_text: str, confirmed_at: str) -> dict:
    if confirmation_text != "复盘完成" or not HASH.fullmatch(approved_draft_hash):
        raise JournalError("explicit_review_completion_and_draft_hash_required")
    with tree.lock("confirm"):
        draft = validate_payload(parse_json(tree.read(f"drafts/{approved_draft_hash}.json")), draft=True)
        if draft["payload_hash"] != approved_draft_hash:
            raise JournalError("approved_draft_hash_mismatch")
        prior = store.connection.execute("SELECT payload_hash FROM journal_confirmation_bindings WHERE review_key=? AND confirmation_version=?", (draft["review_key"], draft["confirmation_version"])).fetchone()
        if prior:
            existing = validate_payload(parse_json(tree.read(f'confirmed/{prior["payload_hash"]}.json')))
            original_draft = seal({**existing, "schema_version": "investment-review-draft.v1", "confirmation_status": "pending", "confirmed_at": None})
            if original_draft != draft:
                raise JournalError("confirmation_version_already_used_for_another_draft")
            insert_confirmation(store, existing)
            return existing
        payload = seal({**draft, "schema_version": SCHEMA, "confirmation_status": "confirmed", "confirmed_at": confirmed_at})
        validate_payload(payload)
        # Persist the exact full package first. An orphan on DB failure has no
        # confirmation binding and can never enter the outbox.
        tree.write_once(f'confirmed/{payload["payload_hash"]}.json', canonical(payload))
        insert_confirmation(store, payload)
        return payload


def outbox_relative(payload: dict) -> str:
    validate_payload(payload)
    return f'outbox/{digest(payload["review_key"])}/v{payload["confirmation_version"]:010d}.json'


def enqueue(store, tree: PrivateTree, payload_hash: str) -> dict:
    if not HASH.fullmatch(payload_hash):
        raise JournalError("invalid_confirmed_payload_identity")
    with tree.lock("enqueue"):
        raw = tree.read(f"confirmed/{payload_hash}.json")
        payload = validate_payload(parse_json(raw))
        if payload["payload_hash"] != payload_hash or raw != canonical(payload):
            raise JournalError("confirmed_artifact_identity_mismatch")
        # Caller supplies a consistent read_state_store transaction. This is
        # snapshot-time freshness, not a lock on subsequent external DB writes.
        verify_confirmation(store, payload)
        path = tree.write_once(outbox_relative(payload), raw)
    return {"status": "enqueued", "payload_hash": payload_hash, "confirmation_version": payload["confirmation_version"], "path": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("prepare"); make.add_argument("--review-key", required=True); make.add_argument("--text", type=Path, required=True)
    approve = sub.add_parser("confirm"); approve.add_argument("--approved-draft-hash", required=True); approve.add_argument("--confirmation-text", required=True)
    queue = sub.add_parser("enqueue"); queue.add_argument("--payload-hash", required=True)
    recover = sub.add_parser("recover-lock"); recover.add_argument("--name", choices=("confirm", "enqueue"), required=True); recover.add_argument("--expected-pid", type=int, required=True)
    args = parser.parse_args()
    tree = PrivateTree(PRODUCER_ROOT, kind="producer")
    try:
        if args.command == "prepare":
            private = Path("/private/tmp/trading-center-review-runtime")
            if private not in args.text.parents:
                raise JournalError("draft_text_must_be_in_private_runtime")
            text = parse_json(read_file(args.text))
            with state.read_state_store() as store:
                draft = prepare(store, tree, review_key=args.review_key, text=text, generated_at=now())
            result = {"status": "pending_user_review", "draft_hash": draft["payload_hash"], "preview": str(tree.path(f'drafts/{draft["payload_hash"]}.md'))}
        elif args.command == "confirm":
            store = state.open_state_store()
            try:
                payload = confirm(store, tree, approved_draft_hash=args.approved_draft_hash, confirmation_text=args.confirmation_text, confirmed_at=now())
                result = {"status": "confirmed_not_enqueued", "payload_hash": payload["payload_hash"]}
            finally:
                store.close()
        elif args.command == "enqueue":
            with state.read_state_store() as store:
                result = enqueue(store, tree, args.payload_hash)
        else:
            result = {"status": tree.recover_lock(args.name, args.expected_pid)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (JournalError, state.StateStoreError, OSError) as error:
        # No note text, account rows or raw CLI response in public output.
        print(json.dumps({"status": "blocked", "error_category": str(error) if isinstance(error, JournalError) else "state_or_private_io_failure"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
