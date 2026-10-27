"""`sandbed dev ...`: the additive development-lab command group."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import click

from . import commands, jobcmds, jobs, labs, runjob
from .capabilities import capabilities
from .errors import EXIT_INTERRUPTED, DevError, InputError, InternalError, Interrupted
from .results import Outcome, emit, envelope, error_envelope

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def json_option(fn: Callable) -> Callable:
    return click.option("--json", "json_mode", is_flag=True, help="One JSON object on stdout.")(fn)


def build_outcome(command: str, body: Callable[[], dict]) -> Outcome:
    """Run `body` and return the envelope, human line and exit code without printing.

    `body` returns the extra envelope fields (it may set `status`, `human`, and a nonzero
    `exit_code` for a failure it already folded into a full result payload). Any DevError becomes a
    failed or interrupted envelope; Ctrl+C becomes exit 130 without claiming remote work
    stopped.
    """
    try:
        fields = body()
        status = fields.pop("status", "succeeded")
        human = str(fields.pop("human", ""))
        code = int(fields.pop("exit_code", 0))
        return Outcome(envelope(command, status, **fields), human, code)
    except KeyboardInterrupt:
        err: DevError = Interrupted("interrupted")
        return Outcome(error_envelope(command, err), "", EXIT_INTERRUPTED)
    except DevError as err:
        return Outcome(error_envelope(command, err), "", err.exit_code)
    except Exception as exc:
        err = InternalError(f"{type(exc).__name__}: {exc}")
        return Outcome(error_envelope(command, err), "", err.exit_code)


def execute(command: str, json_mode: bool, body: Callable[[], dict]) -> None:
    """Run `body`, print one envelope, exit with the contract's exit code."""
    outcome = build_outcome(command, body)
    emit(outcome.payload, json_mode=json_mode, human=outcome.human)
    if outcome.code:
        raise SystemExit(outcome.code)


def detach_option(fn: Callable) -> Callable:
    return click.option(
        "--detach", is_flag=True,
        help="Start the command as a background systemd job and return immediately; "
        "follow it with `sandbed dev job`.",
    )(fn)


def run_job(command: str, json_mode: bool, detach: bool, params: dict, lab_ref: str,
            validate: Callable[[], None]) -> None:
    """Validate cheaply, then run the command as a systemd job (blocking or detached)."""
    blocking: list[Outcome] = []

    def body() -> dict:
        validate()
        res = runjob.launch(command, params, lab_ref, detach=detach)
        if isinstance(res, runjob.Launched):
            return dict(res.fields)
        blocking.append(res)
        return {}

    built = build_outcome(command, body)
    outcome = blocking[0] if blocking else built
    emit(outcome.payload, json_mode=json_mode, human=outcome.human)
    if outcome.code:
        raise SystemExit(outcome.code)


def _validate_scenario(scenario: str) -> None:
    if scenario not in commands.SUPPORTED_SCENARIOS:
        raise InputError(
            f"unknown scenario {scenario!r}; supported: {list(commands.SUPPORTED_SCENARIOS)}")


def _validate_lab_ref(lab: str) -> None:
    """Existing-lab commands take a label or a canonical lab UUID (only `create` mints labels)."""
    try:
        labs.validate_label(lab)
        return
    except InputError:
        pass
    try:
        if str(uuid.UUID(lab)) == lab.lower():
            return
    except ValueError:
        pass
    raise InputError(
        f"invalid lab id {lab!r}: use a label (1-31 chars of a-z, 0-9 and '-') or a lab UUID")


def _validate_create(lab: str, provider: str, profile: str) -> None:
    labs.validate_label(lab)
    if (provider, profile) != commands.SUPPORTED_PROVIDER_PROFILE:
        raise InputError("milestone 1 supports only --provider manual --profile single-node")


def _reject_testflinger(_ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if value:
        raise click.UsageError("--use-testflinger is not supported by `sandbed dev` (milestone 1)")


@click.group("dev", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--use-testflinger", is_flag=True, hidden=True, expose_value=False,
    callback=_reject_testflinger,
)
def dev() -> None:
    """Isolated snap-openstack development labs (manual provider, one VM)."""


@dev.command("capabilities", context_settings=CONTEXT_SETTINGS)
@json_option
def capabilities_cmd(json_mode: bool) -> None:
    """Report the supported interface version, commands, profiles and scenarios."""
    execute("capabilities", json_mode, lambda: {"human": "ok", **capabilities(dev)})


def _id_option(fn: Callable) -> Callable:
    return click.option("--id", "lab", required=True, help="Lab id (label or UUID).")(fn)


@dev.group("host", context_settings=CONTEXT_SETTINGS)
def host() -> None:
    """Check or prepare the shared host prerequisites."""


@host.command("check", context_settings=CONTEXT_SETTINGS)
@json_option
def host_check(json_mode: bool) -> None:
    """Read-only readiness check."""
    execute("host check", json_mode, commands.host_check)


@host.command("prepare", context_settings=CONTEXT_SETTINGS)
@click.option("--osd-size", default="100G", show_default=True, help="microceph backend only")
@click.option(
    "--storage-backend", type=click.Choice(["auto", "microceph", "dir"]), default="auto",
    show_default=True,
)
@click.option(
    "--storage-source", type=click.Path(file_okay=False), default=None,
    help="dir backend: existing empty directory for the pool",
)
@detach_option
@json_option
def host_prepare(osd_size: str, storage_backend: str, storage_source: str | None,
                 detach: bool, json_mode: bool) -> None:
    """Install and configure the shared LXD storage pool (MicroCeph or dir; never wipes a
    healthy backend)."""
    params = {"osd_size": osd_size, "storage_backend": storage_backend,
              "storage_source": storage_source}
    run_job("host prepare", json_mode, detach, params, jobs.HOST_LAB_REF, lambda: None)


@dev.command("create", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--provider", default="manual", show_default=True)
@click.option("--profile", default="single-node", show_default=True)
@click.option("--vcpus", type=int)
@click.option("--memory", type=int, help="MiB")
@click.option("--disk", type=int, help="Root disk, GiB")
@click.option("--sdb-size", help="Guest storage disk, e.g. 50GiB")
@detach_option
@json_option
def create(lab, provider, profile, vcpus, memory, disk, sdb_size, detach, json_mode) -> None:
    """Provision one isolated VM (does not install or bootstrap Sunbeam)."""
    overrides = {"vcpus": vcpus, "memory": memory, "disk": disk, "sdb_size": sdb_size}
    params = {"lab": lab, "provider": provider, "profile": profile, "overrides": overrides}
    run_job("create", json_mode, detach, params, lab,
            lambda: _validate_create(lab, provider, profile))


@dev.command("build", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--source", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--include", multiple=True, help="Untracked file to include (repeatable).")
@detach_option
@json_option
def build(lab, source, include, detach, json_mode) -> None:
    """Build the current source snapshot into a registered artifact."""
    params = {"lab": lab, "source": str(Path(source).resolve()), "include": list(include)}
    run_job("build", json_mode, detach, params, lab, lambda: _validate_lab_ref(lab))


@dev.command("deploy", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--artifact", required=True)
@detach_option
@json_option
def deploy(lab, artifact, detach, json_mode) -> None:
    """Install exactly the selected artifact in the lab VM."""
    run_job("deploy", json_mode, detach, {"lab": lab, "artifact": artifact}, lab,
            lambda: _validate_lab_ref(lab))


@dev.command("test", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--scenario", default="bootstrap-and-launch", show_default=True)
@detach_option
@json_option
def test_cmd(lab, scenario, detach, json_mode) -> None:
    """Run the built-in scenario, then collect diagnostics."""
    def validate() -> None:
        _validate_lab_ref(lab)
        _validate_scenario(scenario)

    run_job("test", json_mode, detach, {"lab": lab, "scenario": scenario}, lab, validate)


@dev.command("status", context_settings=CONTEXT_SETTINGS)
@_id_option
@json_option
def status(lab, json_mode) -> None:
    """Show lab state and recorded stage attempts."""
    execute("status", json_mode, lambda: commands.status(lab))


@dev.command("collect", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--run", "run_id", required=True)
@detach_option
@json_option
def collect(lab, run_id, detach, json_mode) -> None:
    """Collect diagnostics again (a new attempt; the original result is untouched)."""
    run_job("collect", json_mode, detach, {"lab": lab, "run_id": run_id}, lab,
            lambda: _validate_lab_ref(lab))


@dev.command("destroy", context_settings=CONTEXT_SETTINGS)
@_id_option
@detach_option
@json_option
def destroy(lab, detach, json_mode) -> None:
    """Remove the lab's resources; results and archives are kept."""
    run_job("destroy", json_mode, detach, {"lab": lab}, lab, lambda: _validate_lab_ref(lab))


@dev.group("job", context_settings=CONTEXT_SETTINGS)
def job() -> None:
    """Inspect and control detached jobs."""


def _job_option(fn: Callable) -> Callable:
    return click.option("--job", "job_id", required=True, help="Job id.")(fn)


@job.command("status", context_settings=CONTEXT_SETTINGS)
@_job_option
@json_option
def job_status(job_id: str, json_mode: bool) -> None:
    """Show a job's state, progress and stderr tail."""
    execute("job status", json_mode, lambda: jobcmds.job_status(job_id))


@job.command("logs", context_settings=CONTEXT_SETTINGS)
@_job_option
@click.option("--stream", type=click.Choice(["stderr", "stdout"]), default="stderr",
              show_default=True, help="Which log to read.")
@click.option("--offset", type=int, default=0, show_default=True, help="Byte offset to start at.")
@click.option("--max-bytes", type=int, default=None, help="Maximum bytes to return.")
@json_option
def job_logs(job_id: str, stream: str, offset: int, max_bytes: int | None,
             json_mode: bool) -> None:
    """Read a job log from a byte offset."""
    execute("job logs", json_mode, lambda: jobcmds.job_logs(job_id, stream, offset, max_bytes))


@job.command("wait", context_settings=CONTEXT_SETTINGS)
@_job_option
@click.option("--timeout", type=int, default=300, show_default=True,
              help="Seconds to wait before returning.")
@json_option
def job_wait(job_id: str, timeout: int, json_mode: bool) -> None:
    """Wait for a job to finish or the timeout to pass."""
    execute("job wait", json_mode, lambda: jobcmds.job_wait(job_id, timeout))


@job.command("cancel", context_settings=CONTEXT_SETTINGS)
@_job_option
@json_option
def job_cancel(job_id: str, json_mode: bool) -> None:
    """Stop a running job."""
    execute("job cancel", json_mode, lambda: jobcmds.job_cancel(job_id))


@job.command("list", context_settings=CONTEXT_SETTINGS)
@click.option("--active", is_flag=True, help="Only jobs that have not finished.")
@click.option("--lab", default=None, help="Only jobs for this lab.")
@click.option("--limit", type=int, default=20, show_default=True, help="Maximum jobs to list.")
@json_option
def job_list(active: bool, lab: str | None, limit: int, json_mode: bool) -> None:
    """List recent jobs, newest first."""
    execute("job list", json_mode, lambda: jobcmds.job_list(active, lab, limit))
