from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from sandbed.dev import commands, jobs, runjob
from sandbed.dev.errors import InputError
from sandbed.dev.host import Check
from sandbed.dev.labs import LabEntry, Registry, lab_prefix
from sandbed.dev.paths import lab_paths, state_root
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.results import envelope
from sandbed.dev.state import AttemptLog
from sandbed.dev.store import exclusive_lock

ACTIVE = {"ActiveState": "active", "LoadState": "loaded"}
INACTIVE = {"ActiveState": "inactive", "LoadState": "not-found"}


class Shown:
    """A `show` fake that records which units were queried."""

    def __init__(self, props):
        self.props, self.calls = props, []

    def __call__(self, unit):
        self.calls.append(unit)
        return self.props


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(runjob.host, "check_systemd_user", lambda: Check("systemd-user", True, ""))
    monkeypatch.setattr(runjob.host, "check_sudo", lambda: Check("sudo", True, ""))
    return tmp_path


def make_lab(label="a"):
    reg = Registry(state_root())
    lab = str(uuid.uuid4())
    uid = os.getuid()
    reg.add(LabEntry(lab, label, uid, ["10.213.0.0/24"]))
    paths = lab_paths(lab, state_root())
    LabStore(paths).save(
        LabRecord(lab, label, uid, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                  config_hash(DEFAULT_CONFIG))
    )
    return lab, paths


def live_job(ref="a", command="build", launched=True):
    job = jobs.create_job(command, {}, ref, "/", True)
    if launched:
        jobs.mark_launched(job)
    return job


def finish(job):
    jobs.write_result(job, envelope(job.command, "succeeded"), "", 0)


def job_dirs():
    return sorted(p.name for p in jobs.jobs_root().iterdir())


def patch_show(monkeypatch, props):
    monkeypatch.setattr("sandbed.dev.runjob.systemd.show", lambda unit, env=None: props)


def test_preflight_rejects_a_second_command_and_creates_no_job(state, monkeypatch) -> None:
    job = live_job("a")
    patch_show(monkeypatch, ACTIVE)
    with pytest.raises(InputError) as exc:
        runjob.launch("deploy", {}, "a", detach=True)
    assert job.job_id in str(exc.value)
    assert f"sandbed dev job wait --job {job.job_id} --json" in str(exc.value)
    assert job_dirs() == [job.job_id]


def test_preflight_ignores_a_finished_job_without_asking_systemd(state) -> None:
    job = live_job("a")
    finish(job)
    show = Shown(ACTIVE)
    assert jobs.find_active("a", show) is None
    assert show.calls == []


def test_preflight_ignores_jobs_of_another_lab(state) -> None:
    live_job("b")
    assert jobs.find_active("a", Shown(ACTIVE)) is None


def test_preflight_host_prepare_blocks_only_host_prepare(state) -> None:
    host_job = live_job(jobs.HOST_LAB_REF, "host prepare")
    lab_job = live_job("a")
    assert jobs.find_active(jobs.HOST_LAB_REF, Shown(ACTIVE)).job_id == host_job.job_id
    assert jobs.find_active("a", Shown(ACTIVE)).job_id == lab_job.job_id
    finish(host_job)
    assert jobs.find_active(jobs.HOST_LAB_REF, Shown(ACTIVE)) is None
    finish(lab_job)
    live_job("a")
    assert jobs.find_active(jobs.HOST_LAB_REF, Shown(ACTIVE)) is None


def test_preflight_matches_uuid_and_label_both_ways(state) -> None:
    lab, _ = make_lab("a")
    by_label = live_job("a")
    assert jobs.find_active(lab, Shown(ACTIVE)).job_id == by_label.job_id
    finish(by_label)
    by_uuid = live_job(lab)
    assert jobs.find_active("a", Shown(ACTIVE)).job_id == by_uuid.job_id


def test_preflight_newest_active_job_wins(state) -> None:
    older = live_job("a")
    newer = live_job("a")
    assert jobs.find_active("a", Shown(ACTIVE)).job_id == max(
        (older, newer), key=lambda j: (j.created_at, j.job_id)).job_id


def test_preflight_unreachable_manager_trusts_launched_jobs_only(state) -> None:
    launched = live_job("a")
    assert jobs.find_active("a", Shown(None)).job_id == launched.job_id
    finish(launched)
    live_job("b", launched=False)
    assert jobs.find_active("b", Shown(None)) is None


def test_preflight_inactive_unit_without_result_is_not_active(state) -> None:
    live_job("a")
    assert jobs.find_active("a", Shown(INACTIVE)) is None


def test_status_busy_reads_without_the_lock_and_names_the_job(state, monkeypatch) -> None:
    _, paths = make_lab("a")
    log = AttemptLog(paths.attempts)
    log.begin("run1", "deploy", "fp")
    job = live_job("a")
    monkeypatch.setattr("sandbed.dev.commands.systemd.show", lambda unit, env=None: ACTIVE)
    before = (paths.attempts.read_bytes(), paths.attempts.stat().st_mtime_ns)
    with exclusive_lock(paths.lock):
        got = commands.status("a")
    assert got["busy"] is True
    assert got["active_job"] == job.job_id
    assert got["blocked_reason"] == "deploy is still running"
    assert got["human"] == "a: new (busy)"
    assert (paths.attempts.read_bytes(), paths.attempts.stat().st_mtime_ns) == before


def snapshot(root):
    snap = {}
    for p in sorted(root.rglob("*")):
        st = p.stat()
        digest = None if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
        snap[str(p.relative_to(root))] = (digest, st.st_mtime_ns, st.st_mode)
    return snap


def test_status_busy_leaves_every_state_file_untouched(state, monkeypatch) -> None:
    _, paths = make_lab("a")
    AttemptLog(paths.attempts).begin("run1", "deploy", "fp")
    live_job("a")
    monkeypatch.setattr("sandbed.dev.commands.systemd.show", lambda unit, env=None: ACTIVE)
    with exclusive_lock(paths.lock):
        before = snapshot(state_root())
        assert any(k.endswith("lab.json") for k in before)
        assert "registry.json" in before, sorted(before)
        got = commands.status("a")
        after = snapshot(state_root())
    assert got["busy"] is True
    assert after == before


def test_status_busy_never_writes(state, monkeypatch) -> None:
    _, paths = make_lab("a")
    monkeypatch.setattr("sandbed.dev.commands.systemd.show", lambda unit, env=None: None)

    def boom(*args, **kwargs):
        raise AssertionError("busy status must not write")

    monkeypatch.setattr(LabStore, "save", boom)
    monkeypatch.setattr("sandbed.dev.store.atomic_write_json", boom)
    monkeypatch.setattr("sandbed.dev.record.atomic_write_json", boom)
    monkeypatch.setattr("sandbed.dev.commands.atomic_write_json", boom)
    with exclusive_lock(paths.lock):
        got = commands.status("a")
    assert got["busy"] is True


def test_status_busy_no_mutation_never_reconciles(state, monkeypatch) -> None:
    _, paths = make_lab("a")
    monkeypatch.setattr("sandbed.dev.commands.systemd.show", lambda unit, env=None: None)

    def boom(self, probe):
        raise AssertionError("reconcile called")

    monkeypatch.setattr(AttemptLog, "reconcile", boom)
    with exclusive_lock(paths.lock):
        got = commands.status("a")
    assert got["busy"] is True and got["active_job"] is None
    assert got["blocked_reason"] is None and got["attempts"] == []


def test_status_free_lock_payload_has_no_busy_key(state) -> None:
    make_lab("a")
    got = commands.status("a")
    assert "busy" not in got and "active_job" not in got
    assert list(got) == [
        "lab_id", "label", "run_id", "provisioning_state", "vm_address",
        "installed_artifact_id", "blocked_reason", "attempts", "human",
    ]
