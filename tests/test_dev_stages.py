from __future__ import annotations

import pytest

from sandbed.dev import stages
from sandbed.dev.errors import DeploymentError, InternalError, Interrupted, ScenarioError
from sandbed.dev.runner import CommandFailed, CommandResult
from sandbed.dev.ssh import SshTarget
from sandbed.dev.stages import stage
from sandbed.dev.state import EXEC_RUNNING, EXEC_STOPPED, EXEC_UNKNOWN, AttemptLog


def failed_result(code: int = 5) -> CommandResult:
    from pathlib import Path

    return CommandResult(("false",), code, False, 0.1, Path("/log"), "")


def test_success_is_recorded(tmp_path) -> None:
    log = AttemptLog(tmp_path / "a.json")
    with stage(log, "r", "deploy.install", layer=DeploymentError):
        pass
    [a] = log.attempts()
    assert (a.state, a.exit_code) == ("succeeded", 0)


def test_command_failure_is_reported_as_the_stage_layer(tmp_path) -> None:
    log = AttemptLog(tmp_path / "a.json")
    with pytest.raises(DeploymentError) as exc, stage(
        log, "r", "deploy.install", layer=DeploymentError
    ):
        raise CommandFailed(failed_result(5))
    assert exc.value.stage == "deploy.install"
    [a] = log.attempts()
    assert (a.state, a.exit_code, a.execution_state) == ("failed", 5, EXEC_STOPPED)


def test_dev_errors_pass_through_with_the_stage_name(tmp_path) -> None:
    log = AttemptLog(tmp_path / "a.json")
    with pytest.raises(ScenarioError) as exc, stage(
        log, "r", "scenario.launch", layer=ScenarioError
    ):
        raise ScenarioError("no server")
    assert exc.value.stage == "scenario.launch"


def test_interrupt_leaves_execution_unknown(tmp_path) -> None:
    log = AttemptLog(tmp_path / "a.json")
    with pytest.raises(Interrupted), stage(log, "r", "scenario.bootstrap", layer=ScenarioError):
        raise Interrupted("ctrl-c")
    [a] = log.attempts()
    assert (a.state, a.execution_state) == ("interrupted", EXEC_UNKNOWN)
    assert log.blocked_reason()


def test_unexpected_exceptions_become_internal_errors(tmp_path) -> None:
    log = AttemptLog(tmp_path / "a.json")
    with pytest.raises(InternalError), stage(log, "r", "x", layer=DeploymentError):
        raise KeyError("bug")
    assert log.attempts()[0].state == "failed"


def test_guest_stage_dir_is_unique_per_attempt(tmp_path) -> None:
    log = AttemptLog(tmp_path / "a.json")
    first = log.begin("r", "deploy.install", "")
    second = log.begin("r", "deploy.install", "")
    assert stages.guest_stage_dir("/home/u", first) != stages.guest_stage_dir("/home/u", second)
    assert stages.guest_stage_dir("/home/u", first).endswith("/r/deploy.install-1")


class FakeRun:
    """Stands in for run_logged, keyed by the guest command's first word."""

    def __init__(self, answers: dict[str, tuple[bool, str]]) -> None:
        self.answers, self.seen = answers, []

    def __call__(self, argv, **_kw):
        from pathlib import Path

        remote = argv[-1]
        self.seen.append(remote)
        for prefix, (ok, out) in self.answers.items():
            if remote.startswith(prefix):
                return CommandResult(tuple(argv), 0 if ok else 1, False, 0, Path("/x"), out)
        return CommandResult(tuple(argv), 1, False, 0, Path("/x"), "")


def probe_with(monkeypatch, tmp_path, answers):
    from pathlib import Path

    fake = FakeRun(answers)
    monkeypatch.setattr(stages, "run_logged", fake)
    log = AttemptLog(tmp_path / "a.json")
    attempt = log.begin("r", "scenario.bootstrap", "")
    target = SshTarget("10.0.0.2", "ubuntu", Path("/k"), Path("/kh"))
    return stages.guest_probe(target, "/home/ubuntu", tmp_path / "probe.log")(attempt), fake


def test_probe_rc_present_means_stopped(monkeypatch, tmp_path) -> None:
    result, _ = probe_with(monkeypatch, tmp_path, {"test -f": (True, "")})
    assert result == EXEC_STOPPED


def test_probe_live_pid_means_running(monkeypatch, tmp_path) -> None:
    answers = {"test -f": (False, ""), "cat": (True, "4242\n"), "kill -0 4242": (True, "")}
    result, _ = probe_with(monkeypatch, tmp_path, answers)
    assert result == EXEC_RUNNING


def test_probe_dead_pid_without_rc_is_unknown_not_stopped(monkeypatch, tmp_path) -> None:
    answers = {"test -f": (False, ""), "cat": (True, "4242\n"), "kill -0": (False, "")}
    result, _ = probe_with(monkeypatch, tmp_path, answers)
    assert result == EXEC_UNKNOWN


def test_probe_unreachable_guest_is_unknown(monkeypatch, tmp_path) -> None:
    result, _ = probe_with(monkeypatch, tmp_path, {})
    assert result == EXEC_UNKNOWN
