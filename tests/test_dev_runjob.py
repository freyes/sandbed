from __future__ import annotations

import json
import os
import signal
import stat
import subprocess

import pytest

from sandbed.dev import host, jobs, runjob, systemd
from sandbed.dev.errors import HostError
from sandbed.dev.host import Check
from sandbed.dev.results import Outcome, envelope

COMMANDS = ["host prepare", "create", "build", "deploy", "test", "collect", "destroy"]
INACTIVE = {"ActiveState": "inactive", "LoadState": "not-found"}
ACTIVE = {"ActiveState": "active", "LoadState": "loaded"}


def ok(name: str) -> Check:
    return Check(name, True, "fine")


def bad(name: str) -> Check:
    return Check(name, False, "broken", f"fix {name}")


class FakeProc:
    def __init__(self, stderr="", code=0, hook=None, interrupt=False):
        self.stderr, self.returncode, self.hook, self.interrupt = stderr, code, hook, interrupt
        self.calls = 0

    def communicate(self, timeout=None):
        self.calls += 1
        if self.interrupt and self.calls == 1:
            raise KeyboardInterrupt
        if self.hook:
            self.hook()
        return None, self.stderr


class Env:
    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.argvs: list[list[str]] = []
        self.popen_kwargs: list[dict] = []
        self.runs: list[list[str]] = []
        self.proc = FakeProc()
        self.props = INACTIVE
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setattr(runjob.host, "check_systemd_user", lambda: ok("systemd-user"))
        monkeypatch.setattr(runjob.host, "check_sudo", lambda: ok("sudo"))
        monkeypatch.setattr(runjob, "TAIL_POLL_SEC", 0.01)
        monkeypatch.setattr(systemd, "_popen", self.popen)
        monkeypatch.setattr(systemd, "_run", self.run)

    def popen(self, argv, **kw):
        self.argvs.append(argv)
        self.popen_kwargs.append(kw)
        return self.proc

    def run(self, argv, *, timeout, env=None):
        self.runs.append(argv)
        if argv[:3] == ["systemctl", "--user", "show"]:
            return 0, "".join(f"{k}={v}\n" for k, v in self.props.items()), ""
        if argv[0] == "loginctl":
            return 0, "Linger=yes\n", ""
        return 0, "", ""

    def job(self) -> jobs.Job:
        (j,) = jobs.list_jobs()
        return j

    def write(self, j, status="succeeded", code=0, started=True):
        if started:
            jobs.mark_started(j, 1)
        jobs.write_result(j, envelope("build", status, tag=status), f"human {status}", code)


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


def launch(command="build", detach=False):
    return runjob.launch(command, {"k": 1}, "lab", detach=detach)


def test_detach_returns_launched_fields_and_marks_launched(env, capsys) -> None:
    got = launch(detach=True)
    job = env.job()
    assert isinstance(got, runjob.Launched)
    assert got.fields == {
        "status": "running", "job_id": job.job_id, "unit": job.unit,
        "job_path": str(job.dir), "stdout_path": str(job.paths.stdout),
        "stderr_path": str(job.paths.stderr), "linger": True,
        "next": f"sandbed dev job wait --job {job.job_id} --json",
        "human": f"job {job.job_id} started; follow with: "
        f"sandbed dev job wait --job {job.job_id} --json",
    }
    assert job.paths.launched.exists()
    assert "--wait" not in env.argvs[0]
    assert capsys.readouterr().out == ""


def test_detach_launch_failure_publishes_host_result(env) -> None:
    env.proc = FakeProc(stderr="Failed to connect to bus", code=1)
    with pytest.raises(HostError, match="Failed to connect to bus"):
        launch(detach=True)
    job = env.job()
    assert jobs.read_result(job)["envelope"]["failure_category"] == "host"
    assert not job.paths.launched.exists()


def test_blocking_returns_recorded_outcome_and_streams_logs_to_stderr(env, capsys) -> None:
    def hook():
        j = env.job()
        j.paths.stdout.write_text("out-1\nout-2\nout-3\n")
        j.paths.stderr.write_text("err-1\nerr-2\nerr-3\n")
        env.write(j, code=0)

    env.proc = FakeProc(hook=hook)
    got = launch()
    cap = capsys.readouterr()
    assert got == Outcome(
        {**envelope("build", "succeeded", tag="succeeded"), "job_id": env.job().job_id},
        "human succeeded", 0,
    )
    assert "--wait" in env.argvs[0]
    assert cap.out == ""
    for prefix in ("out", "err"):
        idx = [cap.err.index(f"{prefix}-{n}\n") for n in (1, 2, 3)]
        assert idx == sorted(idx)


def test_gate_sudo_blocks_create_and_host_prepare_but_not_deploy(env, monkeypatch) -> None:
    monkeypatch.setattr(runjob.host, "check_sudo", lambda: bad("sudo"))
    for command in ("create", "host prepare"):
        with pytest.raises(HostError, match="sudo check failed: broken") as exc:
            launch(command, detach=True)
        assert exc.value.context["remediation"] == "fix sudo"
    assert jobs.list_jobs() == []
    assert isinstance(launch("deploy", detach=True), runjob.Launched)


@pytest.mark.parametrize("command", COMMANDS)
def test_gate_systemd_user_blocks_all_commands_without_full_host_check(
    env, monkeypatch, command
) -> None:
    def full_check():
        raise AssertionError("check_host must not be called")

    monkeypatch.setattr(runjob.host, "check_systemd_user", lambda: bad("systemd-user"))
    monkeypatch.setattr(host, "check_host", full_check)
    with pytest.raises(HostError, match="systemd-user check failed"):
        launch(command, detach=True)
    assert jobs.list_jobs() == [] and env.argvs == []


def test_gate_check_launchable_failure_precedes_job_creation(env, monkeypatch) -> None:
    monkeypatch.setattr("sandbed.dev.runjob.sys.executable", "/opt/with space/python")
    with pytest.raises(HostError, match="python interpreter path"):
        launch(detach=True)
    assert jobs.list_jobs() == []


def test_launch_failure_without_started_marker_raises_host_error_with_remediation(env) -> None:
    env.proc = FakeProc(stderr="Unit sandbed-dev-x failed to start", code=1)
    with pytest.raises(HostError, match="Unit sandbed-dev-x failed") as exc:
        launch()
    assert "enable a user manager" in exc.value.context["remediation"]
    job = env.job()
    result = jobs.read_result(job)
    assert result["envelope"]["failure_category"] == "host"
    assert result["exit_code"] == 3


def test_no_result_with_started_and_inactive_unit_synthesizes_internal(env) -> None:
    env.proc = FakeProc(hook=lambda: jobs.mark_started(env.job(), 1))
    got = launch()
    job = env.job()
    assert got.code == 6
    assert got.payload["failure_category"] == "internal"
    assert job.unit in got.payload["message"] and str(job.dir) in got.payload["message"]


def test_active_unit_after_client_exit_keeps_polling_for_later_result(env, monkeypatch) -> None:
    env.props = ACTIVE
    env.proc = FakeProc(hook=lambda: jobs.mark_started(env.job(), 1))
    sleeps = []

    def fake_sleep(sec):
        sleeps.append(sec)
        if len(sleeps) == 2:
            env.write(env.job(), code=0, started=False)

    monkeypatch.setattr(runjob, "sleep", fake_sleep)
    got = launch()
    assert len(sleeps) == 2
    assert got.payload["status"] == "succeeded" and got.code == 0


def test_interrupt_stops_unit_once_and_returns_recorded_130(env) -> None:
    def hook():
        env.write(env.job(), status="interrupted", code=130)

    env.proc = FakeProc(hook=hook, interrupt=True)
    got = launch()
    stops = [a for a in env.runs if a[:3] == ["systemctl", "--user", "stop"]]
    assert stops == [["systemctl", "--user", "stop", env.job().unit]]
    assert got.code == 130 and got.payload["status"] == "interrupted"


def test_second_interrupt_during_stop_still_returns_result(env, monkeypatch) -> None:
    env.proc = FakeProc(interrupt=True)
    original, env_ = env.run, env

    def run(argv, *, timeout, env=None):
        if argv[:3] == ["systemctl", "--user", "stop"]:
            jobs.mark_started(env_.job(), 1)
            raise KeyboardInterrupt
        return original(argv, timeout=timeout, env=env)

    monkeypatch.setattr(systemd, "_run", run)
    got = launch()
    assert got.code == 6


def test_finalizer_first_race_returns_existing_result(env) -> None:
    def hook():
        env.write(env.job(), status="failed", code=4, started=False)

    env.proc = FakeProc(stderr="boom", hook=hook)
    got = launch()
    assert got.code == 4 and got.payload["tag"] == "failed"


def test_finalizer_wins_race_against_parent_host_failure(env, monkeypatch) -> None:
    real = jobs.read_result
    calls = {"n": 0}

    def late(job):
        calls["n"] += 1
        if calls["n"] == 1:  # parent sees nothing, then the finalizer publishes before it writes
            return None
        return real(job)

    def hook():
        env.write(env.job(), status="failed", code=3, started=False)

    env.proc = FakeProc(stderr="x", hook=hook)
    monkeypatch.setattr(runjob.jobs, "read_result", late)
    got = launch()
    assert isinstance(got, Outcome) and got.payload["tag"] == "failed"
    assert json.loads(env.job().paths.result.read_text())["exit_code"] == 3


SECRETS = {
    "HTTPS_PROXY": "http://user:TOPSECRET@proxy:3128",
    "TF_VAR_token": "TFSECRET",
    "AWS_SECRET_ACCESS_KEY": "NOTFORWARDED",
}


@pytest.mark.parametrize("detach", [True, False])
def test_secrets_stay_out_of_argv_and_job_json(env, monkeypatch, detach) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    if not detach:
        env.proc = FakeProc(hook=lambda: env.write(env.job()))
    launch(detach=detach)
    job = env.job()
    argv = env.argvs[0]
    for secret in ("TOPSECRET", "TFSECRET", "NOTFORWARDED"):
        assert not any(secret in arg for arg in argv)
        assert secret not in job.paths.job.read_text()
    envfile = job.paths.env
    assert stat.S_IMODE(envfile.stat().st_mode) == 0o600
    lines = envfile.read_text().splitlines()
    assert 'HTTPS_PROXY="http://user:TOPSECRET@proxy:3128"' in lines
    assert 'TF_VAR_token="TFSECRET"' in lines
    assert "AWS_SECRET_ACCESS_KEY" not in envfile.read_text()
    client_env = env.popen_kwargs[0]["env"]
    assert client_env["AWS_SECRET_ACCESS_KEY"] == "NOTFORWARDED"
    assert client_env == systemd.bus_env(client_env, os.getuid())


@pytest.mark.parametrize("detach", [True, False])
def test_unit_gets_environment_via_environment_file_only(env, detach) -> None:
    if not detach:
        env.proc = FakeProc(hook=lambda: env.write(env.job()))
    launch(detach=detach)
    argv = env.argvs[0]
    pairs = list(zip(argv, argv[1:]))
    assert ("-p", f"EnvironmentFile={env.job().dir}/env") in pairs
    assert "-E" not in argv and not any(a.startswith("--setenv") for a in argv)


def test_sigterm_handler_installed_while_waiting_and_restored_after(env) -> None:
    original = signal.getsignal(signal.SIGTERM)
    seen = []

    def hook():
        seen.append(signal.getsignal(signal.SIGTERM))
        env.write(env.job())

    env.proc = FakeProc(hook=hook)
    launch()
    assert seen == [runjob._raise_interrupt]
    assert signal.getsignal(signal.SIGTERM) is original


def test_sigterm_handler_restored_after_interrupt_path(env) -> None:
    original = signal.getsignal(signal.SIGTERM)
    env.proc = FakeProc(
        hook=lambda: env.write(env.job(), status="interrupted", code=130), interrupt=True
    )
    launch()
    assert signal.getsignal(signal.SIGTERM) is original


def test_detach_timeout_kills_client_and_publishes_host_failure(env) -> None:
    class Slow(FakeProc):
        killed = False

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                assert timeout == runjob.DETACH_TIMEOUT_SEC
                raise subprocess.TimeoutExpired("systemd-run", timeout)
            return None, ""

        def kill(self):
            self.killed = True

    env.proc = Slow(code=-9)
    with pytest.raises(HostError, match="did not return in time"):
        launch(detach=True)
    job = env.job()
    assert env.proc.killed
    assert jobs.read_result(job)["envelope"]["failure_category"] == "host"
    assert jobs.derive_state(job, INACTIVE) == "failed"


@pytest.mark.parametrize("target", ["write_env_file", "start"])
def test_exception_before_client_runs_publishes_host_failure_and_reraises(
    env, monkeypatch, target
) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk full")

    owner = jobs if target == "write_env_file" else systemd
    monkeypatch.setattr(owner, target, boom)
    with pytest.raises(OSError, match="disk full"):
        launch(detach=True)
    job = env.job()
    result = jobs.read_result(job)
    assert result["exit_code"] == 3
    assert result["envelope"]["failure_category"] == "host"
    assert jobs.derive_state(job, INACTIVE) == "failed"


def test_stop_failure_after_interrupt_kills_client_and_continues(env, monkeypatch) -> None:
    class Killable(FakeProc):
        killed = False

        def kill(self):
            self.killed = True

    env.proc = Killable(interrupt=True, hook=lambda: jobs.mark_started(env.job(), 1))
    original = env.run

    def run(argv, *, timeout, env=None):
        if argv[:3] == ["systemctl", "--user", "stop"]:
            return 1, "", "Access denied"
        return original(argv, timeout=timeout, env=env)

    monkeypatch.setattr(systemd, "_run", run)
    got = launch()
    assert env.proc.killed and env.proc.calls == 2
    assert got.code == 6
    assert "could not stop the unit" in got.payload["message"]
    assert "Access denied" in got.payload["message"]


class LongRunningUnit:
    """The unit stays active for `polls` polls of 0.5 s (fake clock), then goes inactive."""

    def __init__(self, env, monkeypatch, polls, on_inactive):
        self.env, self.clock, self.polls, self.on_inactive = env, 0.0, polls, on_inactive
        self.results_while_active: list = []
        env.props = ACTIVE
        env.proc = FakeProc(hook=lambda: jobs.mark_started(env.job(), 1))
        monkeypatch.setattr(runjob, "sleep", self.sleep)

    def sleep(self, sec):
        self.clock += sec
        job = self.env.job()
        self.results_while_active.append(jobs.read_result(job))
        if self.clock >= self.polls * runjob.RESULT_POLL_SEC:
            self.env.props = INACTIVE
            self.on_inactive(job)


def test_await_result_keeps_polling_past_ten_minutes_while_unit_active(env, monkeypatch) -> None:
    unit = LongRunningUnit(
        env, monkeypatch, 2400, lambda job: env.write(job, "succeeded", 0, started=False)
    )
    got = launch()
    assert unit.clock > 600
    assert all(r is None for r in unit.results_while_active[:-1])
    assert got.code == 0
    assert got.payload["status"] == "succeeded"


def test_synthesized_failure_is_written_only_after_unit_goes_inactive(env, monkeypatch) -> None:
    unit = LongRunningUnit(env, monkeypatch, 2400, lambda job: None)
    got = launch()
    assert unit.clock > 600
    assert all(r is None for r in unit.results_while_active)
    assert got.code == 6
    assert got.payload["failure_category"] == "internal"
    assert "worker ended without a result" in got.payload["message"]


def test_broken_stderr_does_not_change_the_outcome(env, monkeypatch) -> None:
    class Broken:
        def write(self, text):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    def hook():
        j = env.job()
        j.paths.stderr.write_text("err-1\n")
        env.write(j, code=0)

    env.proc = FakeProc(hook=hook)
    monkeypatch.setattr(runjob.sys, "stderr", Broken())
    got = launch()
    assert got.code == 0 and got.payload["status"] == "succeeded"


def test_gate_failure_wins_over_busy_lab(env, monkeypatch) -> None:
    env.props = ACTIVE
    launch(detach=True)
    monkeypatch.setattr(runjob.host, "check_systemd_user", lambda: bad("systemd-user"))
    with pytest.raises(HostError, match="systemd-user check failed"):
        launch(detach=True)
