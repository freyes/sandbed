"""Stage attempts and recovery.

Every stage attempt is recorded before it starts and finished after it ends. A record left in
`running` whose controller process is gone is *interrupted*, never succeeded, and whether the
underlying work has stopped is a separate fact (`execution_state`) that must be established
before the lab can be mutated again.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .store import atomic_write_json, read_json

PENDING, RUNNING, SUCCEEDED, FAILED, INTERRUPTED = (
    "pending", "running", "succeeded", "failed", "interrupted",
)
# Whether the work behind the attempt is still going: running | stopped | unknown.
EXEC_RUNNING, EXEC_STOPPED, EXEC_UNKNOWN = "running", "stopped", "unknown"

GuestProbe = Callable[["Attempt"], str]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def process_identity(pid: int) -> str | None:
    """Start time (clock ticks since boot) from /proc/<pid>/stat; distinguishes a recycled pid."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # comm may contain spaces and parentheses; fields resume after the last ')'.
    return raw.rsplit(")", 1)[1].split()[19]


@dataclass
class Attempt:
    run_id: str
    stage: str
    attempt: int
    state: str
    input_fingerprint: str
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    controller_pid: int | None = None
    controller_start: str | None = None
    execution_state: str = EXEC_RUNNING
    evidence: list[str] = field(default_factory=list)

    def controller_alive(self) -> bool:
        if self.controller_pid is None:
            return False
        return process_identity(self.controller_pid) == self.controller_start


class AttemptLog:
    """`attempts.json` of one lab. Callers hold the lab lock while mutating."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def attempts(self) -> list[Attempt]:
        raw = read_json(self.path, default=[])
        return [Attempt(**item) for item in raw]  # type: ignore[union-attr]

    def _save(self, items: list[Attempt]) -> None:
        atomic_write_json(self.path, [asdict(a) for a in items])

    def begin(self, run_id: str, stage: str, fingerprint: str) -> Attempt:
        items = self.attempts()
        number = 1 + sum(1 for a in items if a.stage == stage)
        pid = os.getpid()
        attempt = Attempt(
            run_id=run_id,
            stage=stage,
            attempt=number,
            state=RUNNING,
            input_fingerprint=fingerprint,
            started_at=now(),
            controller_pid=pid,
            controller_start=process_identity(pid),
        )
        items.append(attempt)
        self._save(items)
        return attempt

    def finish(
        self,
        attempt: Attempt,
        state: str,
        *,
        exit_code: int | None = None,
        evidence: tuple[str, ...] = (),
        execution_state: str | None = None,
    ) -> Attempt:
        items = self.attempts()
        for item in items:
            if (item.run_id, item.stage, item.attempt) == (
                attempt.run_id, attempt.stage, attempt.attempt,
            ):
                item.state = state
                item.finished_at = now()
                item.exit_code = exit_code
                item.evidence = [*item.evidence, *evidence]
                # A stage that returned (success or failure) has no work left behind it.
                item.execution_state = execution_state or (
                    EXEC_UNKNOWN if state == INTERRUPTED else EXEC_STOPPED
                )
                self._save(items)
                return item
        raise KeyError(f"unknown attempt: {attempt.stage}#{attempt.attempt}")

    def reconcile(self, probe: GuestProbe) -> list[Attempt]:
        """Mark orphaned `running` attempts interrupted and ask `probe` whether their work has
        stopped. Returns the attempts changed. Never promotes anything to succeeded."""
        items = self.attempts()
        changed = []
        for item in items:
            if item.state != RUNNING or item.controller_alive():
                continue
            item.state = INTERRUPTED
            item.finished_at = now()
            item.execution_state = probe(item)
            changed.append(item)
        if changed:
            self._save(items)
        return changed

    def blocked_reason(self) -> str | None:
        """Why the lab must not be mutated, or None. Collection and status ignore this."""
        for item in self.attempts():
            if item.state == RUNNING and item.controller_alive():
                return f"{item.stage} is still running (pid {item.controller_pid})"
            if item.state == RUNNING:
                return f"{item.stage} was left running; reconcile first"
            if item.state == INTERRUPTED and item.execution_state != EXEC_STOPPED:
                return (
                    f"{item.stage} was interrupted and its work is "
                    f"{item.execution_state}; reconcile or collect and destroy the lab"
                )
        return None