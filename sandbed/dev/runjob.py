"""Launch a dev command as a transient systemd user unit; wait for it unless detached."""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import host, jobs, systemd
from .errors import HostError, InputError, InternalError
from .results import Outcome, error_envelope

sleep = time.sleep
TAIL_POLL_SEC = 0.2
RESULT_POLL_SEC = 0.5
DETACH_TIMEOUT_SEC = 60
STOP_TIMEOUT_SEC = 90
SUDO_COMMANDS = ("create", "host prepare")
SYSTEMD_REMEDIATION = (
    "enable a user manager: sudo loginctl enable-linger $USER, then export "
    f"XDG_RUNTIME_DIR=/run/user/$(id -u) (needs systemd >= {systemd.MIN_SYSTEMD})"
)


@dataclass(frozen=True)
class Launched:
    fields: dict


def _gate(command: str) -> None:
    checks = [host.check_systemd_user()]
    if command in SUDO_COMMANDS:
        checks.append(host.check_sudo())
    for check in checks:
        if not check.ok:
            raise HostError(
                f"{check.name} check failed: {check.detail}",
                stage="launch", remediation=check.remediation,
            )


class _Tail(threading.Thread):
    """Copy a growing log file to stderr (never stdout), from offset 0."""

    def __init__(self, path: Path) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.stopping = threading.Event()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.offset = 0

    def _copy(self) -> None:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                while chunk := fh.read(65536):
                    self.offset += len(chunk)
                    self._emit(self.decoder.decode(chunk))
        except FileNotFoundError:
            return

    @staticmethod
    def _emit(text: str) -> None:
        if not text:
            return
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except OSError:
            pass  # a closed stderr must not turn a finished job into a failure

    def run(self) -> None:
        while not self.stopping.is_set():
            self._copy()
            self.stopping.wait(TAIL_POLL_SEC)

    def finish(self) -> None:
        self.stopping.set()
        self.join()
        self._copy()
        self._emit(self.decoder.decode(b"", final=True))


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def _wait_client(proc: subprocess.Popen, job: jobs.Job, env: dict) -> str:
    """Wait for the `systemd-run --wait` client; Ctrl+C or SIGTERM stops the unit, not the lab."""
    on_main = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, _raise_interrupt) if on_main else None
    try:
        try:
            return proc.communicate()[1] or ""
        except KeyboardInterrupt:
            try:
                try:
                    systemd.stop(job.unit, STOP_TIMEOUT_SEC, env=env)
                except HostError as exc:
                    proc.kill()
                    return f"{proc.communicate()[1] or ''} (could not stop the unit: {exc})"
                return proc.communicate()[1] or ""
            except KeyboardInterrupt:
                return ""
    finally:
        if on_main:
            signal.signal(signal.SIGTERM, previous)


def _recorded(job: jobs.Job) -> Outcome:
    result = jobs.read_result(job)
    if result is None:
        raise InternalError(f"result.json vanished (job {job.dir})", stage="launch")
    return Outcome(result["envelope"], result["human"], result["exit_code"])


def _publish_failure(job: jobs.Job, err: HostError | InternalError, code: int) -> bool:
    return jobs.write_result(job, error_envelope(job.command, err), "", code)


def _detach(job: jobs.Job, proc: subprocess.Popen, env: dict) -> Launched:
    try:
        err = proc.communicate(timeout=DETACH_TIMEOUT_SEC)[1] or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        err = (proc.communicate()[1] or "") + " (systemd-run did not return in time)"
    if proc.returncode != 0:
        failure = HostError(
            f"systemd-run failed to start unit {job.unit}: {err.strip()}",
            stage="launch", remediation=SYSTEMD_REMEDIATION,
        )
        _publish_failure(job, failure, 3)
        raise failure
    jobs.mark_launched(job)
    return Launched({
        "status": "running",
        "job_id": job.job_id,
        "unit": job.unit,
        "job_path": str(job.dir),
        "stdout_path": str(job.paths.stdout),
        "stderr_path": str(job.paths.stderr),
        "linger": systemd.linger(os.getuid(), env),
        "next": f"sandbed dev job wait --job {job.job_id} --json",
        "human": f"job {job.job_id} started; follow with: "
        f"sandbed dev job wait --job {job.job_id} --json",
    })


def _await_result(job: jobs.Job, env: dict) -> dict | None:
    """Poll while the unit is still active (the client can exit before ExecStopPost is done).

    RuntimeMaxSec and TimeoutStopSec bound the unit, so there is no cap here: a result is never
    synthesized while the unit is active."""
    result = jobs.read_result(job)
    while result is None and systemd.unit_active(systemd.show(job.unit, env=env)):
        sleep(RESULT_POLL_SEC)
        result = jobs.read_result(job)
    return result


def _block(job: jobs.Job, proc: subprocess.Popen, env: dict) -> Outcome:
    tails = [_Tail(job.paths.stdout), _Tail(job.paths.stderr)]
    for tail in tails:
        tail.start()
    try:
        client_err = _wait_client(proc, job, env)
    finally:
        for tail in tails:
            tail.finish()
    if _await_result(job, env) is None:
        if not job.paths.started.exists():
            failure = HostError(
                f"worker for job {job.job_id} never started: {client_err.strip() or 'no output'}",
                stage="launch", remediation=SYSTEMD_REMEDIATION,
            )
            if not _publish_failure(job, failure, 3):
                return _recorded(job)
            raise failure
        detail = f": {client_err.strip()}" if client_err.strip() else ""
        _publish_failure(job, InternalError(
            f"worker ended without a result (unit {job.unit}, job {job.dir}){detail}",
            stage="launch",
        ), 6)
    return _recorded(job)


def launch(command: str, params: dict, lab_ref: str, *, detach: bool) -> Launched | Outcome:
    _gate(command)
    bus = systemd.bus_env(os.environ, os.getuid())
    active = jobs.find_active(lab_ref, lambda unit: systemd.show(unit, env=bus))
    if active is not None:
        raise InputError(
            f"job {active.job_id} ({active.command}) is already running for lab {lab_ref}; "
            f"wait with `sandbed dev job wait --job {active.job_id} --json`",
            job_id=active.job_id,
        )
    systemd.check_launchable(sys.executable, jobs.jobs_root())
    job = jobs.create_job(command, params, lab_ref, os.getcwd(), detach)
    try:
        jobs.write_env_file(job, jobs.worker_env(os.environ))
        env = systemd.bus_env(os.environ, os.getuid())
        argv = systemd.unit_argv(
            job, wait=not detach, python=sys.executable,
            runtime_max=jobs.runtime_max_seconds(command),
        )
        proc = systemd.start(argv, wait=not detach, env=env)
    except Exception as exc:
        _publish_failure(job, HostError(
            f"could not start unit {job.unit}: {exc}", stage="launch",
            remediation=SYSTEMD_REMEDIATION,
        ), 3)
        raise
    if detach:
        return _detach(job, proc, env)
    return _block(job, proc, env)
