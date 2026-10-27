from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import install_systemd_guard

from sandbed.dev import systemd
from sandbed.dev.errors import HostError

JOB = SimpleNamespace(
    job_id="0123456789ab", command="build", cwd="/work/tree", dir="/state/jobs/0123456789ab"
)
PY = "/opt/venv/bin/python"

EXPECTED = [
    "systemd-run", "--user", "--quiet", "--collect",
    "--unit=sandbed-dev-0123456789ab",
    "--description=sandbed dev build job 0123456789ab",
    "--service-type=exec",
    "--working-directory=/work/tree",
    "-p", "KillMode=mixed",
    "-p", "TimeoutStopSec=60s",
    "-p", "RuntimeMaxSec=900",
    "-p", "EnvironmentFile=/state/jobs/0123456789ab/env",
    "-p", "StandardOutput=append:/state/jobs/0123456789ab/stdout.log",
    "-p", "StandardError=append:/state/jobs/0123456789ab/stderr.log",
    "-p", f"ExecStopPost={PY} -m sandbed.dev.worker --finalize --job-id 0123456789ab",
    "--", PY, "-m", "sandbed.dev.worker", "--job-id", "0123456789ab",
]


def test_argv_matches_snapshot() -> None:
    assert systemd.unit_argv(JOB, False, PY, runtime_max=900) == EXPECTED


def test_wait_flag_only_when_waiting() -> None:
    waiting = systemd.unit_argv(JOB, True, PY, runtime_max=900)
    assert waiting == [*EXPECTED[:4], "--wait", *EXPECTED[4:]]
    assert "--wait" not in systemd.unit_argv(JOB, False, PY, runtime_max=900)


def test_exec_argv_replaces_worker_command() -> None:
    argv = systemd.unit_argv(JOB, False, PY, exec_argv=["/bin/true"], runtime_max=1)
    assert argv[argv.index("--") + 1:] == ["/bin/true"]


def test_argv_has_none_of_the_forbidden_options() -> None:
    joined = " ".join(systemd.unit_argv(JOB, True, PY, runtime_max=900))
    for banned in ("ExitType", "expand-environment", "control-group"):
        assert banned not in joined
    assert "-E" not in systemd.unit_argv(JOB, True, PY, runtime_max=900)


def test_runtime_max_emitted_only_when_given() -> None:
    with_max = systemd.unit_argv(JOB, False, PY, runtime_max=7)
    assert "RuntimeMaxSec=7" in with_max
    without = systemd.unit_argv(JOB, False, PY)
    assert not any(e.startswith("RuntimeMaxSec") for e in without)


def test_unit_properties_are_exactly_the_allowed_set() -> None:
    argv = systemd.unit_argv(JOB, True, PY, runtime_max=900)
    names = {argv[i + 1].split("=", 1)[0] for i, e in enumerate(argv) if e == "-p"}
    assert names == {
        "KillMode", "TimeoutStopSec", "RuntimeMaxSec", "EnvironmentFile",
        "StandardOutput", "StandardError", "ExecStopPost",
    }
    assert "-E" not in argv
    assert not any(e.startswith(("--setenv", "Environment=")) for e in argv)


@pytest.mark.parametrize("bad", [" ", "\t", '"', "'", "\\", "$", "%", ";"])
def test_check_launchable_rejects_hostile_python(bad) -> None:
    with pytest.raises(HostError):
        systemd.check_launchable(f"/opt/py{bad}thon", "/state/jobs/x")


@pytest.mark.parametrize("bad", [" ", '"', "'", "\\", "$", "%", ";"])
def test_check_launchable_rejects_hostile_jobdir(bad) -> None:
    with pytest.raises(HostError):
        systemd.check_launchable(PY, f"/state/a{bad}b/jobs/x")


def test_check_launchable_accepts_plain_paths() -> None:
    systemd.check_launchable(PY, Path("/home/u/.local/state/sandbed/dev/jobs/0123456789ab"))


def test_parse_version() -> None:
    assert systemd.parse_version("systemd 249 (249.11-0ubuntu3.12)") == 249
    old = systemd.parse_version("systemd 239 (239-45.el8)")
    assert old is not None and old < systemd.MIN_SYSTEMD
    assert systemd.parse_version("garbage") is None


def test_bus_env_defaults_when_unset_and_runtime_dir_exists(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_dir", lambda self: str(self) == "/run/user/1234")
    env = systemd.bus_env({"PATH": "/usr/bin"}, 1234)
    assert env == {
        "PATH": "/usr/bin",
        "XDG_RUNTIME_DIR": "/run/user/1234",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1234/bus",
    }


def test_bus_env_keeps_existing_values(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    given = {"XDG_RUNTIME_DIR": "/x", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x/b"}
    assert systemd.bus_env(given, 1) == given


def test_bus_env_leaves_environment_alone_without_runtime_dir(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    assert systemd.bus_env({"PATH": "/usr/bin"}, 1) == {"PATH": "/usr/bin"}


def test_show_returns_properties_for_collected_unit(monkeypatch) -> None:
    seen = []

    def fake_run(argv, *, timeout, env=None):
        seen.append(argv)
        return 0, "ActiveState=inactive\nSubState=dead\nLoadState=not-found\n", ""

    monkeypatch.setattr(systemd, "_run", fake_run)
    props = systemd.show("sandbed-dev-x")
    assert props is not None and props["LoadState"] == "not-found"
    assert seen[0][:3] == ["systemctl", "--user", "show"] and seen[0][-1] == "sandbed-dev-x"
    assert not systemd.unit_active(props)


def test_show_is_none_when_manager_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(systemd, "_run", lambda argv, *, timeout, env=None: (1, "", "no bus"))
    assert systemd.show("u") is None
    assert not systemd.unit_active(None)


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading"])
def test_unit_active_states(state) -> None:
    assert systemd.unit_active({"ActiveState": state})


def test_stop_tolerates_unit_not_loaded(monkeypatch) -> None:
    monkeypatch.setattr(
        systemd, "_run", lambda argv, *, timeout, env=None: (5, "", "Unit x.service not loaded.")
    )
    systemd.stop("x", 5)


def test_stop_raises_on_other_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        systemd, "_run", lambda argv, *, timeout, env=None: (1, "", "Failed to connect to bus")
    )
    with pytest.raises(HostError):
        systemd.stop("x", 5)


def test_start_uses_devnull_stdout_and_piped_stderr(monkeypatch) -> None:
    import subprocess

    captured = {}
    monkeypatch.setattr(
        systemd, "_popen", lambda argv, **kw: captured.update(argv=argv, **kw) or "proc"
    )
    assert systemd.start(["systemd-run"], wait=True) == "proc"
    assert captured["stdout"] == subprocess.DEVNULL and captured["stderr"] == subprocess.PIPE


def test_guard_fails_unpatched_seams(monkeypatch) -> None:
    monkeypatch.delenv("SANDBED_DEV_INTEGRATION", raising=False)
    with monkeypatch.context() as m:
        install_systemd_guard(m)
        with pytest.raises(AssertionError, match="patch the seam"):
            systemd.show("u")
        with pytest.raises(AssertionError, match="patch the seam"):
            systemd.start(["systemd-run"], wait=False)


def test_guard_is_noop_for_integration_runs(monkeypatch) -> None:
    monkeypatch.setenv("SANDBED_DEV_INTEGRATION", "1")
    with monkeypatch.context() as m:
        before = (systemd._popen, systemd._run)
        install_systemd_guard(m)
        assert (systemd._popen, systemd._run) == before


@pytest.mark.parametrize(
    ("code", "out", "expected"),
    [(0, "Linger=yes\n", True), (0, "Linger=no\n", False), (0, "", None), (1, "Linger=yes", None)],
)
def test_linger_parses_loginctl(monkeypatch, code, out, expected) -> None:
    calls = []

    def fake_run(argv, *, timeout, env=None):
        calls.append(argv)
        return code, out, ""

    monkeypatch.setattr(systemd, "_run", fake_run)
    assert systemd.linger(1000) is expected
    assert calls == [["loginctl", "show-user", "1000", "-p", "Linger"]]
