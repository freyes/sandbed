import stat
from pathlib import Path

import pytest

from sandbed.config import load_config
from sandbed.hooks import find_hooks, run_hook


def _write_hook(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_find_hooks_only_executables(tmp_path) -> None:
    _write_hook(tmp_path, "pre-run", "exit 0")
    _write_hook(tmp_path, "terraform.post", "exit 0")
    (tmp_path / "not-executable.sh").write_text("#!/bin/sh\n")
    (tmp_path / "README.md").write_text("# readme")
    assert find_hooks(str(tmp_path)) == ["pre-run", "terraform.post"]


def test_run_hook_sets_env(tmp_path) -> None:
    _write_hook(
        tmp_path,
        "preflight.pre",
        'test "$SDB_PHASE" = "preflight" && test "$SDB_PROVIDER" = "manual" || exit 1\n',
    )
    run_hook(
        "preflight.pre",
        load_config("manual", "up", hooks_dir=str(tmp_path)),
        phase="preflight",
    )


def test_run_hook_nonzero_aborts(tmp_path) -> None:
    _write_hook(tmp_path, "pre-run", "exit 3\n")
    with pytest.raises(RuntimeError):
        run_hook("pre-run", load_config("manual", "up", hooks_dir=str(tmp_path)))


def test_run_hook_skips_when_disabled(tmp_path) -> None:
    _write_hook(tmp_path, "pre-run", "exit 1\n")
    run_hook("pre-run", load_config("manual", "up", hooks_dir=str(tmp_path), no_hooks=True))