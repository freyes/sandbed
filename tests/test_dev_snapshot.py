"""Uses a real throwaway git repository: the snapshot's whole job is to be a faithful clone."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sandbed.dev import snapshot
from sandbed.dev.errors import InputError

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    import os

    out = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, check=True,
        capture_output=True, text=True,
    )
    return out.stdout


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch) -> None:
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("one\n")
    (root / "run.sh").write_text("#!/bin/sh\n")
    (root / "run.sh").chmod(0o755)
    (root / ".gitignore").write_text("build/\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


def dirty(snap: Path) -> bool:
    r = subprocess.run(["git", "-C", str(snap), "diff-index", "--quiet", "HEAD", "--"])
    return r.returncode != 0


def test_clean_tree_is_a_clean_checkout_at_head(repo, tmp_path) -> None:
    info = snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )
    assert info.head == git(repo, "rev-parse", "HEAD").strip()
    assert info.dirty is False
    assert not dirty(tmp_path / "snap")
    assert (tmp_path / "snap" / ".git").exists()


def test_tracked_edits_are_applied_but_not_committed(repo, tmp_path) -> None:
    (repo / "a.txt").write_text("two\n")
    info = snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )
    assert (tmp_path / "snap" / "a.txt").read_text() == "two\n"
    assert info.dirty and dirty(tmp_path / "snap")
    assert git(tmp_path / "snap", "rev-parse", "HEAD").strip() == info.head


def test_staged_deleted_and_binary_changes_survive(repo, tmp_path) -> None:
    (repo / "run.sh").unlink()
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    git(repo, "add", "blob.bin")
    snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )
    assert not (tmp_path / "snap" / "run.sh").exists()
    assert (tmp_path / "snap" / "blob.bin").read_bytes() == bytes(range(256))


def test_untracked_files_need_an_explicit_include(repo, tmp_path) -> None:
    (repo / "new.py").write_text("x = 1\n")
    (repo / "build").mkdir()
    (repo / "build" / "out").write_text("ignored")
    info = snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )
    assert not (tmp_path / "snap" / "new.py").exists()
    assert info.excluded_untracked == ["new.py"]
    assert not dirty(tmp_path / "snap")


def test_included_untracked_file_makes_the_snapshot_dirty(repo, tmp_path) -> None:
    (repo / "new.py").write_text("x = 1\n")
    info = snapshot.capture(
        repo, tmp_path / "snap", include=["new.py"], manifest_path=tmp_path / "snap.json"
    )
    assert (tmp_path / "snap" / "new.py").read_text() == "x = 1\n"
    assert info.included_untracked == ["new.py"] and info.dirty
    assert dirty(tmp_path / "snap")


def test_ignored_or_unknown_include_is_rejected(repo, tmp_path) -> None:
    (repo / "build").mkdir()
    (repo / "build" / "out").write_text("x")
    with pytest.raises(InputError, match="not untracked"):
        snapshot.capture(
        repo, tmp_path / "snap", include=["build/out"], manifest_path=tmp_path / "snap.json"
    )


def test_content_digest_changes_with_content_and_is_stable(repo, tmp_path) -> None:
    first = snapshot.capture(
        repo, tmp_path / "s1", include=[], manifest_path=tmp_path / "s1.json"
    )
    again = snapshot.capture(
        repo, tmp_path / "s2", include=[], manifest_path=tmp_path / "s2.json"
    )
    (repo / "a.txt").write_text("changed\n")
    changed = snapshot.capture(
        repo, tmp_path / "s3", include=[], manifest_path=tmp_path / "s3.json"
    )
    assert first.content_sha256 == again.content_sha256
    assert changed.content_sha256 != first.content_sha256


def test_executable_bit_is_preserved(repo, tmp_path) -> None:
    snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )
    assert (tmp_path / "snap" / "run.sh").stat().st_mode & 0o111


def test_symlink_escaping_the_tree_is_rejected(repo, tmp_path) -> None:
    (repo / "evil").symlink_to("/etc/passwd")
    git(repo, "add", "evil")
    git(repo, "commit", "-q", "-m", "link")
    with pytest.raises(InputError, match="outside"):
        snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )


def test_existing_destination_is_refused(repo, tmp_path) -> None:
    (tmp_path / "snap").mkdir()
    with pytest.raises(InputError, match="already exists"):
        snapshot.capture(
        repo, tmp_path / "snap", include=[], manifest_path=tmp_path / "snap.json"
    )