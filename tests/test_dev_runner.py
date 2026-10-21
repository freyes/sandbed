from __future__ import annotations

import io
import os
import signal
import sys
import threading
import time

import pytest

from sandbed.dev.errors import Interrupted
from sandbed.dev.runner import CommandFailed, run_logged


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_output_is_logged_and_streamed(tmp_path) -> None:
    log, stream = tmp_path / "logs" / "cmd.log", io.StringIO()
    result = run_logged(py("print('hello')"), log_path=log, timeout=10, stream=stream)
    assert result.ok
    assert "hello" in log.read_text()
    assert log.read_text().startswith("$ ")
    assert "hello" in stream.getvalue()
    assert "hello" in result.tail


def test_failure_is_reported_and_check_raises(tmp_path) -> None:
    log = tmp_path / "c.log"
    result = run_logged(
        py("import sys; sys.exit(3)"), log_path=log, timeout=10, stream=io.StringIO()
    )
    assert (result.returncode, result.ok) == (3, False)
    with pytest.raises(CommandFailed) as exc:
        run_logged(
            py("import sys; sys.exit(3)"), log_path=log, timeout=10, stream=io.StringIO(),
            check=True,
        )
    assert str(log) in exc.value.evidence


def test_deadline_kills_the_whole_process_group(tmp_path) -> None:
    marker = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    result = run_logged(py(code), log_path=tmp_path / "c.log", timeout=1.0, stream=io.StringIO())
    assert result.timed_out and not result.ok
    assert time.monotonic() - started < 15
    child = int(marker.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("grandchild survived the deadline")
    assert "timed out" in (tmp_path / "c.log").read_text()


def test_interrupt_stops_the_command_and_raises_interrupted(tmp_path) -> None:
    def interrupt_soon() -> None:
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=interrupt_soon, daemon=True).start()
    with pytest.raises(Interrupted) as exc:
        run_logged(
            py("import time; time.sleep(60)"), log_path=tmp_path / "c.log", timeout=60,
            stream=io.StringIO(),
        )
    assert exc.value.exit_code == 130


def test_stdin_is_delivered(tmp_path) -> None:
    result = run_logged(
        py("import sys; print(sys.stdin.read().upper())"), log_path=tmp_path / "c.log",
        timeout=10, stream=io.StringIO(), input_text="abc",
    )
    assert "ABC" in result.tail
