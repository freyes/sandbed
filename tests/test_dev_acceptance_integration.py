"""Real-host acceptance tests for `sandbed dev`. Skipped unless SANDBED_DEV_INTEGRATION=1.

When enabled they refuse to run on an unready host and say exactly what is missing; they never
fall back to anything fake. Required: SANDBED_DEV_SOURCE = a snap-openstack checkout at the
commit being qualified.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tarfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from sandbed.dev import jobs, systemd

pytestmark = pytest.mark.skipif(
    os.environ.get("SANDBED_DEV_INTEGRATION") != "1",
    reason="set SANDBED_DEV_INTEGRATION=1 to run real-host acceptance",
)
CLI = [sys.executable, "-c", "from sandbed.cli import cli; cli()"]
CANARY = "SANDBED-CANARY-9f3c"


def sbd(*args: str, timeout: int = 7200) -> tuple[int, dict]:
    proc = subprocess.run([*CLI, "dev", *args], capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"unparsed_stdout": proc.stdout, "stderr": proc.stderr[-2000:]}
    return proc.returncode, payload


@pytest.fixture(scope="session")
def host_ready() -> None:
    code, payload = sbd("host", "check", "--json", timeout=300)
    if code != 0:
        failed = [c for c in payload.get("checks", []) if not c["ok"]]
        pytest.fail(
            "host is not ready for real-host qualification: "
            + "; ".join(f"{c['name']} ({c['detail']}): {c['remediation']}" for c in failed)
        )


@pytest.fixture(scope="session")
def source() -> str:
    path = os.environ.get("SANDBED_DEV_SOURCE")
    if not path:
        pytest.fail("SANDBED_DEV_SOURCE must point at the snap-openstack checkout under test")
    return path


@pytest.fixture
def lab(host_ready):
    """A fresh disposable lab, destroyed afterwards even if the test failed."""
    label = f"it-{uuid.uuid4().hex[:8]}"
    code, payload = sbd("create", "--id", label, "--json")
    assert code == 0, payload
    try:
        yield label
    finally:
        sbd("destroy", "--id", label, "--json")


def built(lab: str, source: str) -> dict:
    code, built = sbd("build", "--id", lab, "--source", source, "--json")
    assert code == 0, built
    return built


def test_happy_path_identifies_the_exact_snap_and_scenario(lab, source) -> None:
    artifact = built(lab, source)
    code, deployed = sbd("deploy", "--id", lab, "--artifact", artifact["artifact_id"], "--json")
    assert code == 0, deployed
    assert deployed["installed_sha256"] == artifact["artifact"]["sha256"]
    code, result = sbd("test", "--id", lab, "--json")
    assert code == 0, result
    assert result["artifact"]["sha256"] == artifact["artifact"]["sha256"]
    assert result["scenario"] == {"name": "bootstrap-and-launch", "version": 1}
    assert all(result["assertions"].values())


def test_tampered_snap_is_rejected_before_installation(lab, source) -> None:
    artifact = built(lab, source)
    pattern = f".local/state/sandbed/dev/labs/*/artifacts/{artifact['artifact_id']}"
    snap = next(Path.home().glob(pattern))
    (snap / "openstack.snap").chmod(0o600)
    with open(snap / "openstack.snap", "ab") as fh:
        fh.write(b"tamper")
    code, out = sbd("deploy", "--id", lab, "--artifact", artifact["artifact_id"], "--json")
    assert code == 4 and "no longer matches" in out["message"]


def test_two_labs_are_isolated_and_destroy_is_repeatable(host_ready) -> None:
    labs = [f"it-iso-{i}-{p}" for i, p in enumerate((CANARY[-4:], CANARY[-8:-4]))]
    for label in labs:
        assert sbd("create", "--id", label, "--json")[0] == 0
    try:
        a, b = (sbd("status", "--id", label, "--json")[1] for label in labs)
        assert a["vm_address"] != b["vm_address"] and a["lab_id"] != b["lab_id"]
        assert sbd("destroy", "--id", labs[0], "--json")[0] == 0
        assert sbd("destroy", "--id", labs[0], "--json")[0] == 0  # repeatable
        assert sbd("host", "check", "--json")[0] == 0  # shared backend intact
        assert sbd("status", "--id", labs[1], "--json")[1]["provisioning_state"] == "ready"
    finally:
        for label in labs:
            sbd("destroy", "--id", label, "--json")


def test_exported_archive_never_contains_credentials(lab, source) -> None:
    artifact = built(lab, source)
    deploy_code, _ = sbd("deploy", "--id", lab, "--artifact", artifact["artifact_id"], "--json")
    assert deploy_code == 0, "deploy must succeed for the archive to be meaningful"
    test_code, result = sbd("test", "--id", lab, "--json")
    assert test_code == 0, "test must succeed to produce a diagnostics archive"
    with tarfile.open(result["diagnostics_path"]) as tar:
        members = tar.getmembers()
        assert members, "diagnostics archive must have at least one member"
        names = {m.name for m in members}
        assert "index.json" in names, "diagnostics archive must contain index.json"
        for member in members:
            data = tar.extractfile(member).read().decode(errors="replace")
            assert "OS_PASSWORD" not in data and "PRIVATE KEY" not in data, member.name
            assert CANARY not in data


# --- detached jobs under the REAL systemd user manager (only the FAKE worker runs) ---------
REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "tests" / "fixtures" / "worker_wrapper.py"
PROXY_MARKER = "http://env-file-honoured.invalid:3128"


def _poll(predicate: Callable[[], object], what: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for {what}")
        time.sleep(0.1)


@dataclass
class Unit:
    job: jobs.Job
    argv: list[str]
    state: Path
    env: dict[str, str]

    @property
    def name(self) -> str:
        return self.job.unit

    def start(self, wait: bool):
        return systemd.start(self.argv, wait=wait, env=self.env)

    def exit(self) -> dict:
        return _poll(lambda: jobs.read_exit(self.job), "exit.json (ExecStopPost)")

    def result(self) -> dict:
        return _poll(lambda: jobs.read_result(self.job), "result.json")

    def systemctl(self, *args: str) -> str:
        code, out, err = systemd._run(["systemctl", "--user", *args], timeout=30, env=self.env)
        assert code == 0, (args, err)
        return out


def _skip_unless_systemd_user() -> dict[str, str]:
    env = systemd.bus_env(os.environ, os.getuid())
    for label, argv in (
        ("systemd-run --user --version", ["systemd-run", "--user", "--version"]),
        ("systemctl --user show-environment", ["systemctl", "--user", "show-environment"]),
    ):
        code, _, err = systemd._run(argv, timeout=30, env=env)
        if code != 0:
            pytest.skip(f"SANDBED_DEV_INTEGRATION=1 but `{label}` failed: {err.strip()}")
    return env


@pytest.fixture
def make_unit(tmp_path, monkeypatch):
    env = _skip_unless_systemd_user()
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    made: list[Unit] = []

    def build(mode: str, runtime_max: int = 300) -> Unit:
        evidence = tmp_path / f"evidence-{len(made)}"
        evidence.mkdir()
        job = jobs.create_job("destroy", {"lab": "detached-it"}, "detached-it", str(REPO), True)
        jobs.write_env_file(job, jobs.worker_env({
            "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(tmp_path)),
            "XDG_STATE_HOME": str(state_home), "HTTPS_PROXY": PROXY_MARKER,
            "SANDBED_E2E_FAKE": mode, "SANDBED_E2E_STATE": str(evidence),
        }))
        argv = systemd.unit_argv(
            job, False, sys.executable,
            exec_argv=[sys.executable, str(WRAPPER), "--job-id", job.job_id],
            runtime_max=runtime_max,
        )
        unit = Unit(job, argv, evidence, env)
        made.append(unit)
        return unit

    def with_wait(unit: Unit) -> Unit:
        unit.argv.insert(unit.argv.index("--collect") + 1, "--wait")
        return unit

    build.wait = with_wait  # type: ignore[attr-defined]
    yield build
    for unit in made:
        for verb in ("stop", "reset-failed"):
            systemd._run(["systemctl", "--user", verb, unit.name], timeout=90, env=env)
    _, listing, _ = systemd._run(
        ["systemctl", "--user", "list-units", "sandbed-dev-*", "--all", "--no-legend"],
        timeout=30, env=env,
    )
    leftovers = [u.name for u in made if u.name in listing]
    assert not leftovers, f"units left behind: {leftovers}\n{listing}"


def test_detached_wait_run_publishes_result_and_exit(make_unit) -> None:
    unit = make_unit.wait(make_unit("ok"))
    client = unit.start(wait=True)
    assert client.wait(timeout=60) == 0, client.stderr.read()
    result = unit.result()
    assert result["envelope"]["status"] == "succeeded" and result["exit_code"] == 0
    assert unit.exit()["service_result"] == "success"
    stderr = unit.job.paths.stderr
    assert stat.S_IMODE(stderr.stat().st_mode) == 0o600
    assert stat.S_IMODE(unit.job.paths.stdout.stat().st_mode) == 0o600
    text = stderr.read_text()
    assert "fake: stage one" in text and "fake: stage two" in text
    assert f"fake: proxy={PROXY_MARKER}" in text  # EnvironmentFile honoured
    props = systemd.show(unit.name, env=unit.env)
    assert props is not None and props["LoadState"] == "not-found"  # --collect gc'd it


def test_detached_stop_signals_the_worker_once_under_kill_mode_mixed(make_unit) -> None:
    unit = make_unit("hang-until-sigterm")
    assert unit.start(wait=False).wait(timeout=30) == 0
    _poll(lambda: (unit.state / "child.ready").exists(), "fake child ready")
    shown = dict(
        line.split("=", 1)
        for line in unit.systemctl(
            "show", "-p", "KillMode", "-p", "RuntimeMaxUSec", "-p", "TimeoutStopUSec", unit.name
        ).splitlines()
    )
    assert shown["KillMode"] == "mixed"
    assert shown["RuntimeMaxUSec"] == "5min"
    assert shown["TimeoutStopUSec"] == "1min"
    systemd.stop(unit.name, 90, env=unit.env)
    result = unit.result()
    assert result["envelope"]["status"] == "interrupted" and result["exit_code"] == 130
    unit.exit()
    signals = (unit.state / "child.signals").read_text().splitlines()
    assert signals == ["SIGTERM"], "the child must see exactly one SIGTERM (no double interrupt)"


def test_detached_runtime_max_sec_ends_with_service_result_timeout(make_unit) -> None:
    unit = make_unit("hang-until-sigterm", runtime_max=3)
    assert unit.start(wait=False).wait(timeout=30) == 0
    assert unit.exit()["service_result"] == "timeout"
    assert unit.result()["exit_code"] in (6, 130)


def test_detached_sigkilled_worker_gets_a_synthesized_internal_result(make_unit) -> None:
    unit = make_unit("crash-sigkill")
    assert unit.start(wait=False).wait(timeout=30) == 0
    exit_info = unit.exit()
    assert exit_info["service_result"] != "success"
    result = unit.result()
    assert result["exit_code"] == 6
    assert result["envelope"]["status"] == "failed"
    assert result["envelope"]["failure_category"] == "internal"
