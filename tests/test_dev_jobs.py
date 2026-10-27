from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import pytest

from sandbed.dev import jobs
from sandbed.dev.errors import InputError
from sandbed.dev.labs import LabEntry, Registry
from sandbed.dev.store import atomic_write_json

DEV = Path(jobs.__file__).parent
TIMEOUT_MODULES = ("provision", "build", "deploy", "scenarios", "diagnostics", "destroy")
OLD = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def make_job(**kw) -> jobs.Job:
    args = {"command": "build", "params": {"a": 1}, "lab_ref": "x", "cwd": "/w", "detach": True}
    return jobs.create_job(**{**args, **kw})


def test_job_files_are_private() -> None:
    job = make_job()
    jobs.write_env_file(job, {"PATH": "/bin"})
    p = job.paths
    assert mode(p.root) == 0o700
    assert [mode(f) for f in (p.job, p.stdout, p.stderr, p.env)] == [0o600] * 4
    assert p.stdout.read_bytes() == p.stderr.read_bytes() == b""


def test_job_json_is_never_rewritten() -> None:
    job = make_job()
    before = job.paths.job.read_bytes()
    jobs.mark_launched(job)
    jobs.mark_started(job, 42)
    assert job.paths.job.read_bytes() == before
    assert json.loads(job.paths.started.read_text())["pid"] == 42
    assert "at" in json.loads(job.paths.launched.read_text())


def test_load_job_round_trips_and_rejects_bad_ids() -> None:
    job = make_job()
    assert jobs.load_job(job.job_id) == job
    assert not jobs.valid_job_id("../x") and not jobs.valid_job_id("ABC")
    assert not jobs.valid_job_id("0123456789ab\n")
    for bad in ("../x", "ABC", "0" * 12):
        with pytest.raises(InputError, match="no such job"):
            jobs.load_job(bad)


def test_list_jobs_is_oldest_first() -> None:
    a, b = make_job(), make_job()
    assert {j.job_id for j in jobs.list_jobs()} == {a.job_id, b.job_id}
    assert jobs.list_jobs() == sorted(jobs.list_jobs(), key=lambda j: j.created_at)


def test_create_job_resolves_lab_id_once() -> None:
    reg = Registry()
    reg.add(LabEntry("11111111-2222-3333-4444-555555555555", "mylab", os.getuid()))
    assert make_job(lab_ref="mylab").lab_id == "11111111-2222-3333-4444-555555555555"
    assert make_job(lab_ref="nolab").lab_id is None
    assert make_job(command="host prepare", lab_ref=jobs.HOST_LAB_REF).lab_id is None


def test_worker_env_allowlist() -> None:
    env = {
        "PATH": "/bin", "SANDBED_X": "1", "TF_LOG": "debug", "LXD_DIR": "/d",
        "SSL_CERT_FILE": "/c", "AWS_SECRET_ACCESS_KEY": "s", "XDG_RUNTIME_DIR": "/run/u",
        "DBUS_SESSION_BUS_ADDRESS": "unix:x", "TF_BAD": "a\nb", "TF_CR": "a\rb",
        "TF_NUL": "a\0b", "TF_RAW": "\udcff",
    }
    assert jobs.worker_env(env) == {
        "PATH": "/bin", "SANDBED_X": "1", "TF_LOG": "debug", "LXD_DIR": "/d",
        "SSL_CERT_FILE": "/c",
    }


def test_write_env_file_escapes_and_skips_unsafe_entries() -> None:
    job = make_job()
    env = {"A": 'q"b\\s', "B": "x\ny", "C": "\udcff", "bad name": "v", "D": "ok"}
    jobs.write_env_file(job, env)
    assert job.paths.env.read_text() == 'A="q\\"b\\\\s"\nD="ok"\n'
    assert mode(job.paths.env) == 0o600


def test_atomic_write_json_sort_keys(tmp_path) -> None:
    data = {"b": 1, "a": 2}
    atomic_write_json(tmp_path / "s.json", data)
    atomic_write_json(tmp_path / "u.json", data, sort_keys=False)
    assert list(json.loads((tmp_path / "s.json").read_text())) == ["a", "b"]
    assert list(json.loads((tmp_path / "u.json").read_text())) == ["b", "a"]


def test_result_keeps_key_order_and_adds_job_id() -> None:
    job = make_job()
    assert jobs.write_result(job, {"z": 1, "status": "succeeded", "a": 2}, "done", 0) is True
    result = jobs.read_result(job)
    assert list(result["envelope"]) == ["z", "status", "a", "job_id"]
    assert result["envelope"]["job_id"] == job.job_id
    assert (result["human"], result["exit_code"]) == ("done", 0)
    assert mode(job.paths.result) == 0o600


def test_second_write_result_is_a_noop() -> None:
    job = make_job()
    jobs.write_result(job, {"status": "succeeded"}, "first", 0)
    first = job.paths.result.read_bytes()
    assert jobs.write_result(job, {"status": "failed"}, "second", 6) is False
    assert job.paths.result.read_bytes() == first
    assert [p.name for p in job.paths.root.glob(".result.*")] == []


def test_read_exit_missing_and_present() -> None:
    job = make_job()
    assert jobs.read_exit(job) is None
    atomic_write_json(job.paths.exit, {"service_result": "timeout"})
    assert jobs.read_exit(job)["service_result"] == "timeout"


def aged(job: jobs.Job, created_at: str) -> jobs.Job:
    return replace(job, created_at=created_at)


ACTIVE = {"ActiveState": "active", "LoadState": "loaded"}
GONE = {"ActiveState": "inactive", "LoadState": "not-found"}


@pytest.mark.parametrize("status", ["succeeded", "failed", "interrupted"])
def test_derive_state_from_result(status) -> None:
    job = make_job()
    jobs.write_result(job, {"status": status}, "h", 0)
    assert jobs.derive_state(job, ACTIVE) == status


def test_derive_state_without_result() -> None:
    job = make_job()
    assert jobs.derive_state(job, GONE) == "starting"
    jobs.mark_launched(job)
    assert jobs.derive_state(job, GONE) == "lost"
    assert jobs.derive_state(job, ACTIVE) == "running"
    for active in ("activating", "deactivating", "reloading"):
        assert jobs.derive_state(job, {"ActiveState": active, "LoadState": "loaded"}) == "running"
    assert jobs.derive_state(job, {"ActiveState": "failed", "LoadState": "loaded"}) == "lost"
    assert jobs.derive_state(job, None) == "unknown"
    old = aged(make_job(), OLD)
    assert jobs.derive_state(old, GONE) == "lost"
    assert jobs.derive_state(old, {"ActiveState": "active", "LoadState": "not-found"}) == "lost"


def test_read_log_windows(tmp_path) -> None:
    log = tmp_path / "l"
    log.write_bytes(b"0123456789")
    assert jobs.read_log(log, 0, 4) == (b"0123", 4, 10, False)
    assert jobs.read_log(log, 4, 100) == (b"456789", 10, 10, True)
    assert jobs.read_log(log, 99, 4) == (b"", 10, 10, True)
    assert jobs.read_log(tmp_path / "none", 0, 4) == (b"", 0, 0, True)


def test_every_timeout_constant_is_budgeted_by_name_and_value() -> None:
    entries = [e for per_command in jobs.STAGE_DEADLINES.values() for e in per_command]
    for module in TIMEOUT_MODULES:
        source = (DEV / f"{module}.py").read_text()
        for name in re.findall(r"^([A-Z][A-Z0-9_]*_TIMEOUT)\s*=", source, re.M):
            value = getattr(import_module(f"sandbed.dev.{module}"), name)
            seconds = [s for n, s in entries if n == name]
            assert seconds, f"{module}.{name} is missing from STAGE_DEADLINES"
            assert all(s == value for s in seconds), f"{name} entries drifted from {value}"


def test_runtime_caps_cover_the_deadlines() -> None:
    assert set(jobs.RUNTIME_MAX_SEC) == set(jobs.STAGE_DEADLINES)
    for command, entries in jobs.STAGE_DEADLINES.items():
        total = sum(seconds for _, seconds in entries)
        assert jobs.runtime_max_seconds(command) >= 1.25 * total
        assert jobs.RUNTIME_MAX_SEC[command] == math.ceil(1.25 * total) + 120
    prov = import_module("sandbed.dev.provision")
    floor = 4 * prov.PROVISION_TIMEOUT + prov.READY_TIMEOUT
    assert sum(s for _, s in jobs.STAGE_DEADLINES["create"]) >= floor
    assert jobs.RUNTIME_MAX_SEC["create"] >= floor


def test_unknown_command_has_no_runtime_cap() -> None:
    with pytest.raises(InputError):
        jobs.runtime_max_seconds("status")


def test_job_dir_is_the_job_directory() -> None:
    job = make_job()
    assert job.dir == jobs.JobPaths(job.job_id).root
    assert job.dir.is_dir()


def test_real_job_feeds_systemd_unit_argv() -> None:
    from sandbed.dev import systemd

    job = make_job()
    argv = systemd.unit_argv(
        job, wait=True, python="/usr/bin/python3", runtime_max=jobs.runtime_max_seconds("build")
    )
    assert "--wait" in argv
    assert f"--unit=sandbed-dev-{job.job_id}" in argv
    assert argv[argv.index(f"EnvironmentFile={job.dir}/env") - 1] == "-p"
    tail = argv[argv.index("--") + 1:]
    assert tail == ["/usr/bin/python3", "-m", "sandbed.dev.worker", "--job-id", job.job_id]


def _corrupt(job: jobs.Job, content: str) -> None:
    job.paths.job.write_text(content)


@pytest.mark.parametrize("content", ['{"job_id": "abc', "[1, 2]", '{"job_id": "x"}', "null"])
def test_malformed_job_json_is_an_input_error_and_skipped_by_list(content) -> None:
    good, bad = make_job(), make_job()
    _corrupt(bad, content)
    with pytest.raises(InputError, match="malformed job.json"):
        jobs.load_job(bad.job_id)
    assert [j.job_id for j in jobs.list_jobs()] == [good.job_id]


def test_missing_job_json_is_no_such_job() -> None:
    job = make_job()
    job.paths.job.unlink()
    with pytest.raises(InputError, match="no such job"):
        jobs.load_job(job.job_id)
