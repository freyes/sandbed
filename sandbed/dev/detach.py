"""Run a command body in a detached worker process (`--detach`).

The worker forks, starts its own session, and runs the same body the foreground command would.
The parent waits only for a start handshake: either "run created, lab lock held" or the error that
prevented that (busy lab, bad input, host not ready), so a refusal still fails synchronously with
its normal exit code. After the handshake the worker's stderr goes to `<run>/detached.log` and its
terminal outcome is written to `<run>/result.json`, which `sandbed dev status` reads.
"""

from __future__ import annotations

import json
import os
import select
from collections.abc import Callable
from pathlib import Path

from .errors import (
    BuildError,
    DeploymentError,
    DevError,
    HostError,
    InputError,
    InternalError,
    Interrupted,
    ProvisioningError,
    ScenarioError,
)
from .results import envelope, error_envelope
from .store import atomic_write_json

LOG_NAME = "detached.log"
START_TIMEOUT = 300.0  # opening a lab may reconcile guest attempts over ssh

# `started(ids, run_dir)` is called by the body once the run exists and the lab lock is held.
Started = Callable[[dict, Path], None]
Body = Callable[[Started], dict]

_BY_CATEGORY: dict[str, type[DevError]] = {
    cls.category.value: cls
    for cls in (InputError, HostError, ProvisioningError, BuildError, DeploymentError,
                ScenarioError, InternalError)
}


def _encode(err: DevError) -> dict:
    return {"category": err.category.value, "status": err.status, "message": str(err),
            "stage": err.stage, "evidence": list(err.evidence), "context": err.context}


def _decode(data: dict) -> DevError:
    cls = Interrupted if data["status"] == "interrupted" else _BY_CATEGORY[data["category"]]
    return cls(data["message"], stage=data["stage"], evidence=tuple(data["evidence"]),
               **data["context"])


def _redirect(path: str, flags: int, *fds: int) -> None:
    new = os.open(path, flags, 0o600)
    for fd in fds:
        os.dup2(new, fd)
    os.close(new)


def _worker(command: str, body: Body, wfd: int) -> int:
    """Child side. Returns the process exit status; never raises."""
    handshake_open = True
    run_dir: Path | None = None
    ids: dict = {}

    def send(message: dict) -> None:
        nonlocal handshake_open
        if handshake_open:
            handshake_open = False
            os.write(wfd, (json.dumps(message) + "\n").encode())
            os.close(wfd)

    def started(started_ids: dict, started_run_dir: Path) -> None:
        nonlocal run_dir, ids
        run_dir, ids = started_run_dir, started_ids
        log = run_dir / LOG_NAME
        _redirect(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 1, 2)
        send({"started": True, "ids": ids, "log_path": str(log),
              "result_path": str(run_dir / "result.json"), "pid": os.getpid()})

    try:
        os.setsid()
        _redirect(os.devnull, os.O_RDONLY, 0)
        _redirect(os.devnull, os.O_WRONLY, 1, 2)
        fields = body(started)
        fields.pop("human", None)
        status = fields.pop("status", "succeeded")
        fields.pop("exit_code", None)
        outcome = envelope(command, status, **fields)
        code = 0
    except BaseException as exc:  # a worker has no caller: every outcome must be recorded
        err: DevError
        if isinstance(exc, DevError):
            err = exc
        elif isinstance(exc, KeyboardInterrupt):
            err = Interrupted("interrupted")
        else:
            err = InternalError(f"{type(exc).__name__}: {exc}")
        err.context.update(ids)
        send({"started": False, "error": _encode(err)})
        outcome, code = error_envelope(command, err), err.exit_code
    if run_dir is not None:
        try:
            atomic_write_json(run_dir / "result.json", outcome)
        except OSError:
            code = code or InternalError("").exit_code
    return code


def start(command: str, body: Body) -> dict:
    """Fork the worker, wait for its start handshake, return the extra envelope fields
    (`status: running`). Raises the worker's DevError when it could not start."""
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: leave only through os._exit, never back into the caller's stack
        code = 1
        try:
            os.close(rfd)
            code = _worker(command, body, wfd)
        finally:
            os._exit(code)
    os.close(wfd)
    try:
        ready, _, _ = select.select([rfd], [], [], START_TIMEOUT)
        raw = os.read(rfd, 1 << 16) if ready else b""
    finally:
        os.close(rfd)
    if not raw:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        why = "exited" if reaped else f"did not report within {START_TIMEOUT:.0f}s"
        raise InternalError(f"detached {command} worker (pid {pid}) {why} before starting")
    message = json.loads(raw.decode().splitlines()[0])
    if not message["started"]:
        os.waitpid(pid, 0)  # the worker is exiting; collect it
        raise _decode(message["error"])
    return {**message["ids"], "status": "running", "detached": True, "pid": message["pid"],
            "log_path": message["log_path"], "result_path": message["result_path"],
            "human": f"{command} started in the background (run {message['ids']['run_id']}, "
                     f"pid {message['pid']}); poll `sandbed dev status`"}
