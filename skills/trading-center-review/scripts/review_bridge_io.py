"""Owner-only, immutable bridge artifacts and explicit single-writer locks."""

from __future__ import annotations

import contextlib
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path

from review_journal_contract import JournalError, MAX_BYTES, canonical, parse_json

PRODUCER_ROOT = Path.home() / "Library/Application Support/MarsTradingCenter/bridge"
RECEIVER_ROOT = Path.home() / "Library/Application Support/MarsKnowledgeCenter/trading-review-bridge/state"
VAULT = Path.home() / "Documents/ChatGPT/个人知识中心/Mars知识库vault"


def no_links(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise JournalError("bridge_requires_absolute_safe_path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise JournalError("bridge_symlink_forbidden")


def identity(path: Path, *, private: bool = True) -> dict:
    no_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise JournalError("bridge_file_identity_invalid")
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        raise JournalError("bridge_requires_owner_only_file")
    return {"device": info.st_dev, "inode": info.st_ino}


def read_file(path: Path, *, private: bool = True, limit: int = MAX_BYTES) -> bytes:
    before = identity(path, private=private)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if before != {"device": info.st_dev, "inode": info.st_ino} or info.st_size > limit:
            raise JournalError("bridge_file_changed_or_too_large")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            result = handle.read(limit + 1)
        if len(result) > limit or identity(path, private=private) != before:
            raise JournalError("bridge_file_changed_or_too_large")
        return result
    finally:
        os.close(fd)


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_exclusive(path: Path, content: bytes) -> bool:
    """Complete file -> same-filesystem exclusive link. Never rename over a target."""
    no_links(path)
    fd, temp_name = tempfile.mkstemp(prefix=".review-bridge-", dir=str(path.parent))
    temporary = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        no_links(path)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            return False
        # Remove our extra link before any normal reader's hardlink check.
        temporary.unlink()
        fsync_directory(path.parent)
        return True
    finally:
        if temporary.exists():
            temporary.unlink()


class PrivateTree:
    def __init__(self, root: Path, *, kind: str, test_root: Path | None = None):
        expected = {"producer": PRODUCER_ROOT, "receiver": RECEIVER_ROOT}.get(kind)
        no_links(root)
        if expected is None:
            raise JournalError("bridge_tree_kind_invalid")
        if test_root is None:
            if root != expected:
                raise JournalError("bridge_root_not_authorized")
        else:
            no_links(test_root)
            if test_root.resolve() == Path("/") or test_root.resolve() not in root.parents:
                raise JournalError("invalid_isolated_test_root")
        for ancestor in [root, *root.parents]:
            if (ancestor / ".git").exists() or (ancestor / ".obsidian").exists():
                raise JournalError("private_bridge_tree_cannot_be_in_git_or_vault")
        self.root = root

    def path(self, relative: str, *, create_parent: bool = False) -> Path:
        pieces = relative.split("/")
        if not pieces or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in {".", ".."} for p in pieces):
            raise JournalError("invalid_bridge_relative_path")
        path = self.root.joinpath(*pieces)
        no_links(path)
        if create_parent:
            # Only the task's fixed root and descendants are made owner-only.
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            cursor = self.root
            for part in pieces[:-1]:
                cursor /= part
                cursor.mkdir(mode=0o700, exist_ok=True)
        if self.root.exists():
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise JournalError("bridge_requires_owner_only_directory")
        cursor = self.root
        for part in pieces[:-1]:
            cursor /= part
            if cursor.exists():
                info = cursor.lstat()
                if cursor.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise JournalError("bridge_requires_owner_only_directory")
        return path

    def read(self, relative: str) -> bytes:
        return read_file(self.path(relative))

    def write_once(self, relative: str, content: bytes) -> Path:
        path = self.path(relative, create_parent=True)
        if not create_exclusive(path, content) and read_file(path) != content:
            raise JournalError("immutable_bridge_artifact_conflict")
        if read_file(path) != content:
            raise JournalError("bridge_write_readback_mismatch")
        return path

    @contextlib.contextmanager
    def lock(self, name: str):
        path = self.path(f"locks/{name}.json", create_parent=True)
        token = canonical({"pid": os.getpid(), "nonce": uuid.uuid4().hex})
        if not create_exclusive(path, token):
            raise JournalError("bridge_busy_or_unrecovered_lock")
        try:
            yield
        finally:
            if read_file(path) == token:
                path.unlink()
                fsync_directory(path.parent)

    def recover_lock(self, name: str, expected_pid: int) -> str:
        path = self.path(f"locks/{name}.json")
        data = self.read(f"locks/{name}.json")
        obj = parse_json(data)
        if set(obj) != {"pid", "nonce"} or type(expected_pid) is not int or expected_pid <= 0 or obj["pid"] != expected_pid:
            raise JournalError("lock_recovery_identity_mismatch")
        try:
            os.kill(expected_pid, 0)
        except ProcessLookupError:
            if read_file(path) != data:
                raise JournalError("lock_changed_during_recovery")
            path.unlink()
            fsync_directory(path.parent)
            return "dead_lock_recovered"
        raise JournalError("lock_owner_still_exists")
