"""`sandbed dev build --detach`: a real forked worker, with the build itself faked."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sandbed.cli import cli
from sandbed.dev import commands
from sandbed.dev.errors import BuildError, HostError, InputError
from sandbed.dev.labs import LabEntry, Registry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash

UID = os.getuid()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture
def lab(state):
    reg = Registry()
    uid = str(uuid.uuid4())
    reg.add(LabEntry(uid, "a", UID, ["10.213.0.0/24"]))
    LabStore(lab_paths(uid, reg.root)).save(
        LabRecord(uid, "a", UID, lab_prefix(uid), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                  config_hash(DEFAULT_CONFIG)))
    return lab_paths(uid, reg.root)


PROVENANCE = {
    "artifact_id": "a-20260101T000000-deadbeef",
    "snap": {"sha256": "s" * 64, "version": "1.0"},
    "adapter_qualified": True,
    "source": {"head": "h" * 40, "content_sha256": "c" * 64},
}


def gated_build(gate: Path, error: Exception | None = None):
    """A stand-in for build_artifact: records a running attempt, waits for `gate`, then ends."""

    def fake(ctx, run_id, _source, _include):
        attempt = ctx.log.begin(run_id, "build.pack", "")
        os.write(2, b"snapcraft progress\n")  # fd level: pytest swaps sys.stderr
        deadline = time.monotonic() + 20
        while not gate.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if error:
            raise error
        ctx.log.finish(attempt, "succeeded", exit_code=0)
        return PROVENANCE

    return fake


def wait_for(path: Path, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.02)
    time.sleep(0.05)
    return json.loads(path.read_text())


def start(tmp_path, gate, error=None):
    with (
        patch.object(commands, "require_host"),
        patch.object(commands, "build_artifact", gated_build(gate, error)),
    ):
        return commands.build_detached("a", str(tmp_path), ())


def test_detach_returns_running_at_once_and_status_reports_the_outcome(lab, tmp_path) -> None:
    gate = tmp_path / "gate"
    out = start(tmp_path, gate)
    assert out["status"] == "running" and out["detached"] is True
    assert out["label"] == "a" and out["run_id"] and out["pid"] != os.getpid()
    assert Path(out["log_path"]).parent == lab.run(out["run_id"])

    running = commands.status("a")  # the worker holds the lab lock; status must still answer
    assert running["busy"] is True
    assert running["last_run"]["run_id"] == out["run_id"]
    assert (running["last_run"]["kind"], running["last_run"]["state"]) == ("build", "running")
    assert "build.pack is still running" in running["blocked_reason"]

    gate.touch()
    result = wait_for(Path(out["result_path"]))
    assert (result["command"], result["status"]) == ("build", "succeeded")
    assert result["artifact_id"] == PROVENANCE["artifact_id"]
    assert "human" not in result and "exit_code" not in result

    done = commands.status("a")
    assert done["busy"] is False and done["blocked_reason"] is None
    assert done["last_run"]["state"] == "succeeded"
    assert done["last_run"]["artifact_id"] == PROVENANCE["artifact_id"]
    assert "snapcraft progress" in Path(out["log_path"]).read_text()


def test_a_build_failure_after_start_is_recorded_not_raised(lab, tmp_path) -> None:
    gate = tmp_path / "gate"
    gate.touch()
    out = start(tmp_path, gate, BuildError("pack failed", stage="build.pack"))
    result = wait_for(Path(out["result_path"]))
    assert result["status"] == "failed" and result["failure_category"] == "build"
    assert result["failed_stage"] == "build.pack" and result["run_id"] == out["run_id"]
    last = commands.status("a")["last_run"]
    assert last["state"] == "failed" and last["result"]["message"] == "pack failed"


def test_a_second_detached_build_is_refused_while_one_runs(lab, tmp_path) -> None:
    gate = tmp_path / "gate"
    first = start(tmp_path, gate)
    with pytest.raises(InputError, match="lock is held"):
        start(tmp_path, gate)
    gate.touch()
    wait_for(Path(first["result_path"]))


def test_host_not_ready_fails_synchronously_with_its_own_category(lab, tmp_path) -> None:
    with patch.object(commands, "require_host", side_effect=HostError("no snapcraft")):
        with pytest.raises(HostError, match="no snapcraft"):
            commands.build_detached("a", str(tmp_path), ())
    assert not list(lab.root.glob("runs/*"))


def test_an_unknown_lab_fails_before_forking(state, tmp_path) -> None:
    with patch("sandbed.dev.detach.os.fork") as fork:
        with pytest.raises(InputError, match="no such lab"):
            commands.build_detached("missing", str(tmp_path), ())
    fork.assert_not_called()


def test_a_worker_that_dies_before_starting_is_an_internal_error(lab, tmp_path) -> None:
    from sandbed.dev import detach
    from sandbed.dev.errors import InternalError

    def dies(_started):
        os._exit(9)

    with pytest.raises(InternalError, match="before starting"):
        detach.start("build", dies)


def test_the_cli_routes_detach_to_the_background_command() -> None:
    with (
        patch.object(commands, "build_detached", return_value={
            "status": "running", "run_id": "r", "human": "started"}) as detached,
        patch.object(commands, "build") as foreground,
    ):
        result = CliRunner().invoke(
            cli, ["dev", "build", "--id", "a", "--source", ".", "--detach", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "running"
    detached.assert_called_once_with("a", ".", ())
    foreground.assert_not_called()
