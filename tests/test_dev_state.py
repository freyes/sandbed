from __future__ import annotations

import os

from sandbed.dev import state
from sandbed.dev.state import (
    EXEC_RUNNING,
    EXEC_STOPPED,
    EXEC_UNKNOWN,
    FAILED,
    INTERRUPTED,
    RUNNING,
    SUCCEEDED,
    AttemptLog,
)


def make(tmp_path) -> AttemptLog:
    return AttemptLog(tmp_path / "attempts.json")


def test_attempts_are_numbered_per_stage(tmp_path) -> None:
    log = make(tmp_path)
    first = log.begin("r1", "provision", "fp")
    log.finish(first, FAILED, exit_code=1)
    second = log.begin("r2", "provision", "fp")
    assert (first.attempt, second.attempt) == (1, 2)
    assert log.begin("r2", "deploy", "fp").attempt == 1


def test_finish_records_outcome_and_stops_execution(tmp_path) -> None:
    log = make(tmp_path)
    a = log.begin("r", "provision", "fp")
    done = log.finish(a, SUCCEEDED, exit_code=0, evidence=("/log",))
    assert (done.state, done.exit_code, done.execution_state) == (SUCCEEDED, 0, EXEC_STOPPED)
    assert done.evidence == ["/log"]
    assert log.blocked_reason() is None


def test_running_attempt_with_a_live_controller_blocks(tmp_path) -> None:
    log = make(tmp_path)
    log.begin("r", "deploy", "fp")
    assert "still running" in (log.blocked_reason() or "")
    assert log.reconcile(lambda _a: EXEC_STOPPED) == []  # our own pid is alive: leave it


def test_orphaned_running_attempt_is_interrupted_never_succeeded(tmp_path, monkeypatch) -> None:
    log = make(tmp_path)
    log.begin("r", "bootstrap", "fp")
    monkeypatch.setattr(state, "process_identity", lambda _pid: None)  # controller is gone
    changed = log.reconcile(lambda _a: EXEC_UNKNOWN)
    assert [(a.state, a.execution_state) for a in changed] == [(INTERRUPTED, EXEC_UNKNOWN)]
    assert "unknown" in (log.blocked_reason() or "")


def test_orphan_whose_work_is_confirmed_stopped_unblocks(tmp_path, monkeypatch) -> None:
    log = make(tmp_path)
    log.begin("r", "provision", "fp")
    monkeypatch.setattr(state, "process_identity", lambda _pid: None)
    log.reconcile(lambda _a: EXEC_STOPPED)
    assert log.blocked_reason() is None
    assert log.attempts()[0].state == INTERRUPTED


def test_orphan_whose_guest_work_still_runs_blocks(tmp_path, monkeypatch) -> None:
    log = make(tmp_path)
    log.begin("r", "bootstrap", "fp")
    monkeypatch.setattr(state, "process_identity", lambda _pid: None)
    log.reconcile(lambda _a: EXEC_RUNNING)
    assert "running" in (log.blocked_reason() or "")


def test_recycled_pid_is_not_mistaken_for_the_controller(tmp_path, monkeypatch) -> None:
    log = make(tmp_path)
    log.begin("r", "deploy", "fp")
    monkeypatch.setattr(state, "process_identity", lambda _pid: "different-start-time")
    assert [a.state for a in log.reconcile(lambda _a: EXEC_UNKNOWN)] == [INTERRUPTED]


def test_process_identity_of_this_process_is_stable() -> None:
    assert state.process_identity(os.getpid()) == state.process_identity(os.getpid())
    assert state.process_identity(2**22 + 12345) is None
    assert RUNNING == "running"