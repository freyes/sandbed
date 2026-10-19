from __future__ import annotations

import subprocess

from rich.text import Text

from .console import console

_TRANSIENT_MARKERS = (
    "ConnectionError",
    "Max retries exceeded",
    "Network is unreachable",
    "Errno 101",
)


def is_transient_connection_error(stderr: str) -> bool:
    return any(marker in stderr for marker in _TRANSIENT_MARKERS)


class PhaseError(RuntimeError):
    def __init__(
        self, cmd: list[str], log_path: str, returncode: int, stderr: str = ""
    ) -> None:
        self.cmd = cmd
        self.log_path = log_path
        self.returncode = returncode
        self.stderr = stderr
        detail = f"\n{stderr.strip()}" if stderr and stderr.strip() else ""
        super().__init__(
            f"command failed (exit {returncode}): {' '.join(cmd)}{detail}\n"
            f"see log: {log_path}"
        )


class RetryableConnectionError(PhaseError):
    def __init__(
        self, cmd: list[str], log_path: str, returncode: int, stderr: str
    ) -> None:
        super().__init__(cmd, log_path, returncode, stderr)


def run(
    cmd: list[str],
    check: bool = True,
    env: dict | None = None,
    cwd: str | None = None,
    log_path: str = "/tmp/sandbed.log",
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    if capture_output:
        result = subprocess.run(cmd, env=env, cwd=cwd, capture_output=capture_output, text=True)
        if check and result.returncode != 0:
            stderr = result.stderr or ""
            with open(log_path, "a") as fh:
                fh.write("$ " + " ".join(cmd) + "\n")
                fh.write(result.stdout or "")
                fh.write(stderr)
            if is_transient_connection_error(stderr):
                raise RetryableConnectionError(cmd, log_path, result.returncode, stderr)
            raise PhaseError(cmd, log_path, result.returncode, stderr)
    else:
        result = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in result.stdout:
            console.print(Text.from_ansi(line), end="")
            console.file.flush()

        result.wait()

        if check and result.returncode != 0:
            raise PhaseError(cmd, log_path, result.returncode)
    return result