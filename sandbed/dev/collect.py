"""Bounded, allowlisted diagnostics. Raw output stays in the lab's private directory; only
redacted, size-capped copies of named collectors (and sanitized archive members) are exported."""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .redact import redact
from .store import atomic_write_json, ensure_private_dir, write_private

OK, FAILED, TIMEOUT, UNAVAILABLE = "ok", "failed", "timeout", "unavailable"

# Archive members that are never exported, whatever their content.
_UNSAFE_NAME = re.compile(
    r"(openrc|\.tfstate|id_(rsa|ed25519|ecdsa)|\.pem$|\.key$|kubeconfig|credential|secret|"
    r"password|\.env$|cacert|/ssh/)",
    re.I,
)
_TEXT_SUFFIXES = (".log", ".txt", ".yaml", ".yml", ".json", ".conf", ".out")


@dataclass(frozen=True)
class Collector:
    name: str
    argv: tuple[str, ...]
    where: str  # "host" or "guest"
    timeout: float = 120.0
    max_bytes: int = 1_000_000


# (status, text, error) - an executor never raises; failures are data.
Executor = Callable[[Collector], tuple[str, str, str]]


def collect(
    collectors: Iterable[Collector],
    execute: Executor,
    *,
    raw_dir: Path,
    out_dir: Path,
    extra: tuple[str, ...] = (),
) -> dict:
    ensure_private_dir(raw_dir)
    ensure_private_dir(out_dir)
    entries = []
    for c in collectors:
        try:
            status, text, error = execute(c)
        except Exception as err:  # a collector must never abort the others
            status, text, error = FAILED, "", f"{type(err).__name__}: {err}"
        write_private(raw_dir / f"{c.name}.txt", text)
        exported = redact(text, extra).encode()
        truncated = len(exported) > c.max_bytes
        if truncated:
            exported = exported[: c.max_bytes] + b"\n[truncated by sandbed]\n"
        write_private(out_dir / f"{c.name}.txt", exported)
        entries.append({
            "name": c.name, "where": c.where, "status": status, "error": error,
            "truncated": truncated, "bytes": len(exported),
            "sha256": hashlib.sha256(exported).hexdigest(),
        })
    index = {"entries": entries, "complete": all(e["status"] == OK for e in entries)}
    atomic_write_json(out_dir / "index.json", index)
    return index


def sanitize_archive(
    src: Path, out_dir: Path, *, max_member_bytes: int = 2_000_000, extra: tuple[str, ...] = ()
) -> list[dict]:
    """Copy only safe, text, size-capped, redacted members of a tar(.gz) into `out_dir`.
    Returns one record per omitted member (name + reason); nothing is silently dropped."""
    ensure_private_dir(out_dir)
    omitted: list[dict] = []
    written: set[str] = set()  # relative POSIX paths of exported files
    with tarfile.open(src) as tar:
        for member in tar:
            name = member.name
            reason = None
            if not member.isfile():
                reason = "not a regular file" if not member.isdir() else None
            elif name.startswith("/") or ".." in Path(name).parts:
                reason = "unsafe path"
            elif _UNSAFE_NAME.search(name):
                reason = "sensitive name"
            elif not name.endswith(_TEXT_SUFFIXES):
                reason = "not an allowlisted text type"
            elif member.size > max_member_bytes:
                reason = "too large"
            if member.isdir():
                continue
            if reason:
                omitted.append({"member": name, "reason": reason})
                continue
            handle = tar.extractfile(member)
            assert handle is not None
            text = redact(handle.read().decode(errors="replace"), extra)
            rel = PurePosixPath(name).as_posix()  # already validated: relative, no `..`
            parts = PurePosixPath(rel).parts
            if rel in written:
                omitted.append({"member": name, "reason": "duplicate member"})
                continue
            ancestors = {PurePosixPath(*parts[:i]).as_posix() for i in range(1, len(parts))}
            if ancestors & written or any(w.startswith(rel + "/") for w in written):
                omitted.append({"member": name, "reason": "path conflicts with another member"})
                continue
            write_private(out_dir / rel, text)
            written.add(rel)
    return omitted


def make_archive(out_dir: Path, archive: Path) -> Path:
    """tar.gz of exactly the files under `out_dir` (never anything outside it)."""
    ensure_private_dir(archive.parent)
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                info = tarfile.TarInfo(str(path.relative_to(out_dir)))
                info.size, info.mode = len(data), 0o600
                tar.addfile(info, io.BytesIO(data))
    archive.chmod(0o600)
    return archive