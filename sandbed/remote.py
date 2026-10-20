"""Run long commands on a remote host so they survive a dropped ssh connection.

The command is started detached (`nohup`, its own session, output to a log file, exit status to a
file) in one short ssh call. The client then follows the log over ssh; if that connection drops, it
reconnects and resumes from the last complete line it showed. The exit status is read from the
file at the end.

Ctrl+C stops only the local follower. A second Ctrl+C within STOP_GRACE seconds also kills the job
on the node (its whole process group).
"""

from __future__ import annotations

import shlex
import subprocess
import time

from rich.text import Text

from .console import console
from .shell import PhaseError, run

JOBS_DIR = "~/.sandbed-jobs"
RECONNECT_TIMEOUT = 300
RECONNECT_INTERVAL = 15
CONNECTION_LOST = 255  # ssh's own failure status
STOP_GRACE = 5  # seconds in which a second Ctrl+C stops the job on the node


def _base(name: str) -> str:
    return f"{JOBS_DIR}/{name}"


def start_script(name: str, command: str) -> str:
    base = _base(name)
    # `setsid` makes the job its own process group leader, so `$$` is also the group id and the
    # whole tree can be signalled later. A subshell lets a command that calls `exit` still have
    # its status recorded.
    inner = f"echo $$ > {base}.pid; ({command}); echo $? > {base}.rc"
    return (
        f"mkdir -p {JOBS_DIR} && "
        f'if [ -f {base}.pid ] && kill -0 "$(cat {base}.pid)" 2>/dev/null; '
        "then echo attach; exit 0; fi; "
        f"rm -f {base}.pid {base}.rc {base}.log; "
        f"nohup setsid sh -c {shlex.quote(inner)} > {base}.log 2>&1 < /dev/null & "
        "echo started"
    )


def follow_script(name: str, seen_lines: int) -> str:
    base = _base(name)
    # the job writes its own pid file, so give it a moment to appear
    return (
        f"i=0; while [ ! -s {base}.pid ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i+1)); done; "
        f"tail -n +{seen_lines + 1} -f --pid=$(cat {base}.pid) {base}.log"
    )


def stop_script(name: str) -> str:
    """Kill the job's process group and record status 130. Does nothing once it has finished."""
    base = _base(name)
    return (
        f"pid=$(cat {base}.pid 2>/dev/null); "
        f'if [ -n "$pid" ] && [ ! -f {base}.rc ]; then '
        "kill -s TERM -- -$pid 2>/dev/null; sleep 2; kill -s KILL -- -$pid 2>/dev/null; "
        f"echo 130 > {base}.rc; fi; echo stopped"
    )


def _status_script(name: str) -> str:
    base = _base(name)
    return f"if [ -f {base}.rc ]; then cat {base}.rc; else echo {CONNECTION_LOST}; fi"


def _ssh_with_retry(
    ssh_base: list[str], script: str, timeout: int, interval: int
) -> subprocess.CompletedProcess:
    """Run a short ssh command, retrying while ssh itself cannot connect."""
    deadline = time.monotonic() + timeout
    while True:
        result = run([*ssh_base, script], check=False)
        if result.returncode != CONNECTION_LOST:
            if result.returncode != 0:
                raise PhaseError([*ssh_base, script], "/tmp/sandbed.log", result.returncode)
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"ssh not reachable within {timeout}s")
        reason = ((result.stderr or "").strip().splitlines() or ["connection failed"])[-1]
        console.print(f"[yellow]ssh not reachable ({reason}); retrying in {interval}s[/yellow]")
        time.sleep(min(interval, remaining))


def _follow(ssh_base: list[str], name: str, seen: int) -> tuple[int, int]:
    """Stream the job log from line `seen`. Returns (ssh exit status, lines shown so far)."""
    proc = subprocess.Popen(
        [*ssh_base, follow_script(name, seen)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        for line in proc.stdout:
            if not line.endswith("\n"):
                break  # cut off by the drop; the whole line is replayed after reconnecting
            console.print(Text.from_ansi(line), end="")
            console.file.flush()
            seen += 1
        return proc.wait(), seen
    finally:
        if proc.poll() is None:
            proc.terminate()  # only the local follower; the job on the node is left alone


def run_detached(
    ssh_base: list[str],
    name: str,
    command: str,
    timeout: int = RECONNECT_TIMEOUT,
    interval: int = RECONNECT_INTERVAL,
) -> int:
    """Run `command` on the remote host and return its exit status.

    `ssh_base` is the ssh command up to and including the target, e.g. ["ssh", ..., "u@host"].
    A job with the same `name` that is still running is attached to instead of started again.
    """
    started = _ssh_with_retry(ssh_base, start_script(name, command), timeout, interval)
    if "attach" in (started.stdout or ""):
        console.print(f"[yellow]{name} is already running on the node; attaching[/yellow]")

    try:
        return _follow_to_completion(ssh_base, name, timeout, interval)
    except KeyboardInterrupt:
        _interrupted(ssh_base, name)


def _interrupted(ssh_base: list[str], name: str) -> None:
    """A first Ctrl+C detaches; a second within STOP_GRACE seconds kills the job. Never returns."""
    console.print(
        f"\n[yellow]interrupted: {name} keeps running on the node. Press Ctrl+C again within "
        f"{STOP_GRACE}s to stop it; otherwise run the same command again to reattach, or stop it "
        f"later with:\n  ssh {ssh_base[-1]} 'kill -s TERM -- -$(cat {_base(name)}.pid)'[/yellow]"
    )
    try:
        for _ in range(STOP_GRACE):
            time.sleep(1)
    except KeyboardInterrupt:
        console.print(f"[red]stopping {name} on the node...[/red]")
        run([*ssh_base, stop_script(name)], check=False)
        console.print(f"[red]{name} stopped[/red]")
    raise SystemExit(130) from None


def _follow_to_completion(
    ssh_base: list[str], name: str, timeout: int, interval: int
) -> int:
    seen = 0
    lost_since: float | None = None
    while True:
        status, shown = _follow(ssh_base, name, seen)
        progressed = shown > seen
        seen = shown
        if status == 0:
            break
        if status != CONNECTION_LOST:
            raise PhaseError([*ssh_base, follow_script(name, seen)], "/tmp/sandbed.log", status)
        now = time.monotonic()
        if progressed or lost_since is None:
            lost_since = now
        if now - lost_since >= timeout:
            raise TimeoutError(f"lost the ssh connection and could not reconnect within {timeout}s")
        console.print(
            f"[yellow]ssh connection lost; {name} keeps running on the node, "
            f"reconnecting in {interval}s[/yellow]"
        )
        time.sleep(min(interval, timeout - (now - lost_since)))

    result = _ssh_with_retry(ssh_base, _status_script(name), timeout, interval)
    try:
        return int((result.stdout or "").strip())
    except ValueError:
        return CONNECTION_LOST
