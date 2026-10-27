"""Test double for `python -m sandbed.dev.worker`: every job command is replaced by a fake.

Run it exactly like the worker: `python tests/fixtures/worker_wrapper.py --job-id ID` (main) or
`... --finalize --job-id ID`. The fake is chosen by env `SANDBED_E2E_FAKE` (the `SANDBED_` prefix
is forwarded into a job's unit): ok | fail-host | slow-with-stage | hang-until-sigterm |
crash-sigkill. `SANDBED_E2E_STATE` is a directory the fakes may write evidence into.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from sandbed.dev import runner, store, worker
from sandbed.dev.errors import HostError
from sandbed.dev.labs import Registry
from sandbed.dev.paths import lab_paths
from sandbed.dev.state import SUCCEEDED, AttemptLog

SLOW_SECONDS = 5

# Appends one line to child.signals per SIGTERM, exits one second after the first one.
CHILD_CODE = """
import os, signal, sys, time
state = sys.argv[1]
def on_term(signum, frame):
    with open(os.path.join(state, "child.signals"), "a") as fh:
        fh.write("SIGTERM\\n")
    on_term.deadline = time.monotonic() + 1.0
on_term.deadline = None
signal.signal(signal.SIGTERM, on_term)
with open(os.path.join(state, "child.ready"), "w") as fh:
    fh.write(str(os.getpid()))
limit = time.monotonic() + 120
while time.monotonic() < limit:
    if on_term.deadline is not None and time.monotonic() >= on_term.deadline:
        break
    time.sleep(0.05)
"""


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def fake_ok(_params: dict) -> dict:
    _say("fake: stage one")
    _say("fake: stage two")
    _say(f"fake: proxy={os.environ.get('HTTPS_PROXY')}")
    return {"human": "done", "x": 1}


def fake_fail_host(_params: dict) -> dict:
    _say("fake: about to fail")
    raise HostError("fake host failure")


def fake_slow_with_stage(params: dict) -> dict:
    paths = lab_paths(Registry().find(params["lab"], os.getuid()).lab_uuid)
    log = AttemptLog(paths.attempts)
    with store.exclusive_lock(paths.lock):
        attempt = log.begin("run-e2e", "scenario.bootstrap", "")
        for second in range(SLOW_SECONDS):
            _say(f"fake: progress {second}")
            time.sleep(1)
        log.finish(attempt, SUCCEEDED, exit_code=0)
    return {"human": "slow done", "x": 2}


def fake_hang_until_sigterm(_params: dict) -> dict:
    state = os.environ["SANDBED_E2E_STATE"]
    _say("fake: starting child")
    runner.run_logged(
        [sys.executable, "-c", CHILD_CODE, state],
        log_path=Path(state) / "child.log", timeout=300, stream=sys.stderr,
    )
    return {"human": "child ended without a signal"}


def fake_crash_sigkill(_params: dict) -> dict:
    _say("fake: crashing")
    os.kill(os.getpid(), signal.SIGKILL)
    time.sleep(60)
    return {}


FAKES = {
    "ok": fake_ok,
    "fail-host": fake_fail_host,
    "slow-with-stage": fake_slow_with_stage,
    "hang-until-sigterm": fake_hang_until_sigterm,
    "crash-sigkill": fake_crash_sigkill,
}


def install(mode: str) -> None:
    for command in worker.JOB_COMMANDS:
        worker.JOB_COMMANDS[command] = FAKES[mode]


if __name__ == "__main__":
    install(os.environ.get("SANDBED_E2E_FAKE", "ok"))
    raise SystemExit(worker.main())
