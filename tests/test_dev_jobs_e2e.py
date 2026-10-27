"""End-to-end job lifecycle: the real CLI, worker, finalizer and files; only systemd is faked.

`tests/fixtures/bin` holds PATH shims for systemd-run, systemctl, loginctl, sudo and setsid.
`tests/fixtures/worker_wrapper.py` replaces the job commands with fakes chosen by
`SANDBED_E2E_FAKE`. Every wait is bounded; leftovers are killed when a test ends.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sandbed.dev import cli as dev_cli
from sandbed.dev.errors import FailureCategory
from sandbed.dev.labs import LabEntry, Registry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.store import LockBusy, exclusive_lock

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
WRAPPER = FIXTURES / "worker_wrapper.py"
CLI_CODE = "from sandbed.cli import cli; cli()"
SIGINT_CLI_CODE = (
    "import signal; signal.signal(signal.SIGINT, signal.default_int_handler); " + CLI_CODE
)
CMD_TIMEOUT = 60


def wait_for(predicate: Callable[[], object], what: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for {what}")
        time.sleep(0.05)


def _kill(pid: int | None, group: bool) -> None:
    if not pid:
        return
    try:
        (os.killpg if group else os.kill)(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


@dataclass
class E2E:
    root: Path
    procs: list[subprocess.Popen] = field(default_factory=list)

    @property
    def state_home(self) -> Path:
        return self.root / "state"

    @property
    def dev_root(self) -> Path:
        return self.state_home / "sandbed" / "dev"

    @property
    def shim(self) -> Path:
        return self.root / "shim"

    @property
    def source(self) -> Path:
        return self.root / "src"

    @property
    def lab_paths(self):
        return lab_paths(self.lab_uuid, self.dev_root)

    def env(self, fake: str = "ok", **extra: str) -> dict[str, str]:
        path = os.pathsep.join(
            [str(FIXTURES / "bin"), str(Path(sys.executable).parent), "/usr/bin", "/bin"]
        )
        env = {
            "PATH": path, "XDG_STATE_HOME": str(self.state_home), "HOME": str(self.root),
            "SHIM_STATE": str(self.shim), "SHIM_WORKER_WRAPPER": str(WRAPPER),
            "SANDBED_E2E_FAKE": fake, "SANDBED_E2E_STATE": str(self.shim),
        }
        env.update(extra)
        return env

    def argv(self, args: list[str], code: str = CLI_CODE) -> list[str]:
        return [sys.executable, "-c", code, "dev", *args]

    def cli(self, args: list[str], fake: str = "ok", **extra: str) -> subprocess.CompletedProcess:
        env = self.env(fake, **extra)
        return subprocess.run(
            self.argv(args), env=env, cwd=REPO, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=CMD_TIMEOUT,
        )

    def popen(self, args: list[str], fake: str, code: str = CLI_CODE) -> subprocess.Popen:
        proc = subprocess.Popen(
            self.argv(args, code), env=self.env(fake), cwd=REPO, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
        )
        self.procs.append(proc)
        return proc

    def build_args(self, *flags: str) -> list[str]:
        return ["build", "--id", "a", "--source", str(self.source), *flags, "--json"]

    def jcli(self, args: list[str], **extra: str) -> dict:
        proc = self.cli(args, **extra)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return json.loads(proc.stdout)

    def job_dir(self, job_id: str) -> Path:
        return self.dev_root / "jobs" / job_id

    def state_files(self) -> list[dict]:
        found = []
        for path in self.shim.glob("*.json"):
            try:
                found.append(json.loads(path.read_text()))
            except ValueError:
                continue
        return found

    def child_pid(self) -> int | None:
        ready = self.shim / "child.ready"
        return int(ready.read_text()) if ready.exists() and ready.read_text() else None

    def child_signals(self) -> list[str]:
        path = self.shim / "child.signals"
        return path.read_text().split() if path.exists() else []

    def wait_state(self, job_id: str, states: tuple[str, ...], timeout: float = 30.0) -> dict:
        def reached():
            job = self.jcli(["job", "status", "--job", job_id, "--json"])["job"]
            return job if job["state"] in states else None

        return wait_for(reached, f"job {job_id} to reach {states}", timeout)

    def wait_service_result(self, job_id: str) -> dict:
        def finalized():
            job = self.jcli(["job", "status", "--job", job_id, "--json"])["job"]
            return job if job["service_result"] is not None else None

        return wait_for(finalized, f"the finalizer of job {job_id}", 30.0)

    def teardown(self) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if all(s["phase"] == "finished" for s in self.state_files()):
                break
            time.sleep(0.1)
        for state in self.state_files():
            if state["phase"] != "finished":
                _kill(state.get("pgid"), group=True)
                _kill(state.get("supervisor_pid"), group=False)
        _kill(self.child_pid(), group=True)
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()


@pytest.fixture
def e2e(tmp_path):
    env = E2E(tmp_path)
    env.shim.mkdir()
    env.source.mkdir()
    lab = str(uuid.uuid4())
    uid = os.getuid()
    Registry(env.dev_root).add(LabEntry(lab, "a", uid, ["10.213.0.0/24"]))
    env.lab_uuid = lab
    LabStore(env.lab_paths).save(
        LabRecord(lab, "a", uid, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                  config_hash(DEFAULT_CONFIG))
    )
    yield env
    env.teardown()


def load_wrapper():
    spec = importlib.util.spec_from_file_location("e2e_worker_wrapper", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blocking_ok_matches_the_in_process_envelope(e2e, capsys) -> None:
    proc = e2e.cli(e2e.build_args(), fake="ok")

    expected = dev_cli.build_outcome("build", lambda: load_wrapper().fake_ok({}))
    in_process_stderr = capsys.readouterr().err
    assert proc.returncode == expected.code == 0
    parsed = json.loads(proc.stdout)  # exactly one JSON object
    assert list(parsed) == [*expected.payload, "job_id"]
    assert {k: v for k, v in parsed.items() if k != "job_id"} == expected.payload
    assert len(parsed["job_id"]) == 12
    for line in in_process_stderr.splitlines():
        if "proxy=" not in line:
            assert line in proc.stderr.splitlines()


def test_blocking_fail_host_exits_3_with_a_failed_envelope(e2e) -> None:
    proc = e2e.cli(e2e.build_args(), fake="fail-host")

    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["status"] == "failed"
    assert payload["failure_category"] == FailureCategory.HOST.value
    assert payload["message"] == "fake host failure"


def test_detach_returns_running_and_the_job_is_observable_lock_free(e2e) -> None:
    started = time.monotonic()
    launch = e2e.cli(e2e.build_args("--detach"), fake="slow-with-stage")
    assert time.monotonic() - started < 10
    assert launch.returncode == 0, launch.stderr
    payload = json.loads(launch.stdout)
    job_id = payload["job_id"]
    assert payload["status"] == "running"
    assert payload["linger"] is True

    def at_stage():
        job = e2e.jcli(["job", "status", "--job", job_id, "--json"])["job"]
        return job if job["progress"]["current_stage"] == "scenario.bootstrap" else None

    job = wait_for(at_stage, "the fake's stage in job status", 20)
    assert job["state"] == "running"
    with pytest.raises(LockBusy):  # the worker really holds the lab flock
        with exclusive_lock(e2e.lab_paths.lock):
            pass

    done = e2e.jcli(["job", "wait", "--job", job_id, "--timeout", "30", "--json"])
    assert done["timed_out"] is False
    assert done["job"]["state"] == "succeeded"
    assert done["job"]["exit_code"] == 0
    assert done["job"]["result"]["x"] == 2
    assert done["job"]["result"]["job_id"] == job_id

    log = (e2e.job_dir(job_id) / "stderr.log").read_text()
    paged, offset = "", 0
    while True:
        page = e2e.jcli(["job", "logs", "--job", job_id, "--offset", str(offset),
                         "--max-bytes", "16", "--json"])
        assert page["offset"] == offset
        paged += page["data"]
        offset = page["next_offset"]
        if page["eof"]:
            break
    assert paged == log
    assert "fake: progress 0" in log and "fake: progress 4" in log


def test_busy_lab_rejects_a_second_command_but_status_still_reads(e2e) -> None:
    launch = e2e.cli(e2e.build_args("--detach"), fake="slow-with-stage")
    job_id = json.loads(launch.stdout)["job_id"]
    wait_for(
        lambda: e2e.jcli(["job", "status", "--job", job_id, "--json"])["job"]["progress"][
            "current_stage"],
        "the running stage", 20,
    )

    second = e2e.cli(e2e.build_args("--detach"))
    assert second.returncode == 2
    assert job_id in json.loads(second.stdout)["message"]

    status = e2e.cli(["status", "--id", "a", "--json"])
    assert status.returncode == 0, status.stderr
    body = json.loads(status.stdout)
    assert body["busy"] is True
    assert body["active_job"] == job_id
    e2e.jcli(["job", "wait", "--job", job_id, "--timeout", "30", "--json"])


def test_cancel_stops_the_worker_and_its_child_group_exactly_once(e2e) -> None:
    job_id = json.loads(e2e.cli(e2e.build_args("--detach"), fake="hang-until-sigterm").stdout)[
        "job_id"]
    wait_for(lambda: e2e.child_pid(), "the fake's child process", 30)
    assert e2e.jcli(["job", "status", "--job", job_id, "--json"])["job"]["state"] == "running"

    cancelled = e2e.jcli(["job", "cancel", "--job", job_id, "--json"])

    assert cancelled["cancelled"] is True
    assert cancelled["job"]["state"] == "interrupted"
    assert cancelled["job"]["exit_code"] == 130
    assert e2e.child_signals() == ["SIGTERM"]
    assert e2e.wait_service_result(job_id)["service_result"] == "exit-code"


def test_crash_sigkill_is_finalized_as_failed_internal(e2e) -> None:
    job_id = json.loads(e2e.cli(e2e.build_args("--detach"), fake="crash-sigkill").stdout)[
        "job_id"]

    done = e2e.jcli(["job", "wait", "--job", job_id, "--timeout", "30", "--json"])["job"]

    assert done["state"] == "failed"
    assert done["exit_code"] == 6
    assert done["result"]["failure_category"] == FailureCategory.INTERNAL.value
    assert e2e.wait_service_result(job_id)["service_result"] == "signal"


def test_runtime_max_expiry_is_a_timeout_never_a_success(e2e) -> None:
    launch = e2e.cli(e2e.build_args("--detach"), fake="hang-until-sigterm",
                     SHIM_RUNTIME_MAX_OVERRIDE="3")
    job_id = json.loads(launch.stdout)["job_id"]

    done = e2e.jcli(["job", "wait", "--job", job_id, "--timeout", "40", "--json"])["job"]

    assert done["state"] in ("failed", "interrupted")
    assert e2e.wait_service_result(job_id)["service_result"] == "timeout"
    assert len(e2e.child_signals()) <= 1


def test_no_systemd_user_manager_fails_early_and_leaves_no_job(e2e, tmp_path) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    for flags in ([], ["--detach"]):
        proc = e2e.cli(e2e.build_args(*flags), PATH=str(empty))

        assert proc.returncode == 3
        payload = json.loads(proc.stdout)
        assert payload["failure_category"] == FailureCategory.HOST.value
        assert "systemd-user" in payload["message"]
        assert "loginctl enable-linger" in payload["remediation"]
    assert list((e2e.dev_root / "jobs").glob("*")) == []


def test_blocking_sigint_prints_the_recorded_130_envelope(e2e) -> None:
    proc = e2e.popen(e2e.build_args(), "hang-until-sigterm", code=SIGINT_CLI_CODE)
    wait_for(lambda: e2e.child_pid(), "the fake's child process", 30)

    proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=CMD_TIMEOUT)

    assert proc.returncode == 130
    payload = json.loads(out)
    assert payload["status"] == "interrupted"
    recorded = json.loads((e2e.job_dir(payload["job_id"]) / "result.json").read_text())
    assert recorded["exit_code"] == 130
    assert payload == recorded["envelope"]
    assert e2e.child_signals() == ["SIGTERM"]


def test_secret_proxy_reaches_the_worker_but_never_an_argv(e2e) -> None:
    proc = e2e.cli(e2e.build_args(), fake="ok", HTTPS_PROXY="http://u:SECRET@h",
                   AWS_SECRET_ACCESS_KEY="not-forwarded")

    assert proc.returncode == 0, proc.stderr
    job_dir = e2e.job_dir(json.loads(proc.stdout)["job_id"])
    assert "proxy=http://u:SECRET@h" in (job_dir / "stderr.log").read_text()
    argv_files = list(e2e.shim.glob("*.argv"))
    assert argv_files
    for argv_file in argv_files:
        assert "SECRET" not in argv_file.read_text()
    assert (job_dir / "env").stat().st_mode & 0o777 == 0o600
    assert "AWS_" not in (job_dir / "env").read_text()
    assert "SECRET" not in (job_dir / "job.json").read_text()


def test_sudo_failure_blocks_create_but_not_build(e2e) -> None:
    create = e2e.cli(["create", "--id", "a", "--detach", "--json"], SHIM_SUDO_FAIL="1")

    assert create.returncode == 3
    payload = json.loads(create.stdout)
    assert payload["failure_category"] == FailureCategory.HOST.value
    assert "NOPASSWD" in payload["remediation"]
    assert list((e2e.dev_root / "jobs").glob("*")) == []

    build = e2e.cli(e2e.build_args("--detach"), SHIM_SUDO_FAIL="1")

    assert build.returncode == 0, build.stderr
    body = json.loads(build.stdout)
    assert body["status"] == "running"
    e2e.jcli(["job", "wait", "--job", body["job_id"], "--timeout", "30", "--json"])
