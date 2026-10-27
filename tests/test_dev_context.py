from __future__ import annotations

import json
import multiprocessing
import uuid

import pytest
from click.testing import CliRunner

from sandbed.cli import cli
from sandbed.dev import context
from sandbed.dev.context import open_lab
from sandbed.dev.errors import FailureCategory, InputError
from sandbed.dev.labs import LabEntry, Registry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.state import EXEC_STOPPED, EXEC_UNKNOWN, AttemptLog
from sandbed.dev.store import LockBusy, exclusive_lock

UID = 1000


def make_lab(tmp_path, label="a"):
    reg = Registry(tmp_path)
    lab = str(uuid.uuid4())
    reg.add(LabEntry(lab, label, UID, ["10.213.0.0/24"]))
    paths = lab_paths(lab, tmp_path)
    LabStore(paths).save(
        LabRecord(lab, label, UID, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                  config_hash(DEFAULT_CONFIG))
    )
    return reg, paths


def stopped(_paths, _record):
    return lambda _a: EXEC_STOPPED


def unknown(_paths, _record):
    return lambda _a: EXEC_UNKNOWN


def test_record_roundtrip_and_home(tmp_path) -> None:
    reg, paths = make_lab(tmp_path)
    record = LabStore(paths).load()
    assert record.home == "/home/ubuntu" and record.provisioning_state == "new"
    assert record.config["vcpus"] == 8 and record.config["memory"] == 32768


def test_unknown_lab_and_other_owner_are_rejected(tmp_path) -> None:
    reg, _ = make_lab(tmp_path)
    with pytest.raises(InputError):
        with open_lab("missing", mutate=False, registry=reg, uid=UID, probe=stopped):
            pass
    with pytest.raises(InputError):
        with open_lab("a", mutate=False, registry=reg, uid=UID + 1, probe=stopped):
            pass


def test_the_lab_lock_is_held_for_the_whole_command(tmp_path) -> None:
    reg, paths = make_lab(tmp_path)
    with open_lab("a", mutate=True, registry=reg, uid=UID, probe=stopped):
        with pytest.raises(LockBusy), exclusive_lock(paths.lock, timeout=0.1):
            pass


def test_orphaned_attempt_with_unknown_work_blocks_mutation_but_not_collection(
    tmp_path, monkeypatch
) -> None:
    reg, paths = make_lab(tmp_path)
    AttemptLog(paths.attempts).begin("r", "scenario.bootstrap", "")
    monkeypatch.setattr("sandbed.dev.state.process_identity", lambda _pid: None)
    with pytest.raises(InputError, match="unknown"):
        with open_lab("a", mutate=True, registry=reg, uid=UID, probe=unknown):
            pass
    with open_lab("a", mutate=False, registry=reg, uid=UID, probe=unknown) as ctx:
        assert ctx.log.attempts()[0].state == "interrupted"


def test_orphan_confirmed_stopped_unblocks(tmp_path, monkeypatch) -> None:
    reg, paths = make_lab(tmp_path)
    AttemptLog(paths.attempts).begin("r", "provision", "")
    monkeypatch.setattr("sandbed.dev.state.process_identity", lambda _pid: None)
    with open_lab("a", mutate=True, registry=reg, uid=UID, probe=stopped) as ctx:
        assert ctx.log.blocked_reason() is None


def test_target_requires_a_discovered_address(tmp_path) -> None:
    reg, _ = make_lab(tmp_path)
    with open_lab("a", mutate=False, registry=reg, uid=UID, probe=stopped) as ctx:
        with pytest.raises(InputError):
            ctx.target()
        ctx.record.vm_address = "10.213.0.9"
        assert ctx.target().host == "10.213.0.9" and ctx.target().user == "ubuntu"


def test_probe_dispatch_by_stage(tmp_path, monkeypatch) -> None:
    reg, paths = make_lab(tmp_path)
    record = LabStore(paths).load()
    seen = []
    monkeypatch.setattr(context, "host_probe", lambda _p: lambda a: seen.append("host") or "x")
    probe = context.make_probe(paths, record)  # no VM address: guest probe unavailable
    from sandbed.dev.state import Attempt

    def attempt(stage):
        return Attempt("r", stage, 1, "running", "", "now")

    assert probe(attempt("provision")) == "x" and seen == ["host"]
    assert probe(attempt("scenario.bootstrap")) == EXEC_UNKNOWN
    assert probe(attempt("build.pack")) == EXEC_STOPPED


def test_host_probe_matches_only_this_labs_directory_literally(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    weird = tmp_path / "a.b+c[1] d"
    paths = lab_paths("u", weird)
    with patch.object(context, "run", return_value=MagicMock(returncode=0)) as fake:
        assert context.host_probe(paths)(None) == "running"
    pattern = fake.call_args.args[0][-1]
    assert pattern.startswith("-chdir=") and "\\." in pattern and "\\+" in pattern
    assert "\\[1\\]" in pattern
    with patch.object(context, "run", return_value=MagicMock(returncode=1)):
        assert context.host_probe(paths)(None) == "stopped"
    assert context.ere_escape("a.b*c") == "a\\.b\\*c"


def _hold_lock(path: str, ready, release) -> None:
    from pathlib import Path

    with exclusive_lock(Path(path)):
        ready.set()
        release.wait(10)


def test_open_lab_on_a_held_lock_is_an_input_category_error(tmp_path) -> None:
    """Lock contention is an incompatible-state input error (exit 2), not an internal bug."""
    reg, paths = make_lab(tmp_path)
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold_lock, args=(str(paths.lock), ready, release))
    proc.start()
    try:
        assert ready.wait(10)
        with pytest.raises(LockBusy) as exc_info:
            with open_lab("a", mutate=False, registry=reg, uid=UID, probe=stopped):
                pass
    finally:
        release.set()
        proc.join(10)
    err = exc_info.value
    assert err.category is FailureCategory.INPUT
    assert err.exit_code == 2


def test_held_lab_lock_surfaces_as_input_error_through_the_cli(tmp_path, monkeypatch) -> None:
    """`status` stays readable under a held lock (busy: true); a mutating open still exits 2."""
    import os

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    from sandbed.dev.paths import state_root

    root = state_root()
    reg = Registry(root)
    lab = str(uuid.uuid4())
    uid = os.getuid()
    reg.add(LabEntry(lab, "a", uid, ["10.213.0.0/24"]))
    paths = lab_paths(lab, root)
    LabStore(paths).save(
        LabRecord(lab, "a", uid, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                  config_hash(DEFAULT_CONFIG))
    )
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold_lock, args=(str(paths.lock), ready, release))
    proc.start()
    try:
        assert ready.wait(10)
        monkeypatch.setattr("sandbed.dev.commands.systemd.show", lambda unit, env=None: None)
        result = CliRunner().invoke(cli, ["dev", "status", "--id", "a", "--json"])
        with pytest.raises(LockBusy) as exc_info:
            with open_lab("a", mutate=True):
                pass
    finally:
        release.set()
        proc.join(10)
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["busy"] is True
    assert exc_info.value.exit_code == 2
    assert exc_info.value.category is FailureCategory.INPUT
    assert "lock is held" in str(exc_info.value)
