"""Run one command with a deadline, a process group and a durable log.

Separate from `sandbed.shell.run` on purpose: that function has no deadline, shares one log
file and does not persist streamed output.
"""

from __future__ import annotations

import os
import selectors
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .errors import DevError, Interrupted
from .store import ensure_private_dir

_GRACE_SECONDS = 5.0


class CommandFailed(DevError):
    def __init__(self, result: CommandResult) -> None:
        reason = "timed out" if result.timed_out else f"exit {result.returncode}"
        super().__init__(
            f"command failed ({reason}): {shlex.join(result.argv)}",
            evidence=(str(result.log_path),),
        )
        self.result = result


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    timed_out: bool
    duration: float
    log_path: Path
    tail: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _stop(proc: subprocess.Popen) -> None:
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        proc.wait()


def run_logged(
    argv: list[str],
    *,
    log_path: Path,
    timeout: float,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    stream: TextIO | None = None,
    input_text: str | None = None,
    check: bool = False,
    tail_bytes: int = 4096,
) -> CommandResult:
    """Append `$ argv` and all output to `log_path` as it arrives. `stream` (default stderr)
    mirrors it for the operator. On deadline or Ctrl+C the whole process group is stopped."""
    ensure_private_dir(log_path.parent)
    out = stream if stream is not None else sys.stderr
    started = time.monotonic()
    tail = b""
    timed_out = False
    with open(log_path, "ab", buffering=0) as log:
        os.chmod(log_path, 0o600)
        log.write(f"$ {shlex.join(argv)}\n".encode())
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            if input_text is not None:
                assert proc.stdin is not None
                payload = input_text.encode()

                def _feed() -> None:
                    try:
                        proc.stdin.write(payload)
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass

                threading.Thread(target=_feed, daemon=True).start()
            assert proc.stdout is not None
            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        break
                    if not sel.select(timeout=min(0.5, remaining)):
                        continue
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    log.write(chunk)
                    out.write(chunk.decode(errors="replace"))
                    out.flush()
                    tail = (tail + chunk)[-tail_bytes:]
                if timed_out:
                    _stop(proc)
                else:
                    try:
                        proc.wait(timeout=max(timeout - (time.monotonic() - started), 0.1))
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _stop(proc)
            except KeyboardInterrupt:
                _stop(proc)
                raise Interrupted(
                    f"interrupted while running: {shlex.join(argv)}", evidence=(str(log_path),)
                ) from None
            finally:
                sel.close()
                proc.stdout.close()
        except BaseException:
            _stop(proc)
            raise
        finally:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        if timed_out:
            log.write(f"\n[sandbed] timed out after {timeout:g}s\n".encode())
    result = CommandResult(
        argv=tuple(argv),
        returncode=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        duration=time.monotonic() - started,
        log_path=log_path,
        tail=tail.decode(errors="replace"),
    )
    if check and not result.ok:
        raise CommandFailed(result)
    return result
