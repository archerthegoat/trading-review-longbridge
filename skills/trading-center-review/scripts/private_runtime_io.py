#!/usr/bin/env python3
"""Owner-only atomic text output for trading-review private runtime artifacts."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


PRIVATE_ROOT = Path("/private/tmp/trading-center-review-runtime").resolve()


class PrivateRuntimeError(RuntimeError):
    """A private artifact path violates the runtime boundary."""


def prepare_private_output(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise PrivateRuntimeError("output must be an absolute non-symlink path")
    lexical = expanded.absolute()
    try:
        relative = lexical.relative_to(PRIVATE_ROOT)
    except ValueError as exc:
        raise PrivateRuntimeError(f"output must be below {PRIVATE_ROOT}") from exc
    if not relative.parts:
        raise PrivateRuntimeError("output must name a file below the private runtime root")

    probe = PRIVATE_ROOT
    if not probe.exists():
        probe.mkdir(mode=0o700)
    if probe.is_symlink() or not probe.is_dir():
        raise PrivateRuntimeError("private runtime root must be a real directory")
    root_info = probe.stat()
    if root_info.st_uid != os.getuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
        raise PrivateRuntimeError("private runtime root must be current-user 0700")
    for part in relative.parts[:-1]:
        probe = probe / part
        if probe.is_symlink():
            raise PrivateRuntimeError("output must not traverse a symbolic link")
        if not probe.exists():
            probe.mkdir(mode=0o700)
        if not probe.is_dir():
            raise PrivateRuntimeError("output parent must be a directory")
        info = probe.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PrivateRuntimeError("private runtime directories must be current-user 0700")

    destination = lexical.resolve(strict=False)
    try:
        destination.relative_to(PRIVATE_ROOT)
    except ValueError as exc:
        raise PrivateRuntimeError("output resolved outside the private runtime root") from exc
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise PrivateRuntimeError("output must be a regular non-symlink file")
        info = destination.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise PrivateRuntimeError("existing private output must be current-user 0600")
    return destination


def write_owner_only_text(path: Path, content: str) -> Path:
    destination = prepare_private_output(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=str(destination.parent),
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_name, destination)
        os.chmod(destination, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            Path(temporary_name).unlink()
        except OSError:
            pass
        raise
    return destination
