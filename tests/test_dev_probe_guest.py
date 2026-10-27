"""probe-guest.sh against a fake `ssh` placed first on PATH."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "sandbed" / "dev" / "data" / "probe-guest.sh"


def fake_ssh(bin_dir: Path, body: str) -> dict[str, str]:
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text("#!/usr/bin/env bash\n" + body)
    ssh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def probe(env, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_done_status_passes_and_target_is_the_given_address(tmp_path) -> None:
    env = fake_ssh(tmp_path / "bin", 'echo "ARGS: $*" >&2; echo "status: done"\n')
    proc = probe(env, "/key", "10.20.20.5", "30")
    assert proc.returncode == 0 and "status: done" in proc.stdout


def test_error_status_fails_without_retrying_forever(tmp_path) -> None:
    env = fake_ssh(tmp_path / "bin", 'echo "status: error"\n')
    proc = probe(env, "/key", "10.20.20.5", "30")
    assert proc.returncode == 1 and "did not finish cleanly" in proc.stderr


def test_unreachable_instance_fails_at_the_deadline(tmp_path) -> None:
    env = fake_ssh(tmp_path / "bin", 'echo "ssh: connect: no route" ; exit 255\n')
    proc = probe(env, "/key", "10.20.20.5", "1")
    assert proc.returncode == 1 and "deadline reached" in proc.stderr


def test_bad_address_is_rejected_before_any_ssh(tmp_path) -> None:
    env = fake_ssh(tmp_path / "bin", "touch %s/ran\n" % tmp_path)
    proc = probe(env, "/key", "10.0.0.1; reboot", "1")
    assert proc.returncode == 64 and not (tmp_path / "ran").exists()


def test_every_staged_guest_script_exists_and_is_executable() -> None:
    from sandbed.dev.provision import GUEST_SCRIPTS

    for name in GUEST_SCRIPTS:
        path = SCRIPT.parent / name
        assert path.is_file() and os.access(path, os.X_OK), name