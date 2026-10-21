"""Freeze a source tree (tracked changes plus chosen untracked files) as a *git checkout*.

snap-openstack's snapcraft.yaml derives the snap version from `git rev-parse HEAD` and
`git diff-index` in the project directory, so an exported tree without `.git` would not build.
The snapshot is a throwaway clone at HEAD with the working-tree changes applied but not
committed, which makes snapcraft stamp the version `...-<sha>+dirty` on its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from ..shell import run
from .errors import InputError


@dataclass(frozen=True)
class SnapshotInfo:
    root: str
    head: str
    origin: str | None
    dirty: bool
    diff_sha256: str
    content_sha256: str
    included_untracked: list[str]
    excluded_untracked: list[str]

    def to_json(self) -> dict:
        return asdict(self)


def _git(root: Path | str, *args: str) -> str:
    return run(["git", "-C", str(root), *args]).stdout


def _split_z(raw: str) -> list[str]:
    return [item for item in raw.split("\0") if item]


def _check_supported(root: Path, tracked: list[str], include: list[str]) -> None:
    staged = _git(root, "ls-files", "--stage", "-z")
    for entry in _split_z(staged):
        if entry.startswith("160000 "):
            raise InputError(f"submodules are not supported: {entry.split(chr(9), 1)[1]}")
    real_root = os.path.realpath(root)
    for rel in [*tracked, *include]:
        path = root / rel
        if path.is_symlink() and not os.path.realpath(path).startswith(real_root + os.sep):
            raise InputError(f"symlink points outside the source tree: {rel}")


def _manifest(dest: Path, files: list[str]) -> list[dict]:
    entries = []
    for rel in sorted(files):
        path = dest / rel
        if not path.exists() and not path.is_symlink():
            continue  # deleted by the working-tree changes
        digest = hashlib.sha256(
            os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        ).hexdigest()
        mode = "symlink" if path.is_symlink() else ("755" if os.access(path, os.X_OK) else "644")
        entries.append({"path": rel, "mode": mode, "sha256": digest})
    return entries


def capture(
    source: Path, dest: Path, *, include: list[str], manifest_path: Path
) -> SnapshotInfo:
    """Create `dest` (must not exist) from the git repository containing `source` and write
    the ordered content manifest (path, mode, sha256 per file) to `manifest_path`."""
    if dest.exists():
        raise InputError(f"snapshot destination already exists: {dest}")
    top = _git(source, "rev-parse", "--show-toplevel").strip()
    root = Path(top)
    head = _git(root, "rev-parse", "HEAD").strip()
    origin = run(["git", "-C", top, "remote", "get-url", "origin"], check=False).stdout.strip()

    untracked = _split_z(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    unknown = sorted(set(include) - set(untracked))
    if unknown:
        raise InputError(f"--include paths are not untracked, unignored files: {unknown}")
    tracked = _split_z(_git(root, "ls-files", "-z"))
    _check_supported(root, tracked, include)

    diff = _git(root, "diff", "--binary", "HEAD")

    old_umask = os.umask(0o022)  # snapcraft fails on odd modes; see snap-openstack CONTRIBUTING
    try:
        run(["git", "clone", "--quiet", "--no-checkout", "--no-hardlinks", top, str(dest)])
        _git(dest, "checkout", "--quiet", "--detach", head)
        if diff:
            patch = dest.parent / f".{dest.name}.patch"
            patch.write_text(diff)
            try:
                _git(dest, "apply", "--index", "--binary", str(patch))
            finally:
                patch.unlink(missing_ok=True)
        for rel in include:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target, follow_symlinks=False)
        if include:
            # Intent-to-add makes `git diff-index HEAD` (and so snapcraft's +dirty) see them.
            _git(dest, "add", "--intent-to-add", "--", *include)
    finally:
        os.umask(old_umask)

    files = _split_z(_git(dest, "ls-files", "-z")) + list(include)
    content = json.dumps(_manifest(dest, files), sort_keys=True).encode()
    manifest_path.write_bytes(content)
    return SnapshotInfo(
        root=str(root),
        head=head,
        origin=origin or None,
        dirty=bool(diff) or bool(include),
        diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        content_sha256=hashlib.sha256(content).hexdigest(),
        included_untracked=sorted(include),
        excluded_untracked=sorted(set(untracked) - set(include)),
    )