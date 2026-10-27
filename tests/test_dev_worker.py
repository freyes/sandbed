import json
import os
import signal
from unittest.mock import MagicMock, call

import pytest

from sandbed.dev import cli, commands, jobs, worker
from sandbed.dev.capabilities import command_paths
from sandbed.dev.errors import HostError


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def make_job(command="destroy", lab="lab1"):
    return jobs.create_job(command, {"lab": lab}, lab, "/tmp", True)


def result_of(job):
    return json.loads(job.paths.result.read_text())


def test_success_envelope_matches_build_outcome_and_started_precedes_body(monkeypatch):
    job = make_job()
    seen = {}

    def fake(params):
        seen["started"] = json.loads(job.paths.started.read_text())
        return {"x": 1, "human": "ok"}

    monkeypatch.setitem(worker.JOB_COMMANDS, "destroy", fake)
    code = worker.main(["--job-id", job.job_id])
    expected = cli.build_outcome("destroy", lambda: {"x": 1, "human": "ok"})
    res = result_of(job)
    assert code == 0
    assert seen["started"]["pid"] == os.getpid()
    assert list(res["envelope"]) == [*expected.payload, "job_id"]
    assert res["envelope"] == {**expected.payload, "job_id": job.job_id}
    assert res["human"] == "ok" and res["exit_code"] == 0


def test_host_error_is_failed_host_exit_3(monkeypatch):
    job = make_job()

    def fake(params):
        raise HostError("nope")

    monkeypatch.setitem(worker.JOB_COMMANDS, "destroy", fake)
    assert worker.main(["--job-id", job.job_id]) == 3
    env = result_of(job)["envelope"]
    assert (env["status"], env["failure_category"]) == ("failed", "host")


def test_sigterm_is_interrupt_and_handler_restored(monkeypatch):
    job = make_job()
    sentinel = lambda *_: None  # noqa: E731
    old = signal.signal(signal.SIGTERM, sentinel)
    try:
        def fake(params):
            os.kill(os.getpid(), signal.SIGTERM)
            return {}

        monkeypatch.setitem(worker.JOB_COMMANDS, "destroy", fake)
        code = worker.main(["--job-id", job.job_id])
        assert code == 130
        assert result_of(job)["envelope"]["status"] == "interrupted"
        assert signal.getsignal(signal.SIGTERM) is sentinel
    finally:
        signal.signal(signal.SIGTERM, old)


def test_sigterm_during_write_result_is_deferred(monkeypatch):
    job = make_job()
    real = jobs.write_result

    def wrapper(*args):
        os.kill(os.getpid(), signal.SIGTERM)
        return real(*args)

    monkeypatch.setattr(jobs, "write_result", wrapper)
    monkeypatch.setitem(worker.JOB_COMMANDS, "destroy", lambda p: {})
    assert worker.main(["--job-id", job.job_id]) == 0
    assert result_of(job)["envelope"]["status"] == "succeeded"


def test_sigterm_after_body_returned_before_write_result_keeps_the_result(monkeypatch):
    job = make_job()
    real = cli.build_outcome

    def build_then_term(command, body):
        outcome = real(command, body)
        os.kill(os.getpid(), signal.SIGTERM)  # body already returned
        return outcome

    monkeypatch.setattr(cli, "build_outcome", build_then_term)
    monkeypatch.setitem(worker.JOB_COMMANDS, "destroy", lambda p: {})
    before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    assert worker.main(["--job-id", job.job_id]) == 0
    res = result_of(job)
    assert res["envelope"]["status"] == "succeeded" and res["exit_code"] == 0
    assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before


@pytest.mark.parametrize("svc,text", [
    ("timeout", "RuntimeMaxSec"), ("oom-kill", "OOM-killed"), ("signal", "killed by a signal"),
    ("core-dump", "killed by a signal"), ("exit-code", "exited without a result"),
])
def test_finalize_synthesizes_failure(monkeypatch, svc, text):
    job = make_job()
    monkeypatch.setenv("SERVICE_RESULT", svc)
    monkeypatch.setenv("EXIT_CODE", "killed")
    monkeypatch.setenv("EXIT_STATUS", "9")
    assert worker.main(["--finalize", "--job-id", job.job_id]) == 0
    res = result_of(job)
    env = res["envelope"]
    assert res["exit_code"] == 6 and text in env["message"]
    assert (env["status"], env["failure_category"], env["failed_stage"], env["evidence"]) == (
        "failed", "internal", None, [])
    assert (env["command"], env["label"], env["job_id"]) == ("destroy", "lab1", job.job_id)
    ex = json.loads(job.paths.exit.read_text())
    assert ex["service_result"] == svc and ex["exit_kind"] == "killed"
    assert ex["exit_status"] == "9" and "finished_at" in ex


def test_finalize_host_job_has_no_label(monkeypatch):
    job = jobs.create_job("host prepare", {}, jobs.HOST_LAB_REF, "/tmp", True)
    monkeypatch.setenv("SERVICE_RESULT", "timeout")
    worker.main(["--finalize", "--job-id", job.job_id])
    assert result_of(job)["envelope"]["label"] is None


def test_finalize_never_overwrites_existing_result(monkeypatch):
    job = make_job()
    jobs.write_result(job, {"status": "succeeded"}, "h", 0)
    before = job.paths.result.read_bytes()
    monkeypatch.setenv("SERVICE_RESULT", "timeout")
    assert worker.main(["--finalize", "--job-id", job.job_id]) == 0
    assert job.paths.result.read_bytes() == before
    assert json.loads(job.paths.exit.read_text())["service_result"] == "timeout"


def test_finalize_synthesizes_result_even_if_exit_json_write_fails(monkeypatch, capsys):
    job = make_job()
    monkeypatch.setenv("SERVICE_RESULT", "timeout")

    def boom(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(worker, "atomic_write_json", boom)
    assert worker.main(["--finalize", "--job-id", job.job_id]) == 0
    assert result_of(job)["exit_code"] == 6
    assert not job.paths.exit.exists()
    assert "exit.json" in capsys.readouterr().err


def test_job_commands_keys():
    assert set(worker.JOB_COMMANDS) == {
        "host prepare", "create", "build", "deploy", "test", "collect", "destroy"}


def test_importing_worker_adds_no_cli_command():
    assert "worker" not in " ".join(command_paths(cli.dev))


def test_unknown_job_id_returns_2_without_traceback(capsys):
    assert worker.main(["--job-id", "0123456789ab"]) == 2
    err = capsys.readouterr().err
    assert "no such job" in err and "Traceback" not in err


# Authoritative job.json `params` shapes. todo 7's CLI must produce exactly these keys.
PARAM_SHAPES = {
    "host prepare": {"osd_size": "100G", "storage_backend": "dir", "storage_source": "/srv/x"},
    "create": {"lab": "a", "provider": "manual", "profile": "single-node",
               "overrides": {"vcpus": 4}},
    "build": {"lab": "a", "source": "/abs/src", "include": ["x.py"]},
    "deploy": {"lab": "a", "artifact": "art-1"},
    "test": {"lab": "a", "scenario": "bootstrap-and-launch"},
    "collect": {"lab": "a", "run_id": "r1"},
    "destroy": {"lab": "a"},
}

DISPATCH = {
    "host prepare": ("host_prepare", call("100G", "dir", "/srv/x")),
    "create": ("create", call("a", provider="manual", profile="single-node",
                              overrides={"vcpus": 4})),
    "build": ("build", call("a", "/abs/src", ("x.py",))),
    "deploy": ("deploy", call("a", "art-1")),
    "test": ("run_test", call("a", "bootstrap-and-launch")),
    "collect": ("collect", call("a", "r1")),
    "destroy": ("destroy", call("a")),
}


@pytest.mark.parametrize("name", sorted(DISPATCH))
def test_job_commands_dispatch_to_commands_with_the_params(monkeypatch, name):
    func, expected = DISPATCH[name]
    rec = MagicMock(return_value={"marker": name})
    monkeypatch.setattr(commands, func, rec)
    out = worker.JOB_COMMANDS[name](PARAM_SHAPES[name])
    assert rec.call_args_list == [expected]
    assert out == {"marker": name}


def test_param_shapes_cover_exactly_the_keys_the_dispatch_reads(monkeypatch):
    class Recording(dict):
        def __init__(self, *a):
            super().__init__(*a)
            self.read = set()

        def __getitem__(self, key):
            self.read.add(key)
            return super().__getitem__(key)

    for func, _ in DISPATCH.values():
        monkeypatch.setattr(commands, func, MagicMock(return_value={}))
    assert set(PARAM_SHAPES) == set(worker.JOB_COMMANDS)
    for name, shape in PARAM_SHAPES.items():
        params = Recording(shape)
        worker.JOB_COMMANDS[name](params)
        assert params.read == set(shape), name
