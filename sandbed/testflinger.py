from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import Config
from .console import console
from .manifest import render
from .shell import RetryableConnectionError, is_transient_connection_error, run

SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=/dev/null"]

JOB_ID_FILE = ".sandbed.testflinger.job_id"

ALIVE_STATES = {"waiting", "provision", "test", "reserve"}

POLL_INTERVAL = 15

TF_MAX_ATTEMPTS = 5

# A node can be reachable before its authorized_keys is populated (Permission denied).
SSH_WAIT_TIMEOUT = 300
SSH_WAIT_INTERVAL = 15

RSYNC_EXCLUDES = [".venv", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".terraform"]

JOB_NOT_FOUND_MSG = "No data found for that job id."


class JobNotFoundError(Exception):
    """Testflinger has no record of the job id."""


def _before_sleep_log(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    wait_s = retry_state.next_action.sleep if retry_state.next_action else 0
    detail = ""
    if isinstance(exc, RetryableConnectionError):
        detail = f": {exc.stderr.strip().splitlines()[0]}" if exc.stderr.strip() else ""
    else:
        detail = f" ({type(exc).__name__})" if exc else ""
    next_attempt = retry_state.attempt_number + 1
    console.print(
        f"[yellow]testflinger connection error{detail}\n"
        f"attempt {retry_state.attempt_number}/{TF_MAX_ATTEMPTS} failed, "
        f"retrying in {wait_s:.0f}s (attempt {next_attempt})[/yellow]"
    )


@retry(
    stop=stop_after_attempt(TF_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type(RetryableConnectionError),
    reraise=True,
    before_sleep=_before_sleep_log,
)
def _tf_run(
    cmd: list[str],
    check: bool = True,
    **kwargs,
) -> subprocess.CompletedProcess:
    result = run(cmd, check=check, **kwargs)
    if not check and result.returncode != 0:
        stderr = result.stderr or ""
        if is_transient_connection_error(stderr):
            console.print("[yellow]transient connection error detected, retrying...[/yellow]")
            console.print(stderr.split('\n')[-1])
            raise RetryableConnectionError(
                cmd,
                kwargs.get("log_path", "/tmp/sandbed.log"),
                result.returncode,
                stderr,
            )
    return result


def parse_duration(value: str) -> int:
    m = re.fullmatch(r"(\d+)\s*(s|m|h)?", value.strip())
    if not m:
        raise ValueError(f"invalid duration: {value!r}")
    n = int(m.group(1))
    unit = m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def _parse_job_id(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("job_id:"):
            return line.split(":", 1)[1].strip()
    candidate = stdout.strip()
    if candidate and "\n" not in candidate:
        return candidate
    raise RuntimeError(f"could not parse job_id from: {stdout!r}")


def _submit_job_yaml(yaml_content: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False, dir="."
    ) as tmp:
        tmp.write(yaml_content)
        tmp_path = tmp.name
    return _parse_job_id(
        _tf_run(["testflinger-cli", "submit", "-q", tmp_path]).stdout
    )


def _reserve(ctx: Config) -> str:
    job = {
        "job_queue": ctx.testflinger_queue,
        "provision_data": {"distro": ctx.testflinger_series},
        "reserve_data": {
            "ssh_keys": list(ctx.testflinger_key),
            "timeout": parse_duration(ctx.testflinger_timeout),
        },
    }
    return _submit_job_yaml(yaml.safe_dump(job, default_flow_style=False))


def _submit_job_file(ctx: Config) -> str:
    rendered = render(
        ctx.testflinger_job_file,
        {
            "queue": ctx.testflinger_queue,
            "series": ctx.testflinger_series,
            "timeout": parse_duration(ctx.testflinger_timeout),
        },
    )
    return _submit_job_yaml(rendered)


def _read_job_id() -> str | None:
    p = Path(JOB_ID_FILE)
    if not p.is_file():
        return None
    return p.read_text().strip() or None


def _write_job_id(job_id: str) -> None:
    Path(JOB_ID_FILE).write_text(job_id + "\n")


def _job_status(job_id: str) -> str | None:
    out = _tf_run(["testflinger-cli", "status", job_id], check=False).stdout.strip()
    return out or None

@retry(
    stop=stop_after_attempt(TF_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type(RetryableConnectionError),
    reraise=True,
    before_sleep=_before_sleep_log,
)
def _job_queue(job_id: str) -> str | None:
    cmd = ["testflinger-cli", "show", job_id]
    log_path = "/tmp/sandbed.log"
    p = _tf_run(cmd, check=False)
    out = p.stdout
    if JOB_NOT_FOUND_MSG in (p.stderr or ""):
        raise JobNotFoundError(job_id)
    try:
        return json.loads(out).get("job_queue")
    except json.JSONDecodeError:
        console.print(f"[yellow]warning: failed to parse job queue for {job_id}: {out!r}[/yellow]")
        console.print(f"[yellow]stderr: {p.stderr!r}[/yellow]")
        console.print(f"[yellow]stdout: {out}[/yellow]")
        console.print(f"[yellow]returncode: {p.returncode}[/yellow]")
        raise RetryableConnectionError(cmd, log_path, p.returncode, p.stderr)


def _warn_timeout_mismatch(ctx: Config, job_id: str) -> None:
    out = _tf_run(["testflinger-cli", "show", job_id], check=False).stdout
    try:
        reserved = json.loads(out).get("reserve_data", {}).get("timeout")
    except json.JSONDecodeError:
        return
    requested = parse_duration(ctx.testflinger_timeout)
    if reserved is not None and int(reserved) != requested:
        console.print(
            f"warning: reusing job {job_id} reserved for {reserved}s, "
            f"but --testflinger-timeout is {ctx.testflinger_timeout} ({requested}s)",
            file=sys.stderr,
        )


def _stale_reason(ctx: Config, job_id: str) -> str | None:
    """Return why a recorded job id can't be reused, or None if it can."""
    state = _job_status(job_id)
    try:
        queue = _job_queue(job_id)
    except JobNotFoundError:
        return "job not found in testflinger"
    if state not in ALIVE_STATES:
        return f"job is no longer active (state: {state or 'unknown'})"
    if queue != ctx.testflinger_queue:
        return f"job is on queue {queue!r}, requested {ctx.testflinger_queue!r}"
    _warn_timeout_mismatch(ctx, job_id)
    console.print(f"reusing testflinger job {job_id} ({state}, queue={queue})")
    return None


def allocated_job_id(ctx: Config) -> str:
    if ctx.testflinger_job:
        return ctx.testflinger_job

    if ctx.testflinger_job_file:
        job_id = _submit_job_file(ctx)
        _write_job_id(job_id)
        return job_id

    existing = _read_job_id()
    if existing:
        reason = _stale_reason(ctx, existing)
        if reason is None:
            return existing
        console.print(
            f"[yellow]warning: dismissing job id {existing} from {JOB_ID_FILE}: {reason}[/yellow]"
        )
        Path(JOB_ID_FILE).unlink(missing_ok=True)

    job_id = _reserve(ctx)
    _write_job_id(job_id)
    return job_id


def _device_ip(ctx: Config, job_id: str) -> str | None:
    out = _tf_run(["testflinger-cli", "results", job_id], check=False).stdout
    try:
        return json.loads(out).get("device_info", {}).get("device_ip")
    except json.JSONDecodeError:
        return None


def _humanize_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _status_renderable(job_id: str, state: str | None, elapsed: float) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row("Job", job_id)
    grid.add_row("State", Spinner("dots", text=state or "unknown"))
    grid.add_row("Time in state", _humanize_duration(elapsed))
    return Panel(grid, title="Testflinger", border_style="cyan")


def wait_for_device(ctx: Config, job_id: str) -> str:
    deadline = time.monotonic() + parse_duration(ctx.testflinger_timeout)
    state: str | None = None
    state_since = time.monotonic()
    next_poll = 0.0

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if now >= next_poll:
                next_poll = now + POLL_INTERVAL
                ip = _device_ip(ctx, job_id)
                if ip:
                    return ip
                new_state = _job_status(job_id)
                if new_state != state:
                    if state is not None:
                        live.console.log(
                            f"job {job_id}: {state} -> {new_state} "
                            f"(after {_humanize_duration(now - state_since)})"
                        )
                    state = new_state
                    state_since = now
            live.update(_status_renderable(job_id, state, now - state_since))
            time.sleep(0.5)
    raise TimeoutError(f"device for {job_id} not ready within {ctx.testflinger_timeout}")


def wait_for_ssh(
    host: str, timeout: int = SSH_WAIT_TIMEOUT, interval: int = SSH_WAIT_INTERVAL
) -> None:
    """Retry `ssh` to the node every `interval` seconds, for up to `timeout` seconds."""
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        result = run(
            ["ssh", *SSH_OPTS, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             f"ubuntu@{host}", "true"],
            check=False,
        )
        if result.returncode == 0:
            return
        lines = (result.stderr or "").strip().splitlines()
        reason = lines[-1] if lines else f"ssh exited {result.returncode}"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"ssh to ubuntu@{host} not accepted within {timeout}s: {reason}"
            )
        console.print(
            f"[yellow]ssh to {host} not ready (attempt {attempt}: {reason}); "
            f"retrying in {min(interval, remaining):.0f}s[/yellow]"
        )
        time.sleep(min(interval, remaining))


def scp_repo(ctx: Config, host: str) -> None:
    # .venv is excluded from the sync, so one left over from an earlier sync (when it was still
    # copied) is never refreshed and can be broken; let `uv run` rebuild it on the remote.
    run(["ssh", *SSH_OPTS, f"ubuntu@{host}", "rm -rf ~/sandbed/.venv"])
    rsync_ssh = ["ssh", *SSH_OPTS]
    excludes = [arg for pattern in RSYNC_EXCLUDES for arg in ("--exclude", pattern)]
    run(
        [
            "rsync",
            "-avr",
            *excludes,
            "-e",
            " ".join(rsync_ssh),
            ".",
            f"ubuntu@{host}:~/sandbed",
        ],
        capture_output=False,
    )


def authorize_key(ctx: Config, host: str) -> None:
    """Add the deployment public key to the node's authorized_keys (idempotent).

    The private half reaches the node with the repo sync (it lives in the working directory).
    """
    if not ctx.ssh_key:
        return
    key = shlex.quote(ctx.ssh_key)
    cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
        f"{{ grep -qxF {key} ~/.ssh/authorized_keys || echo {key} >> ~/.ssh/authorized_keys; }}"
    )
    run(["ssh", *SSH_OPTS, f"ubuntu@{host}", cmd])


def prep_host(ctx: Config, host: str) -> int:
    cmd = (
        "cd ~/sandbed && "
        f"sudo ./scripts/prep-host.sh {shlex.quote(ctx.microceph_disk_size)}"
    )
    console.print("preparing host...")
    p = run(["ssh", *SSH_OPTS, f"ubuntu@{host}", cmd], capture_output=False)
    return p.returncode


def remote_command(ctx: Config) -> str:
    value_opts = {
        "--testflinger-series",
        "--testflinger-queue",
        "--testflinger-timeout",
        "--testflinger-job",
        "--testflinger-job-file",
        "--testflinger-key",
    }
    args: list[str] = []
    i = 0
    raw = sys.argv[1:]
    while i < len(raw):
        a = raw[i]
        if a == "--use-testflinger":
            i += 1
            continue
        if a.startswith("--testflinger"):
            if "=" not in a and a in value_opts:
                i += 2
            else:
                i += 1
            continue
        args.append(a)
        i += 1
    return "cd ~/sandbed && uv run sandbed " + shlex.join(args)


def run_remote(ctx: Config, host: str) -> int:
    p = run(["ssh", *SSH_OPTS, f"ubuntu@{host}", remote_command(ctx)], capture_output=False)
    return p.returncode


def run_(ctx: Config) -> int:
    job_id = allocated_job_id(ctx)
    console.print(f"testflinger job: [bold]{job_id}[/bold]")
    host = wait_for_device(ctx, job_id)
    console.print(f"device ready: [bold green]{host}[/bold green]")
    wait_for_ssh(host)
    scp_repo(ctx, host)
    authorize_key(ctx, host)
    prep_host(ctx, host)
    return run_remote(ctx, host)
