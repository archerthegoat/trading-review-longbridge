#!/usr/bin/env python3
"""Rehearse or apply the approved append-only state migration with redacted proof."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path

import run_incremental_review as runner
import trading_review_state as state


def fingerprint(path: Path, *, standalone_backup: bool = False):
    uri = path.as_uri() + "?mode=ro"
    if standalone_backup:
        # SQLite backups retain the WAL header even when their contents are
        # fully checkpointed. A normal read-only open can try to create -shm.
        # Only an explicitly closed standalone backup may bypass those files;
        # never use immutable mode on the live database or an active WAL set.
        if ".backup-" not in path.name or not path.name.endswith(".sqlite3"):
            raise state.StateContractError("immutable reads require a named standalone backup")
        if path.is_symlink() or not path.is_file():
            raise state.StateContractError("standalone backup must be a regular file")
        state._require_owner_mode(path, 0o600, "standalone backup")
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise state.StateContractError("standalone backup must not have SQLite companion files")
        uri += "&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        if not set(names).issubset(state.SCHEMA_TABLES):
            raise state.StateContractError("unexpected tables in migration target")
        tables = {}
        for name in names:
            if name == "schema_meta":
                continue
            columns = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            primary_key = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            order = ", ".join('"' + column + '"' for column in primary_key)
            rows = connection.execute(f'SELECT * FROM "{name}" ORDER BY {order}').fetchall()
            tables[name] = {
                "count": len(rows),
                "logical_hash": hashlib.sha256(state.canonical_json(rows).encode()).hexdigest(),
            }
        return {
            "schema_version": version,
            "quick_check": [row[0] for row in connection.execute("PRAGMA quick_check")],
            "foreign_key_error_count": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "created_at": connection.execute("SELECT created_at FROM schema_meta WHERE singleton=1").fetchone()[0],
            "tables": tables,
            "mode": oct(stat.S_IMODE(path.stat().st_mode)),
        }


def verify(before, after):
    if after["schema_version"] != state.SCHEMA_VERSION or after["quick_check"] != ["ok"] or after["foreign_key_error_count"]:
        raise state.StateContractError("migrated database integrity check failed")
    if before["created_at"] != after["created_at"] or after["mode"] != "0o600":
        raise state.StateContractError("migration changed creation history or permissions")
    for name, proof in before["tables"].items():
        if after["tables"].get(name) != proof:
            raise state.StateContractError("migration changed existing table facts: " + name)
    return sorted(set(after["tables"]) - set(before["tables"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("rehearse", "apply"))
    parser.add_argument("--state-db", type=Path, default=state.DEFAULT_STATE_DB)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = state.validate_state_db_path(args.state_db)
    output = runner._private_path(args.output, "output")
    before = fingerprint(path)
    if before["schema_version"] not in range(1, state.SCHEMA_VERSION + 1) or before["quick_check"] != ["ok"] or before["foreign_key_error_count"]:
        raise state.StateContractError("source database is not a healthy supported state")
    backups = []
    if args.mode == "rehearse":
        with tempfile.TemporaryDirectory(prefix="trading-state-rehearsal-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            copy_path = root / "review.sqlite3"
            old_umask = os.umask(0o077)
            try:
                with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
                    with sqlite3.connect(copy_path) as destination:
                        source.backup(destination)
            finally:
                os.umask(old_umask)
            copy_path.chmod(0o600)
            with state.open_state_store(copy_path, test_root=root):
                pass
            after = fingerprint(copy_path)
            added = verify(before, after)
            source_unchanged = fingerprint(path) == before
            if not source_unchanged:
                raise state.StateContractError("live source changed during rehearsal; retry from a fresh baseline")
    else:
        previous = set(path.parent.glob(path.name + ".backup-*.sqlite3"))
        with state.open_state_store(path):
            pass
        after = fingerprint(path)
        added = verify(before, after)
        for backup in sorted(set(path.parent.glob(path.name + ".backup-*.sqlite3")) - previous):
            if fingerprint(backup, standalone_backup=True) != before:
                raise state.StateContractError("pre-migration backup does not match baseline")
            backups.append(str(backup))
        if before["schema_version"] < state.SCHEMA_VERSION and not backups:
            raise state.StateContractError("migration backup was not found; verification incomplete")
        source_unchanged = None
    evidence = {
        "status": "passed", "mode": args.mode, "generated_at": state.utc_now(),
        "before": before, "after": after, "added_tables": added,
        "existing_rows_unchanged": True, "source_unchanged_during_rehearsal": source_unchanged,
        "backup_paths": backups,
    }
    runner._write_private_json(output, evidence)
    print(json.dumps({
        "status": "passed", "mode": args.mode,
        "schema_before": before["schema_version"], "schema_after": after["schema_version"],
        "added_tables": added, "existing_rows_unchanged": True,
        "backup_count": len(backups), "output": str(output),
    }))


if __name__ == "__main__":
    main()
