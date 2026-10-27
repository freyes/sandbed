"""`systemd-run --user` command builder, launch checks and unit control.

Every subprocess call goes through the two seams `_popen` and `_run` so tests can patch them.
There is deliberately no escaping logic: `check_launchable` refuses paths systemd would parse
differently across versions.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .errors import HostError

MIN_SYSTEMD = 240
ACTIVE_STATES = frozenset({"active", "activating", "deactivating", "reloading"})
SHOW_PROPERTIES = ("ActiveState", "SubState", "Result", "ExecMainStatus", "LoadState")
_HOSTILE = re.compile(r"[\s\"'\\$%;]")
_VERSION = re.compile(r"systemd\s+(\d+)")


class JobLike(Protocol):
    """What `unit_argv` reads from a job; `sandbed.dev.jobs.Job` satisfies it structurally."""

    @property
    def job_id(self) -> str: ...
    @property
    def command(self) -> str: ...
    @property
    def cwd(self) -> str | Path: ...
    @property
    def dir(self) -> str | Path: ...


def _popen(argv: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(argv, **kwargs)


def _run(
    argv: list[str], *, timeout: float, env: Mapping[str, str] | None = None
) -> tuple[int, str, str]:
    """(returncode, stdout, stderr); 124 on timeout, 127 if the program is missing."""
    try:
        done = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, env=None if env is None else dict(env),
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def parse_version(text: str) -> int | None:
    """`systemd 249 (249.11-0ubuntu3.12)` -> 249."""
    found = _VERSION.search(text)
    return int(found.group(1)) if found else None


def check_launchable(python: str, jobdir: str | Path) -> None:
    for label, value in (("python interpreter path", str(python)), ("job directory", str(jobdir))):
        if _HOSTILE.search(value):
            raise HostError(
                f"cannot launch a systemd unit: {label} {value!r} contains whitespace or one of "
                "\" ' \\ $ % ;",
                stage="launch",
                remediation="use a path without whitespace or those characters "
                "(for the job directory, set XDG_STATE_HOME to such a path)",
            )


def unit_argv(
    job: JobLike,
    wait: bool,
    python: str,
    exec_argv: list[str] | None = None,
    runtime_max: int | None = None,
) -> list[str]:
    jobdir = str(job.dir)
    worker = [python, "-m", "sandbed.dev.worker", "--job-id", job.job_id]
    finalize = f"{python} -m sandbed.dev.worker --finalize --job-id {job.job_id}"
    return [
        "systemd-run", "--user", "--quiet", "--collect",
        *(["--wait"] if wait else []),
        f"--unit=sandbed-dev-{job.job_id}",
        f"--description=sandbed dev {job.command} job {job.job_id}",
        "--service-type=exec",
        f"--working-directory={job.cwd}",
        "-p", "KillMode=mixed",
        "-p", "TimeoutStopSec=60s",
        *(["-p", f"RuntimeMaxSec={runtime_max}"] if runtime_max is not None else []),
        "-p", f"EnvironmentFile={jobdir}/env",
        "-p", f"StandardOutput=append:{jobdir}/stdout.log",
        "-p", f"StandardError=append:{jobdir}/stderr.log",
        "-p", f"ExecStopPost={finalize}",
        "--", *(exec_argv if exec_argv is not None else worker),
    ]


def bus_env(environ: Mapping[str, str], uid: int) -> dict[str, str]:
    """Full environment for systemd client calls, defaulting the user-bus variables."""
    merged = dict(environ)
    runtime = Path(f"/run/user/{uid}")
    if runtime.is_dir():
        merged.setdefault("XDG_RUNTIME_DIR", str(runtime))
        merged.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return merged


def start(argv: list[str], *, wait: bool, env: Mapping[str, str] | None = None) -> subprocess.Popen:
    """Start `systemd-run`. stdout is discarded (the unit logs to files); stderr is captured.

    `wait` is the caller's mode (the argv already carries `--wait` or not); it only decides
    whether the client is expected to outlive this call.
    """
    del wait
    return _popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, env=None if env is None else dict(env),
    )


def show(unit: str, env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Unit properties. A collected unit is `LoadState=not-found` (exit 0); `None` only when the
    manager cannot be reached."""
    argv = ["systemctl", "--user", "show"]
    for prop in SHOW_PROPERTIES:
        argv += ["-p", prop]
    code, out, _ = _run([*argv, unit], timeout=30, env=env)
    if code != 0:
        return None
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def unit_active(props: Mapping[str, str] | None) -> bool:
    return props is not None and props.get("ActiveState") in ACTIVE_STATES


def stop(unit: str, timeout: float, env: Mapping[str, str] | None = None) -> None:
    """Stop the unit; an already-gone unit is not an error."""
    code, _, err = _run(["systemctl", "--user", "stop", unit], timeout=timeout, env=env)
    if code != 0 and "not loaded" not in err.lower():
        raise HostError(f"systemctl --user stop {unit} failed: {err.strip()}", stage="stop")


def linger(uid: int, env: Mapping[str, str] | None = None) -> bool | None:
    """Whether the user's services survive logout (`Linger=yes`); None when it cannot be read."""
    code, out, _ = _run(["loginctl", "show-user", str(uid), "-p", "Linger"], timeout=30, env=env)
    if code != 0:
        return None
    value = out.strip().partition("=")[2]
    return {"yes": True, "no": False}.get(value)
