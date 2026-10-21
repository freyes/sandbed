"""The guest wrapper is plain bash; run it locally against harmless commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "sandbed" / "dev" / "data" / "run-stage.sh"


def run_stage(stage: Path, limit: int, *cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), str(stage), str(limit), "--", *cmd],
        capture_output=True, text=True, timeout=60,
    )


def test_success_leaves_pid_log_and_rc_zero(tmp_path) -> None:
    proc = run_stage(tmp_path / "s", 10, "echo", "hello")
    assert proc.returncode == 0 and "hello" in proc.stdout
    assert (tmp_path / "s" / "rc").read_text().strip() == "0"
    assert "hello" in (tmp_path / "s" / "out.log").read_text()
    assert (tmp_path / "s" / "pid").read_text().strip().isdigit()


def test_failure_exit_code_is_recorded_and_returned(tmp_path) -> None:
    proc = run_stage(tmp_path / "s", 10, "bash", "-c", "exit 7")
    assert proc.returncode == 7
    assert (tmp_path / "s" / "rc").read_text().strip() == "7"


def test_deadline_is_enforced_with_124(tmp_path) -> None:
    proc = run_stage(tmp_path / "s", 1, "sleep", "30")
    assert proc.returncode == 124
    assert (tmp_path / "s" / "rc").read_text().strip() == "124"


def test_rc_is_absent_until_the_work_ends(tmp_path) -> None:
    stage = tmp_path / "s"
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), str(stage), "20", "--", "sleep", "5"],
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if (stage / "pid").exists():
                break
            subprocess.run(["sleep", "0.1"])
        assert (stage / "pid").exists() and not (stage / "rc").exists()
    finally:
        proc.kill()
        proc.wait()


def test_arguments_are_not_re_parsed_by_a_shell(tmp_path) -> None:
    proc = run_stage(tmp_path / "s", 10, "echo", "a;b", "$HOME")
    assert "a;b $HOME" in proc.stdout
