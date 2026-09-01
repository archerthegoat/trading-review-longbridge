#!/usr/bin/env python3
"""Install/verify a knowledge-owned receiver outside Git and the Obsidian Vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from review_bridge_io import PrivateTree, RECEIVER_ROOT, fsync_directory, read_file
from review_journal_contract import JournalError, canonical, digest, exact, parse_json

FILES = ("review_bridge_receiver.py", "review_bridge_io.py", "review_journal_contract.py", "obsidian_managed_update.js")


def verify(tree: PrivateTree, *, pointer: str = "installation.json") -> dict:
    record = parse_json(tree.read(pointer))
    exact(record, {"schema_version", "code_id", "files", "python"}, "receiver_installation_fields_invalid")
    if record["schema_version"] != "knowledge-review-receiver.v1" or set(record["files"]) != set(FILES) or record["python"] != "/usr/bin/python3" or digest(record["files"]) != record["code_id"]:
        raise JournalError("receiver_installation_invalid")
    for name in FILES:
        if hashlib.sha256(tree.read(f'code/{record["code_id"]}/{name}')).hexdigest() != record["files"][name]:
            raise JournalError("receiver_code_integrity_failed")
    return record


def write_pointer(tree: PrivateTree, name: str, content: bytes):
    destination = tree.path(name, create_parent=True)
    if destination.exists():
        read_file(destination)
    fd, temporary_name = tempfile.mkstemp(prefix=".receiver-install-", dir=str(destination.parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def install(tree: PrivateTree, *, source: Path = Path(__file__).parent) -> dict:
    contents = {name: (source / name).read_bytes() for name in FILES}
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}
    info = {"schema_version": "knowledge-review-receiver.v1", "code_id": digest(hashes), "files": hashes, "python": "/usr/bin/python3"}
    with tree.lock("install"):
        for name, content in contents.items():
            tree.write_once(f'code/{info["code_id"]}/{name}', content)
        if tree.path("installation.json").exists():
            previous = verify(tree)
            if previous == info:
                return info
            write_pointer(tree, "installation.previous.json", canonical(previous))
        write_pointer(tree, "installation.json", canonical(info))
        verify(tree)
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status", "rollback"))
    args = parser.parse_args()
    tree = PrivateTree(RECEIVER_ROOT, kind="receiver")
    try:
        if args.command == "install":
            info = install(tree)
        elif args.command == "rollback":
            with tree.lock("install"):
                verify(tree)
                info = verify(tree, pointer="installation.previous.json")
                write_pointer(tree, "installation.json", canonical(info))
        else:
            info = verify(tree)
        print(json.dumps({"status": "installed_verified", "code_id": info["code_id"], "receiver": str(tree.path(f'code/{info["code_id"]}/review_bridge_receiver.py')), "python": info["python"]}))
        return 0
    except (JournalError, OSError) as error:
        print(json.dumps({"status": "blocked", "error_category": str(error) if isinstance(error, JournalError) else "receiver_install_io_failure"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
