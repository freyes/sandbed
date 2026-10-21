"""Open a lab for a command: find it, take its lock, reconcile unfinished work, and refuse
mutation while anything is unresolved."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..shell import run
from .errors import InputError
from .labs import LabEntry, Registry
from .paths import LabPaths, lab_paths
from .record import LabRecord, LabStore
from .ssh import SshTarget
from .stages import guest_probe
from .state import EXEC_RUNNING, EXEC_STOPPED, EXEC_UNKNOWN, Attempt, AttemptLog
from .store import exclusive_lock

_HOST_STAGES = ("provision", "destroy")
_GUEST_STAGES = ("deploy.", "scenario.")


@dataclass
class LabContext:
    entry: LabEntry
    paths: LabPaths
    store: LabStore
    record: LabRecord
    log: AttemptLog

    def target(self, *, first_contact: bool = False) -> SshTarget:
        if not self.record.vm_address:
            raise InputError("the lab has no VM address yet; run `sandbed dev create` first")
        return SshTarget(
            self.record.vm_address, self.record.vm_user, self.paths.ssh_key,
            self.paths.known_hosts, first_contact,
        )


def ere_escape(text: str) -> str:
    """Escape POSIX extended-regex metacharacters (pgrep -f takes a regex)."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", text)


def host_probe(paths: LabPaths) -> Callable[[Attempt], str]:
    """Host-side work (terraform) is stopped unless a process still runs against this lab."""

    def probe(_attempt: Attempt) -> str:
        found = run(["pgrep", "-f", "--", f"-chdir={ere_escape(str(paths.root))}(/|$| )"],
                    check=False)
        return EXEC_RUNNING if found.returncode == 0 else EXEC_STOPPED

    return probe


def make_probe(ctx_paths: LabPaths, record: LabRecord) -> Callable[[Attempt], str]:
    host = host_probe(ctx_paths)
    guest = None
    if record.vm_address and ctx_paths.ssh_key.exists():
        target = SshTarget(
            record.vm_address, record.vm_user, ctx_paths.ssh_key, ctx_paths.known_hosts
        )
        guest = guest_probe(target, record.home, ctx_paths.private / "reconcile.log")

    def probe(attempt: Attempt) -> str:
        if attempt.stage in _HOST_STAGES:
            return host(attempt)
        if attempt.stage.startswith(_GUEST_STAGES):
            return guest(attempt) if guest else EXEC_UNKNOWN
        return EXEC_STOPPED  # build stages: the builder is namespaced and harmless

    return probe


@contextmanager
def open_lab(
    ref: str,
    *,
    mutate: bool,
    registry: Registry | None = None,
    uid: int | None = None,
    probe: Callable[[LabPaths, LabRecord], Callable[[Attempt], str]] = make_probe,
    lock_timeout: float = 0.0,
    include_destroyed: bool = False,
) -> Iterator[LabContext]:
    """Hold the lab lock for the whole command. With `mutate=True`, an unresolved interrupted
    attempt (or a still-running one) refuses the command; collect/status pass `mutate=False`."""
    reg = registry or Registry()
    entry = reg.find(
        ref, os.getuid() if uid is None else uid, include_destroyed=include_destroyed
    )
    paths = lab_paths(entry.lab_uuid, reg.root)
    with exclusive_lock(paths.lock, timeout=lock_timeout):
        store = LabStore(paths)
        record = store.load()
        log = AttemptLog(paths.attempts)
        log.reconcile(probe(paths, record))
        if mutate:
            reason = log.blocked_reason()
            if reason:
                raise InputError(
                    reason, lab_id=entry.lab_uuid, label=entry.label,
                    evidence=(str(paths.attempts),),
                )
        yield LabContext(entry, paths, store, record, log)