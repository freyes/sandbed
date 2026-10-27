"""Detached-job store: one private directory per job under `<state_root>/jobs/<job_id>/`.

`job.json` is written once and never rewritten. Everything that changes later lives in its own
single-writer file (`launched.json`, `started.json`, `result.json`, `exit.json`), so no field is
ever read-modify-written by two processes. This module never talks to systemd: callers pass the
unit properties they obtained to `derive_state`.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from .errors import InputError
from .labs import Registry
from .paths import state_root
from .state import now
from .store import atomic_write_json, ensure_private_dir, read_json, write_private

JOB_ID_RE = re.compile(r"[0-9a-f]{12}")
SCHEMA = 1
HOST_LAB_REF = "@host"  # not a valid lab label, so it cannot collide with a real lab
STARTING_GRACE_SEC = 20

ENV_NAMES = frozenset((
    "PATH HOME USER LOGNAME LANG LC_ALL TZ XDG_STATE_HOME XDG_CONFIG_HOME XDG_DATA_HOME "
    "XDG_CACHE_HOME http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY "
    "SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE"
).split())
ENV_PREFIXES = ("SANDBED_", "TF_", "LXD_", "SNAPCRAFT_")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ACTIVE_STATES = frozenset(("active", "activating", "deactivating", "reloading"))
_RESULT_STATES = frozenset(("succeeded", "failed", "interrupted"))


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def valid_job_id(job_id: object) -> bool:
    return isinstance(job_id, str) and JOB_ID_RE.fullmatch(job_id) is not None


def unit_name(job_id: str) -> str:
    return f"sandbed-dev-{job_id}"


def jobs_root() -> Path:
    return state_root() / "jobs"


@dataclass(frozen=True)
class JobPaths:
    job_id: str

    @property
    def root(self) -> Path:
        return jobs_root() / self.job_id

    @property
    def job(self) -> Path:
        return self.root / "job.json"

    @property
    def launched(self) -> Path:
        return self.root / "launched.json"

    @property
    def started(self) -> Path:
        return self.root / "started.json"

    @property
    def env(self) -> Path:
        return self.root / "env"

    @property
    def result(self) -> Path:
        return self.root / "result.json"

    @property
    def exit(self) -> Path:
        return self.root / "exit.json"

    @property
    def stdout(self) -> Path:
        return self.root / "stdout.log"

    @property
    def stderr(self) -> Path:
        return self.root / "stderr.log"


@dataclass(frozen=True)
class Job:
    job_id: str
    command: str
    params: dict
    lab_ref: str
    lab_id: str | None
    cwd: str
    unit: str
    detach: bool
    created_at: str
    sandbed_version: str

    @property
    def paths(self) -> JobPaths:
        return JobPaths(self.job_id)

    @property
    def dir(self) -> Path:
        return self.paths.root


def _sandbed_version() -> str:
    try:
        return metadata.version("sandbed")
    except metadata.PackageNotFoundError:
        return "unknown"


def _resolve_lab_id(lab_ref: str) -> str | None:
    try:
        return Registry().find(lab_ref, os.getuid(), include_destroyed=True).lab_uuid
    except InputError:
        return None


def create_job(command: str, params: dict, lab_ref: str, cwd: str, detach: bool) -> Job:
    """Create the job directory, `job.json` (once) and the empty private log files."""
    job_id = new_job_id()
    while JobPaths(job_id).root.exists():
        job_id = new_job_id()
    job = Job(job_id, command, params, lab_ref, _resolve_lab_id(lab_ref), cwd,
              unit_name(job_id), detach, now(), _sandbed_version())
    paths = job.paths
    ensure_private_dir(paths.root)
    atomic_write_json(paths.job, {
        "schema": SCHEMA, "job_id": job.job_id, "command": job.command, "params": job.params,
        "lab_ref": job.lab_ref, "lab_id": job.lab_id, "cwd": job.cwd, "unit": job.unit,
        "detach": job.detach, "created_at": job.created_at,
        "sandbed_version": job.sandbed_version,
    })
    write_private(paths.stdout, b"")
    write_private(paths.stderr, b"")
    return job


def load_job(job_id: str) -> Job:
    if not valid_job_id(job_id):
        raise InputError(f"no such job: {job_id}")
    path = JobPaths(job_id).job
    if not path.exists():
        raise InputError(f"no such job: {job_id}")
    try:
        data = read_json(path)
        if not isinstance(data, dict):
            raise TypeError("job.json is not an object")
        return Job(
            data["job_id"], data["command"], data["params"], data["lab_ref"], data["lab_id"],
            data["cwd"], data["unit"], data["detach"], data["created_at"],
            data["sandbed_version"],
        )
    except (OSError, ValueError, TypeError, KeyError):
        raise InputError(f"job {job_id} has a malformed job.json") from None


def list_jobs() -> list[Job]:
    """All readable jobs, oldest first. Unreadable directories are skipped, never fatal."""
    root = jobs_root()
    if not root.is_dir():
        return []
    jobs = []
    for entry in root.iterdir():
        try:
            jobs.append(load_job(entry.name))
        except InputError:
            continue
    return sorted(jobs, key=lambda j: (j.created_at, j.job_id))


def mark_launched(job: Job) -> None:
    atomic_write_json(job.paths.launched, {"at": now()})


def mark_started(job: Job, pid: int) -> None:
    atomic_write_json(job.paths.started, {"at": now(), "pid": pid})


def write_result(job: Job, envelope: dict, human: str, exit_code: int) -> bool:
    """Publish `result.json` exactly once: the first writer wins, a later one is a no-op.
    Returns whether this call published it."""
    paths = job.paths
    payload = {
        "envelope": {**envelope, "job_id": job.job_id}, "human": human,
        "exit_code": exit_code, "finished_at": now(),
    }
    tmp = paths.root / f".result.{uuid.uuid4().hex}.tmp"
    atomic_write_json(tmp, payload, sort_keys=False)
    try:
        os.link(tmp, paths.result)  # atomic and never replaces an existing file
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)
    return True


def read_result(job: Job) -> dict | None:
    data = read_json(job.paths.result)
    return data if isinstance(data, dict) else None


def read_exit(job: Job) -> dict | None:
    data = read_json(job.paths.exit)
    return data if isinstance(data, dict) else None


def _unit_active(props: Mapping[str, str]) -> bool:
    return props.get("LoadState") != "not-found" and props.get("ActiveState") in _ACTIVE_STATES


def _is_starting(job: Job) -> bool:
    if job.paths.launched.exists():
        return False
    created = datetime.fromisoformat(job.created_at)
    return (datetime.now(timezone.utc) - created).total_seconds() < STARTING_GRACE_SEC


_UNSET: Any = object()


def derive_state(
    job: Job, unit_props: Mapping[str, str] | None, result: dict | None = _UNSET
) -> str:
    """starting | running | succeeded | failed | interrupted | lost | unknown.

    `unit_props` is the unit's systemd properties, or None when the manager is unreachable
    (the only case that yields `unknown`). A collected unit (`LoadState=not-found`) is inactive."""
    if result is _UNSET:
        result = read_result(job)
    if result is not None:
        status = (result.get("envelope") or {}).get("status")
        return status if status in _RESULT_STATES else "failed"
    if unit_props is not None and _unit_active(unit_props):
        return "running"
    if _is_starting(job):
        return "starting"
    return "unknown" if unit_props is None else "lost"


def _same_lab(job: Job, lab_ref: str, lab_id: str | None) -> bool:
    if HOST_LAB_REF in (lab_ref, job.lab_ref):
        return job.lab_ref == lab_ref
    if job.lab_ref == lab_ref:
        return True
    return lab_id is not None and lab_id in (job.lab_id, _resolve_lab_id(job.lab_ref))


def find_active(lab_ref: str, show: Callable[[str], dict | None]) -> Job | None:
    """The newest job of this lab (or of the host, for `@host`) that has not finished.

    `show(unit)` returns the unit's systemd properties, or None when the manager is unreachable;
    it is a parameter so this module never touches systemd. Jobs with a result are skipped
    without calling it. When the manager cannot be queried, a launched job without a result
    counts as active (nothing proves otherwise). Takes no lock."""
    lab_id = None if lab_ref == HOST_LAB_REF else _resolve_lab_id(lab_ref)
    for job in reversed(list_jobs()):
        if not _same_lab(job, lab_ref, lab_id) or read_result(job) is not None:
            continue
        props = show(job.unit)
        if props is None:
            if job.paths.launched.exists():
                return job
        elif derive_state(job, props) in ("starting", "running"):
            return job
    return None


def read_log(path: Path, offset: int, max_bytes: int) -> tuple[bytes, int, int, bool]:
    """Bytes from `offset` (at most `max_bytes`) as `(data, next_offset, size, eof)`. An offset
    beyond the end yields no data and `next_offset == size`."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return b"", 0, 0, True
    start = min(max(offset, 0), size)
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read(max_bytes)
    next_offset = start + len(data)
    return data, next_offset, size, next_offset >= size


def _env_value_ok(value: str) -> bool:
    if any(ch in value for ch in "\n\r\0"):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # surrogate escapes: the bytes were not valid UTF-8
        return False
    return True


def worker_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The allowlisted slice of `environ` the worker unit receives (nothing else is forwarded)."""
    return {
        k: v for k, v in environ.items()
        if (k in ENV_NAMES or k.startswith(ENV_PREFIXES)) and _env_value_ok(v)
    }


def write_env_file(job: Job, env: Mapping[str, str]) -> None:
    """systemd `EnvironmentFile`: `NAME="value"` lines, mode 0600. Entries that could not be
    represented on one line are skipped rather than escaped."""
    lines = []
    for name, value in env.items():
        if not _ENV_NAME_RE.fullmatch(name) or not _env_value_ok(value):
            continue
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{name}="{escaped}"\n')
    write_private(job.paths.env, "".join(lines))


# --- RuntimeMaxSec table (C-RUNTIME) -------------------------------------------------------
# One entry per deadline-bearing call: (name, seconds). `name` is the module-level constant's
# NAME when the call uses one, otherwise a literal label. Seconds are copied here (importing the
# modules would couple this one to the whole command stack); tests pin every constant by name
# and value. Each guest call through `run_guest` also gets GRACE (stages.py:82, timeout + 30).
GRACE = ("guest-grace", 30)

_COLLECT_RUN = [  # collect_run, used by `collect` and, after the scenario, by `test`
    ("terraform-version", 30), ("lxd-version", 30),  # diagnostics.py:30-31
    ("lxd-instance", 60), ("lxd-instance-config", 60),  # diagnostics.py:32-35
    ("cloud-init-status", 60), ("cloud-init-log", 60),  # diagnostics.py:36-38
    ("snap-list", 60), ("snap-connections", 60), ("snap-services", 60),  # diagnostics.py:39-41
    ("clusterd-journal", 120), ("sunbeam-cluster-list", 120), ("juju-status", 120),  # :42-45
    ("stage-logs", 60),  # diagnostics.py:46
    ("openstack-servers", 120), ("openstack-networks", 120),  # diagnostics.py:48-49
    ("instance-console", 120),  # diagnostics.py:52-53
    ("INSPECT_TIMEOUT", 300),  # diagnostics.py:77
    ("inspect-listing", 60),  # diagnostics.py:79-81
    ("COLLECT_TIMEOUT", 600),  # diagnostics.py:86
]

STAGE_DEADLINES: dict[str, list[tuple[str, int]]] = {
    "host prepare": [
        ("host-prepare-budget", 3 * 3600),  # prepare_host has no internal deadline
    ],
    "create": [
        ("taken-cidrs-ip-route", 30), ("taken-cidrs-lxc-network", 30),  # hostnet.py:24 via :35,42
        ("PROVISION_TIMEOUT", 1200),  # dev-network init: provision.py:137 via :53
        ("PROVISION_TIMEOUT", 1200),  # dev-network apply: provision.py:137 via :53
        ("PROVISION_TIMEOUT", 1200),  # dev-manual init: provision.py:149 via :53
        ("PROVISION_TIMEOUT", 1200),  # dev-manual apply: provision.py:149 via :53
        ("READY_TIMEOUT", 600),  # wait_ready ssh loop: provision.py:93-99
        ("wait-ready-last-probe", 30),  # a probe may start just before the deadline: :96
        ("wait-ready-cloud-init", 30),  # floor of max(deadline - now, 30): provision.py:104
        ("wait-ready-kvm", 30),  # provision.py:104
        ("wait-ready-sdb", 30),  # provision.py:104
        ("push-scripts-mkdir", 60),  # provision.py:112
        ("push-scripts-run-stage", 60),  # GUEST_SCRIPTS[0] scp: provision.py:116
        ("push-scripts-probe-guest", 60),  # GUEST_SCRIPTS[1] scp: provision.py:116
        ("push-scripts-os", 60),  # GUEST_SCRIPTS[2] scp: provision.py:116
    ],
    "build": [
        ("BUILD_TIMEOUT", 3600),  # snapcraft pack: build.py:63
    ],
    "deploy": [
        ("guest-mkdir", 60), GRACE,  # deploy.py:113
        ("TRANSFER_TIMEOUT", 600),  # snap scp: deploy.py:116 (first pass of the loop)
        ("TRANSFER_TIMEOUT", 600),  # manifest scp: deploy.py:116 (second pass)
        ("guest-sha", 300), GRACE,  # deploy.py:118
        ("INSTALL_TIMEOUT", 1800), GRACE,  # snap install: deploy.py:125
        ("snap-alias", 120), GRACE,  # deploy.py:126
        ("INSTALL_TIMEOUT", 1800), GRACE,  # prepare-node: deploy.py:130
        ("wait-juju", 600), GRACE,  # deploy.py:132
        ("connect-juju-bin", 120), GRACE,  # adapter.connect[0]: deploy.py:134
        ("connect-dot-local-share-juju", 120), GRACE,  # adapter.connect[1]: deploy.py:134
        ("connect-dot-config-openstack", 120), GRACE,  # adapter.connect[2]: deploy.py:134
        ("connect-dot-local-share-openstack", 120), GRACE,  # adapter.connect[3]: deploy.py:134
        ("verify-snap-list", 120), GRACE,  # deploy.py:59
        ("verify-installed-sha", 120), GRACE,  # deploy.py:68
        ("verify-connections", 120), GRACE,  # deploy.py:73
        ("verify-cli-probe", 120), GRACE,  # deploy.py:77
        ("verify-groups", 60), GRACE,  # deploy.py:78
    ],
    "test": [
        ("precondition-kvm", 60), GRACE,  # scenarios.py:89
        ("BOOTSTRAP_TIMEOUT", 3600), GRACE,  # scenarios.py:96
        ("cluster-list", 300), GRACE,  # scenarios.py:97
        ("CONFIGURE_TIMEOUT", 1200), GRACE,  # scenarios.py:104
        ("project-list", 300), GRACE,  # openstack(): scenarios.py:65,105
        ("LAUNCH_TIMEOUT", 900), GRACE,  # scenarios.py:113
        ("server-show", 300), GRACE,  # scenarios.py:116
        ("image-show", 300), GRACE,  # scenarios.py:118
        ("LAUNCH_TIMEOUT", 900), ("guest-probe-extra", 60), GRACE,  # LAUNCH_TIMEOUT + 60: :129
        ("server-delete", 300), GRACE,  # scenarios.py:134
        ("fip-delete", 300), GRACE,  # scenarios.py:135
        ("server-gone", 300), GRACE,  # scenarios.py:136
        *_COLLECT_RUN,  # collect_run after success and failure: commands.py:151
    ],
    "collect": list(_COLLECT_RUN),  # commands.py:182
    "destroy": [
        ("GUEST_TEARDOWN_TIMEOUT", 1200),  # destroy.py:56
        ("PROVISION_TIMEOUT", 1200),  # terraform destroy dev-manual: destroy.py:71-75 via :53
        ("PROVISION_TIMEOUT", 1200),  # terraform destroy dev-network: destroy.py:71-75 via :53
        ("lxc-query-project", 30),  # destroy.py:27,41
        ("lxc-query-networks", 30),  # destroy.py:27,43
    ],
}


def _runtime_max(entries: list[tuple[str, int]]) -> int:
    return math.ceil(1.25 * sum(seconds for _, seconds in entries)) + 120


RUNTIME_MAX_SEC: dict[str, int] = {c: _runtime_max(e) for c, e in STAGE_DEADLINES.items()}


def runtime_max_seconds(command: str) -> int:
    try:
        return RUNTIME_MAX_SEC[command]
    except KeyError:
        raise InputError(f"no runtime limit for command: {command}") from None
