"""Job inspection: status, logs, wait, cancel, list. Returns envelope-field dicts, no click.

Everything here is lock-free: it never calls `open_lab`, takes no lock and writes nothing. Lab
progress is read straight from `attempts.json` (written atomically) and the job's log files.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import jobs, systemd
from .errors import HostError, InputError
from .jobs import Job
from .labs import LabEntry, Registry
from .paths import lab_paths
from .state import RUNNING, Attempt, AttemptLog
from .store import read_json

DEFAULT_LOG_BYTES = 65536
MAX_LOG_BYTES = 1 << 20
DEFAULT_WAIT_SEC = 300
MAX_WAIT_SEC = 3600
POLL_SEC = 1.0
STOP_TIMEOUT_SEC = 90
CANCEL_WAIT_SEC = 70
DEFAULT_LIST_LIMIT = 20
STDERR_TAIL_BYTES = 4096
ATTEMPTS_TAIL = 5
STREAMS = ("stderr", "stdout")
ACTIVE_STATES = ("starting", "running")
TERMINAL_STATES = ("succeeded", "failed", "interrupted", "lost")

# Module-level so tests can patch them (fake clock, no real sleeping).
sleep = time.sleep
monotonic = time.monotonic


def _env() -> dict[str, str]:
    return systemd.bus_env(os.environ, os.getuid())


def _no_manager(job: Job) -> HostError:
    return HostError(
        f"cannot reach the systemd user manager to inspect job {job.job_id}, and it has no "
        "recorded result",
        stage="job",
        remediation="start a user session or run `loginctl enable-linger $USER`, then check "
        "`systemctl --user status`; `sandbed dev host check` reports the systemd-user check",
    )


def _state(job: Job, props: dict[str, str] | None, result: dict | None = jobs._UNSET) -> str:
    state = jobs.derive_state(job, props, result)
    if state == "unknown":
        raise _no_manager(job)
    return state


def _find_lab(ref: str) -> LabEntry | None:
    try:
        return Registry().find(ref, os.getuid(), include_destroyed=True)
    except InputError:
        return None


def _resolve_lab(job: Job, result: dict | None) -> LabEntry | None:
    """The lab this job ran against: job.lab_id, else the result's lab_id, else (only when a job
    has neither) the lab_ref. Labels can be reused after destroy, hence the order."""
    envelope = (result or {}).get("envelope") or {}
    ref = job.lab_id or envelope.get("lab_id") or job.lab_ref
    return _find_lab(ref)


def _parse(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp) if stamp else None


def _elapsed(created: str, started: str | None, finished: str | None) -> float:
    begin = _parse(started or created)
    end = _parse(finished) or datetime.now(timezone.utc)
    return round(max((end - begin).total_seconds(), 0.0), 1)


def _attempt_view(a: Attempt) -> dict:
    return {
        "stage": a.stage, "attempt": a.attempt, "state": a.state,
        "started_at": a.started_at, "finished_at": a.finished_at, "exit_code": a.exit_code,
    }


def _stderr_view(path: Path) -> dict:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"path": str(path), "size": 0, "mtime": None, "tail": ""}
    data, _, _, _ = jobs.read_log(path, max(stat.st_size - STDERR_TAIL_BYTES, 0), STDERR_TAIL_BYTES)
    mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")
    return {
        "path": str(path), "size": stat.st_size, "mtime": mtime,
        "tail": data.decode("utf-8", errors="replace"),
    }


def _progress(job: Job, state: str, entry: LabEntry | None) -> dict:
    current, tail = None, []
    if state == "running" and entry is not None:
        attempts = AttemptLog(lab_paths(entry.lab_uuid).attempts).attempts()
        running = [a for a in attempts if a.state == RUNNING]
        current = running[-1].stage if running else None
        tail = [_attempt_view(a) for a in attempts[-ATTEMPTS_TAIL:]]
    return {
        "current_stage": current, "attempts_tail": tail,
        "stderr": _stderr_view(job.paths.stderr),
    }


def _systemd_view(props: dict[str, str] | None) -> dict | None:
    if props is None:
        return None
    return {k: props.get(k) for k in ("ActiveState", "SubState", "Result", "ExecMainStatus")}


def _status_fields(
    job: Job, props: dict[str, str] | None, state: str, result: dict | None
) -> dict:
    envelope = (result or {}).get("envelope")
    exit_info = jobs.read_exit(job)
    started = read_json(job.paths.started)
    started_at = started.get("at") if isinstance(started, dict) else None
    finished_at = result.get("finished_at") if result else None
    entry = _resolve_lab(job, result)
    lab_id = job.lab_id or (envelope or {}).get("lab_id") or (entry.lab_uuid if entry else None)
    label = (envelope or {}).get("label") or (entry.label if entry else None)
    run_id = (envelope or {}).get("run_id")
    body = {
        "job_id": job.job_id, "command": job.command, "params": job.params,
        "lab_ref": job.lab_ref, "lab_id": lab_id, "label": label, "run_id": run_id,
        "state": state, "unit": job.unit, "created_at": job.created_at,
        "started_at": started_at, "finished_at": finished_at,
        "elapsed_seconds": _elapsed(job.created_at, started_at, finished_at),
        "exit_code": result.get("exit_code") if result else None,
        "service_result": exit_info.get("service_result") if exit_info else None,
        "systemd": _systemd_view(props),
        "progress": _progress(job, state, entry),
        "result": envelope,
    }
    return {
        "job": body, "lab_id": lab_id, "label": label, "run_id": run_id,
        "human": f"job {job.job_id} ({job.command}): {state}",
    }


def _snapshot(job: Job) -> dict:
    props = systemd.show(job.unit, env=_env())
    result = jobs.read_result(job)
    return _status_fields(job, props, _state(job, props, result), result)


def job_status(job_id: str) -> dict:
    return _snapshot(jobs.load_job(job_id))


def job_logs(
    job_id: str, stream: str = "stderr", offset: int = 0, max_bytes: int | None = None
) -> dict:
    if stream not in STREAMS:
        raise InputError(f"unknown stream: {stream} (expected stderr or stdout)")
    job = jobs.load_job(job_id)
    limit = min(max_bytes if max_bytes is not None else DEFAULT_LOG_BYTES, MAX_LOG_BYTES)
    if limit < 1:
        raise InputError("max_bytes must be positive")
    path = job.paths.stderr if stream == "stderr" else job.paths.stdout
    data, next_offset, size, eof = jobs.read_log(path, offset, limit)
    finished = jobs.read_result(job) is not None
    return {
        "job_id": job.job_id, "stream": stream, "offset": max(offset, 0),
        "next_offset": next_offset, "size": size, "eof": eof, "finished": finished,
        "data": data.decode("utf-8", errors="replace"),
        "human": f"{job.job_id} {stream}: {len(data)} bytes from offset {max(offset, 0)}",
    }


def job_wait(job_id: str, timeout: int | None = None) -> dict:
    job = jobs.load_job(job_id)
    budget = min(DEFAULT_WAIT_SEC if timeout is None else max(timeout, 0), MAX_WAIT_SEC)
    deadline = monotonic() + budget
    while True:
        fields = _snapshot(job)
        if fields["job"]["state"] in TERMINAL_STATES:
            return {**fields, "timed_out": False}
        if monotonic() >= deadline:
            return {**fields, "timed_out": True}
        sleep(POLL_SEC)


def job_cancel(job_id: str) -> dict:
    job = jobs.load_job(job_id)
    fields = _snapshot(job)
    if fields["job"]["state"] in TERMINAL_STATES:
        return {**fields, "cancelled": False, "reason": "already finished"}
    systemd.stop(job.unit, STOP_TIMEOUT_SEC, env=_env())
    deadline = monotonic() + CANCEL_WAIT_SEC
    while True:
        fields = _snapshot(job)
        if fields["job"]["state"] in TERMINAL_STATES or monotonic() >= deadline:
            return {**fields, "cancelled": True}
        sleep(POLL_SEC)


def _list_state(job: Job) -> str:
    if jobs.read_result(job) is not None:
        return jobs.derive_state(job, None)
    return _state(job, systemd.show(job.unit, env=_env()))


def _matches_lab(job: Job, lab: str) -> bool:
    if job.lab_ref == lab:
        return True
    entry = _resolve_lab(job, jobs.read_result(job))
    return entry is not None and lab in (entry.label, entry.lab_uuid)


def job_list(active: bool = False, lab: str | None = None, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    if limit < 1:
        raise InputError("limit must be positive")
    entries = []
    for job in sorted(jobs.list_jobs(), key=lambda j: (j.created_at, j.job_id), reverse=True):
        if lab is not None and not _matches_lab(job, lab):
            continue
        state = _list_state(job)
        if active and state not in ACTIVE_STATES:
            continue
        entries.append({
            "job_id": job.job_id, "command": job.command, "lab_ref": job.lab_ref,
            "state": state, "created_at": job.created_at, "unit": job.unit,
        })
        if len(entries) >= limit:
            break
    return {"jobs": entries, "human": f"{len(entries)} job(s)"}
