from __future__ import annotations

import json
import multiprocessing
import os
import stat

import pytest

from sandbed.dev.store import (
    LockBusy,
    atomic_write_json,
    exclusive_lock,
    read_json,
    write_private,
)


def test_atomic_write_is_private_and_leaves_no_temp_files(tmp_path) -> None:
    target = tmp_path / "d" / "lab.json"
    atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert [p.name for p in target.parent.iterdir()] == ["lab.json"]


def test_failed_write_keeps_the_old_content(tmp_path) -> None:
    target = tmp_path / "lab.json"
    atomic_write_json(target, {"v": 1})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"v": object()})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["lab.json"]


def test_existing_directories_are_never_chmodded(tmp_path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    from sandbed.dev.store import ensure_private_dir

    ensure_private_dir(shared / "new" / "deeper")
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE((shared / "new").stat().st_mode) == 0o700
    assert stat.S_IMODE((shared / "new" / "deeper").stat().st_mode) == 0o700


def test_read_json_default(tmp_path) -> None:
    assert read_json(tmp_path / "missing", default=[]) == []


def _hold(path: str, ready, release) -> None:
    from pathlib import Path

    with exclusive_lock(Path(path)):
        ready.set()
        release.wait(10)


def test_lock_contention_across_processes(tmp_path) -> None:
    lock = tmp_path / "lock"
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold, args=(str(lock), ready, release))
    proc.start()
    try:
        assert ready.wait(10)
        with pytest.raises(LockBusy), exclusive_lock(lock, timeout=0.2):
            pass
    finally:
        release.set()
        proc.join(10)
    with exclusive_lock(lock):
        assert os.path.exists(lock)


def test_lock_is_reusable_after_release(tmp_path) -> None:
    for _ in range(2):
        with exclusive_lock(tmp_path / "lock"):
            pass


def test_write_private_is_0600_even_over_an_existing_wider_file(tmp_path) -> None:
    target = tmp_path / "d" / "f.txt"
    write_private(target, "one")
    target.chmod(0o666)
    write_private(target, b"two")
    assert target.read_bytes() == b"two"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
