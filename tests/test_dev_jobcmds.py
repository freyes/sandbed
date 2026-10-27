from __future__ import annotations

import json
import uuid

import pytest

from sandbed.dev import jobcmds, jobs, systemd
from sandbed.dev.errors import HostError, InputError
from sandbed.dev.labs import LabEntry, Registry
from sandbed.dev.paths import lab_paths
from sandbed.dev.state import Attempt, AttemptLog
from sandbed.dev.store import atomic_write_json, exclusive_lock

UID = __import__("os").getuid()
ACTIVE = {"ActiveState": "active", "SubState": "running", "Result": "success",
          "ExecMainStatus": "0", "LoadState": "loaded"}
GONE = {"LoadState": "not-found", "ActiveState": "inactive"}


class Fake:
    """Patches systemd.show/stop and the module clock."""

    def __init__(self, monkeypatch, props=ACTIVE):
        self.props = props
        self.stops = []
        self.clock = 0.0
        monkeypatch.setattr(systemd, "show", lambda unit, env=None: self.props)
        monkeypatch.setattr(systemd, "stop", lambda unit, t, env=None: self.stops.append(unit))
        monkeypatch.setattr(jobcmds, "monotonic", lambda: self.clock)
        monkeypatch.setattr(jobcmds, "sleep", self.sleep)
        self.on_sleep = None

    def sleep(self, seconds):
        self.clock += seconds
        if self.on_sleep:
            self.on_sleep()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return Fake(monkeypatch)


def add_lab(label="a"):
    lab = str(uuid.uuid4())
    Registry().add(LabEntry(lab, label, UID, ["10.213.0.0/24"]))
    return lab


def make_job(command="deploy", lab_ref="a", created=None, launched=True):
    job = jobs.create_job(command, {}, lab_ref, "/tmp", True)
    if launched:
        jobs.mark_launched(job)
    return job


def finish(job, status="succeeded", code=0, lab_id=None):
    jobs.write_result(job, {"status": status, "lab_id": lab_id, "label": "a", "run_id": "r1"},
                      "done", code)


def add_attempt(lab, state="running", stage="deploy"):
    log = AttemptLog(lab_paths(lab).attempts)
    log._save([*log.attempts(), Attempt("r1", stage, 1, state, "fp", "2026-01-01T00:00:00+00:00")])


def test_running_progress_is_read_while_the_lab_lock_is_held(env):
    lab = add_lab()
    job = make_job()
    assert job.lab_id == lab
    add_attempt(lab)
    with exclusive_lock(lab_paths(lab).lock):
        out = jobcmds.job_status(job.job_id)
    assert out["job"]["state"] == "running"
    assert out["job"]["progress"]["current_stage"] == "deploy"
    assert out["job"]["progress"]["attempts_tail"][0]["stage"] == "deploy"
    assert out["label"] == "a" and out["lab_id"] == lab


def test_current_stage_is_none_for_a_finished_job(env):
    lab = add_lab()
    job = make_job()
    add_attempt(lab)
    finish(job)
    env.props = GONE
    progress = jobcmds.job_status(job.job_id)["job"]["progress"]
    assert progress["current_stage"] is None and progress["attempts_tail"] == []


def test_stderr_tail_and_systemd_fields(env):
    job = make_job()
    job.paths.stderr.write_bytes(b"x" * 5000 + b"end\xff")
    out = jobcmds.job_status(job.job_id)["job"]
    assert out["systemd"] == {"ActiveState": "active", "SubState": "running",
                              "Result": "success", "ExecMainStatus": "0"}
    tail = out["progress"]["stderr"]
    assert tail["size"] == 5004 and len(tail["tail"]) == 4096 and tail["tail"].endswith("end\ufffd")


def test_no_result_and_no_manager_raises_host_error(env):
    job = make_job()
    env.props = None
    with pytest.raises(HostError):
        jobcmds.job_status(job.job_id)


def test_finished_job_status_works_without_systemd(env):
    job = make_job()
    finish(job, "failed", 4)
    env.props = None
    out = jobcmds.job_status(job.job_id)["job"]
    assert out["systemd"] is None and out["state"] == "failed" and out["exit_code"] == 4
    assert out["result"]["status"] == "failed" and out["finished_at"] is not None


def test_exit_code_is_none_without_result_and_service_result_from_exit_json(env):
    job = make_job()
    atomic_write_json(job.paths.exit, {"service_result": "timeout", "exit_kind": "killed",
                                       "exit_status": "TERM", "finished_at": "x"})
    out = jobcmds.job_status(job.job_id)["job"]
    assert out["exit_code"] is None and out["service_result"] == "timeout"
    assert out["result"] is None


def test_service_result_is_none_without_exit_json(env):
    job = make_job()
    assert jobcmds.job_status(job.job_id)["job"]["service_result"] is None


def test_started_at_comes_from_started_json(env):
    job = make_job()
    jobs.mark_started(job, 42)
    started = json.loads(job.paths.started.read_text())["at"]
    assert jobcmds.job_status(job.job_id)["job"]["started_at"] == started


def test_reused_label_still_reports_the_original_lab(env):
    old = add_lab("a")
    job = make_job()
    finish(job, lab_id=old)
    Registry().set_state(old, "destroyed")
    new = add_lab("a")
    out = jobcmds.job_status(job.job_id)
    assert out["lab_id"] == old != new


def test_unknown_and_malformed_ids_raise_input_error(env):
    for bad in ("../x", "ABC", "0" * 12):
        with pytest.raises(InputError):
            jobcmds.job_status(bad)
    with pytest.raises(InputError):
        jobcmds.job_logs("../x")


def test_logs_paging_is_contiguous_and_reaches_eof(env):
    job = make_job()
    job.paths.stderr.write_bytes(b"abcdefghij")
    first = jobcmds.job_logs(job.job_id, "stderr", 0, 4)
    second = jobcmds.job_logs(job.job_id, "stderr", first["next_offset"], 4)
    third = jobcmds.job_logs(job.job_id, "stderr", second["next_offset"], 4)
    assert [first["data"], second["data"], third["data"]] == ["abcd", "efgh", "ij"]
    assert (first["eof"], second["eof"], third["eof"]) == (False, False, True)
    assert third["size"] == 10 and third["finished"] is False


def test_logs_stdout_finished_and_bad_stream(env):
    job = make_job()
    job.paths.stdout.write_bytes(b"out")
    finish(job)
    out = jobcmds.job_logs(job.job_id, "stdout")
    assert out["data"] == "out" and out["finished"] is True
    with pytest.raises(InputError):
        jobcmds.job_logs(job.job_id, "nope")


def test_logs_max_bytes_is_capped(env):
    job = make_job()
    job.paths.stderr.write_bytes(b"a" * (jobcmds.MAX_LOG_BYTES + 10))
    out = jobcmds.job_logs(job.job_id, "stderr", 0, 10**9)
    assert len(out["data"]) == jobcmds.MAX_LOG_BYTES and out["eof"] is False


def test_wait_times_out_while_running(env):
    job = make_job()
    out = jobcmds.job_wait(job.job_id, 5)
    assert out["timed_out"] is True and out["job"]["state"] == "running"
    assert env.clock >= 5


def test_wait_returns_terminal_state_when_result_appears(env):
    job = make_job()
    env.on_sleep = lambda: finish(job) if not job.paths.result.exists() else None
    out = jobcmds.job_wait(job.job_id, 60)
    assert out["timed_out"] is False and out["job"]["state"] == "succeeded"


def test_wait_timeout_is_capped(env):
    job = make_job()
    jobcmds.job_wait(job.job_id, 10**6)
    assert jobcmds.MAX_WAIT_SEC <= env.clock < jobcmds.MAX_WAIT_SEC + 2


def test_cancel_on_finished_job_does_not_stop(env):
    job = make_job()
    finish(job)
    out = jobcmds.job_cancel(job.job_id)
    assert out["cancelled"] is False and out["reason"] == "already finished"
    assert env.stops == []


def test_cancel_running_job_stops_once_and_waits_for_terminal(env):
    job = make_job()
    env.on_sleep = lambda: finish(job, "interrupted", 130)
    out = jobcmds.job_cancel(job.job_id)
    assert env.stops == [job.unit]
    assert out["cancelled"] is True and out["job"]["state"] == "interrupted"
    assert out["job"]["exit_code"] == 130


def test_cancel_gives_up_after_the_wait_budget(env):
    job = make_job()
    out = jobcmds.job_cancel(job.job_id)
    assert out["cancelled"] is True and out["job"]["state"] == "running"
    assert jobcmds.CANCEL_WAIT_SEC <= env.clock < jobcmds.CANCEL_WAIT_SEC + 2


def test_list_active_excludes_finished_jobs(env):
    done = make_job()
    finish(done)
    live = make_job()
    out = jobcmds.job_list(active=True)["jobs"]
    assert [j["job_id"] for j in out] == [live.job_id]
    assert set(out[0]) == {"job_id", "command", "lab_ref", "state", "created_at", "unit"}
    everything = [j["job_id"] for j in jobcmds.job_list()["jobs"]]
    assert set(everything) == {done.job_id, live.job_id}


def test_list_limit_and_newest_first(env):
    first = make_job()
    second = make_job()
    atomic_write_json(first.paths.job, {**json.loads(first.paths.job.read_text()),
                                        "created_at": "2000-01-01T00:00:00+00:00"})
    out = jobcmds.job_list(limit=1)["jobs"]
    assert [j["job_id"] for j in out] == [second.job_id]


def test_list_lab_filter_matches_ref_label_and_uuid(env):
    lab = add_lab("a")
    add_lab("b")
    mine = make_job(lab_ref="a")
    make_job(lab_ref="b")
    host = make_job("host prepare", lab_ref="@host")
    assert [j["job_id"] for j in jobcmds.job_list(lab="a")["jobs"]] == [mine.job_id]
    assert [j["job_id"] for j in jobcmds.job_list(lab=lab)["jobs"]] == [mine.job_id]
    assert [j["job_id"] for j in jobcmds.job_list(lab="@host")["jobs"]] == [host.job_id]


def test_list_without_manager_and_no_result_raises_host_error(env):
    make_job()
    env.props = None
    with pytest.raises(HostError):
        jobcmds.job_list()


def test_create_job_without_lab_id_reports_the_lab_from_the_result_envelope(env):
    job = make_job("create", lab_ref="a")  # the lab does not exist yet
    assert job.lab_id is None
    lab = add_lab("a")
    finish(job, lab_id=lab)
    env.props = GONE
    out = jobcmds.job_status(job.job_id)
    assert out["job"]["lab_id"] == lab and out["lab_id"] == lab and out["label"] == "a"
    assert out["job"]["progress"]["current_stage"] is None


def test_running_create_job_without_lab_id_follows_the_lab_found_by_ref(env):
    job = make_job("create", lab_ref="a")
    assert job.lab_id is None
    lab = add_lab("a")
    add_attempt(lab, stage="provision")
    out = jobcmds.job_status(job.job_id)
    assert out["job"]["state"] == "running"
    assert out["job"]["progress"]["current_stage"] == "provision"
    assert out["lab_id"] == lab and out["label"] == "a"


def test_running_job_with_unresolvable_lab_ref_has_no_stage(env):
    job = make_job("create", lab_ref="ghost")
    assert job.lab_id is None
    out = jobcmds.job_status(job.job_id)["job"]
    assert out["state"] == "running" and out["lab_id"] is None
    assert out["progress"]["current_stage"] is None
    assert out["progress"]["attempts_tail"] == []


def test_list_lab_filter_matches_through_the_envelope_lab_id(env):
    job = make_job("create", lab_ref="renamed-ref")
    assert job.lab_id is None
    lab = add_lab("a")
    finish(job, lab_id=lab)
    other = make_job(lab_ref="elsewhere")
    assert [j["job_id"] for j in jobcmds.job_list(lab="a")["jobs"]] == [job.job_id]
    assert other.job_id not in [j["job_id"] for j in jobcmds.job_list(lab=lab)["jobs"]]


def test_status_state_and_result_come_from_one_read(env, monkeypatch):
    job = make_job()
    jobs.mark_started(job, 1)
    finish(job, "succeeded", 0)
    real = jobs.read_result
    calls = []

    def racing(j):
        calls.append(1)
        return None if len(calls) == 1 else real(j)

    monkeypatch.setattr(jobs, "read_result", racing)
    body = jobcmds.job_status(job.job_id)["job"]
    assert (body["state"] == "running") == (body["result"] is None)
    assert (body["exit_code"] is None) == (body["result"] is None)
