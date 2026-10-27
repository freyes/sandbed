"""Run one named stage: record the attempt, translate failures into the right layer, and run
commands inside the guest through the stage wrapper so their fate can be queried later."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import DevError, InternalError, Interrupted
from .runner import CommandFailed, CommandResult, run_logged
from .ssh import SshTarget
from .state import (
    EXEC_RUNNING,
    EXEC_STOPPED,
    EXEC_UNKNOWN,
    FAILED,
    INTERRUPTED,
    SUCCEEDED,
    Attempt,
    AttemptLog,
)

# Relative to the VM user's home. Deliberately not a dot-directory: the snap's `home` plug cannot
# read or write hidden paths, and `sunbeam` must read the manifest and write the openrc here.
GUEST_DIR = "sandbed"


@contextmanager
def stage(
    log: AttemptLog,
    run_id: str,
    name: str,
    *,
    layer: type[DevError],
    fingerprint: str = "",
) -> Iterator[Attempt]:
    """`layer` is the error class a failed command in this stage is reported as."""
    attempt = log.begin(run_id, name, fingerprint)
    try:
        yield attempt
    except Interrupted as err:
        err.stage = err.stage or name
        log.finish(attempt, INTERRUPTED, evidence=err.evidence)
        raise
    except CommandFailed as err:
        log.finish(attempt, FAILED, exit_code=err.result.returncode, evidence=err.evidence)
        raise layer(str(err), stage=name, evidence=err.evidence) from err
    except DevError as err:
        err.stage = err.stage or name
        log.finish(attempt, FAILED, evidence=err.evidence)
        raise
    except Exception as err:
        log.finish(attempt, FAILED)
        raise InternalError(f"{name}: {err}", stage=name) from err
    else:
        log.finish(attempt, SUCCEEDED, exit_code=0)


def guest_stage_dir(home: str, attempt: Attempt) -> str:
    return f"{home}/{GUEST_DIR}/stages/{attempt.run_id}/{attempt.stage}-{attempt.attempt}"


def run_guest(
    target: SshTarget,
    attempt: Attempt,
    argv: list[str],
    *,
    home: str,
    timeout: float,
    log_path: Path,
    check: bool = True,
    tail_bytes: int = 4096,
) -> CommandResult:
    """Run `argv` in the guest under run-stage.sh with a guest-side deadline; the controller's
    own deadline is slightly later so the guest normally reports first."""
    remote = [
        "bash", f"{home}/{GUEST_DIR}/run-stage.sh", guest_stage_dir(home, attempt),
        str(int(timeout)), "--", *argv,
    ]
    return run_logged(
        target.ssh_argv(remote), log_path=log_path, timeout=timeout + 30, check=check,
        tail_bytes=tail_bytes,
    )


def guest_probe(target: SshTarget, home: str, log_path: Path, quick: float = 20.0):
    """Build the callable `AttemptLog.reconcile` uses to ask the guest what became of an
    attempt: rc file present -> stopped; pid alive -> running; anything else -> unknown."""

    def probe(attempt: Attempt) -> str:
        sdir = guest_stage_dir(home, attempt)

        def ssh(*remote: str) -> CommandResult:
            return run_logged(
                target.ssh_argv(list(remote)),
                log_path=log_path, timeout=quick, stream=_Devnull(),
            )

        try:
            if ssh("test", "-f", f"{sdir}/rc").ok:
                return EXEC_STOPPED
            pid = ssh("cat", f"{sdir}/pid")
            if pid.ok and pid.tail.strip().isdigit():
                if ssh("kill", "-0", pid.tail.strip()).ok:
                    return EXEC_RUNNING
        except DevError:
            pass
        return EXEC_UNKNOWN

    return probe


class _Devnull:
    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        return None
