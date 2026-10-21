"""Atomic JSON files and advisory locks. Private by default (dirs 0700, files 0600)."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import DevError


class LockBusy(DevError):
    """Another sandbed process holds the lock."""


def ensure_private_dir(path: Path) -> Path:
    """Create `path` and any missing parents with mode 0700. Existing directories are never
    chmod-ed: callers may pass a directory they do not own (e.g. a log path's parent)."""
    missing = []
    for candidate in (path, *path.parents):
        if candidate.exists():
            break
        missing.append(candidate)
    for candidate in reversed(missing):
        candidate.mkdir(mode=0o700, exist_ok=True)
        os.chmod(candidate, 0o700)  # mkdir's mode is masked by the umask
    return path


def write_private(path: Path, data: bytes | str) -> None:
    """Write `data` to `path` with mode 0600 from the first byte (no world-readable window)."""
    ensure_private_dir(path.parent)
    payload = data.encode() if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # O_CREAT's mode is ignored for an existing wider file
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    os.chmod(path, 0o600)


def atomic_write_json(path: Path, data: object, *, mode: int = 0o600) -> None:
    """Write via a temp file in the same directory and rename, so readers never see a torn file."""
    ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: object = None) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text())


@contextmanager
def exclusive_lock(path: Path, *, timeout: float = 0.0) -> Iterator[None]:
    """flock(2) on `path`. The kernel drops the lock if the process dies, so a stale lock file
    is harmless; a *held* lock is not proof that guest-side work has ended."""
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(f"lock is held by another process: {path}") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)
