"""Detached-job worker: runs one job's command inside its systemd unit and records the result.

`python -m sandbed.dev.worker --job-id <id>` is the unit's main process. `--finalize` is the
unit's `ExecStopPost`: it records how the unit ended and, when the worker never published
`result.json` (killed, timed out, OOM), synthesizes a failed envelope. Nothing here prints the
envelope; it only goes to `result.json`.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Callable

from . import cli, commands, jobs
from .errors import InputError, InternalError
from .results import error_envelope
from .state import now
from .store import atomic_write_json

JOB_COMMANDS: dict[str, Callable[[dict], dict]] = {
    "host prepare": lambda p: commands.host_prepare(
        p["osd_size"], p["storage_backend"], p["storage_source"]),
    "create": lambda p: commands.create(
        p["lab"], provider=p["provider"], profile=p["profile"], overrides=p["overrides"]),
    "build": lambda p: commands.build(p["lab"], p["source"], tuple(p["include"])),
    "deploy": lambda p: commands.deploy(p["lab"], p["artifact"]),
    "test": lambda p: commands.run_test(p["lab"], p["scenario"]),
    "collect": lambda p: commands.collect(p["lab"], p["run_id"]),
    "destroy": lambda p: commands.destroy(p["lab"]),
}

_FINALIZE_MESSAGES = {
    "timeout": "worker exceeded RuntimeMaxSec",
    "oom-kill": "worker was OOM-killed",
    "signal": "worker was killed by a signal",
    "core-dump": "worker was killed by a signal",
}


def _raise_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def run_worker(job: jobs.Job) -> int:
    jobs.mark_started(job, os.getpid())
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())  # query only
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)

    def body() -> dict:
        fields = JOB_COMMANDS[job.command](job.params)
        # The command finished: from here a cancel must not turn it into a lost result.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        return fields

    try:
        outcome = cli.build_outcome(job.command, body)
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})  # also the interrupted path
        jobs.write_result(job, outcome.payload, outcome.human, outcome.code)
        return outcome.code
    finally:
        # Ignore before unblocking so a deferred SIGTERM is discarded, not delivered.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        signal.signal(signal.SIGTERM, previous)


def finalize(job: jobs.Job) -> int:
    service_result = os.environ.get("SERVICE_RESULT", "")
    message = _FINALIZE_MESSAGES.get(service_result, "worker exited without a result")
    label = None if job.lab_ref == jobs.HOST_LAB_REF else job.lab_ref
    err = InternalError(message, lab_id=job.lab_id, label=label)
    try:
        jobs.write_result(job, error_envelope(job.command, err), "", err.exit_code)
    except OSError as exc:
        print(f"error: could not write result.json: {exc}", file=sys.stderr)
    try:
        atomic_write_json(job.paths.exit, {
            "service_result": service_result,
            "exit_kind": os.environ.get("EXIT_CODE", ""),
            "exit_status": os.environ.get("EXIT_STATUS", ""),
            "finished_at": now(),
        })
    except OSError as exc:
        print(f"error: could not write exit.json: {exc}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sandbed.dev.worker")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args(argv)
    try:
        job = jobs.load_job(args.job_id)
    except InputError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return finalize(job) if args.finalize else run_worker(job)


if __name__ == "__main__":
    raise SystemExit(main())
