"""`sandbed dev ...`: the additive development-lab command group."""

from __future__ import annotations

from collections.abc import Callable

import click

from . import commands
from .capabilities import capabilities
from .errors import EXIT_INTERRUPTED, DevError, Interrupted
from .results import emit, envelope, error_envelope

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def json_option(fn: Callable) -> Callable:
    return click.option("--json", "json_mode", is_flag=True, help="One JSON object on stdout.")(fn)


def execute(command: str, json_mode: bool, body: Callable[[], dict]) -> None:
    """Run `body`, print one envelope, exit with the contract's exit code.

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
        emit(envelope(command, status, **fields), json_mode=json_mode, human=human)
        if code:
            raise SystemExit(code)
    except KeyboardInterrupt:
        err: DevError = Interrupted("interrupted")
        emit(error_envelope(command, err), json_mode=json_mode)
        raise SystemExit(EXIT_INTERRUPTED) from None
    except DevError as err:
        emit(error_envelope(command, err), json_mode=json_mode)
        raise SystemExit(err.exit_code) from None


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
@click.option("--osd-size", default="100G", show_default=True)
@json_option
def host_prepare(osd_size: str, json_mode: bool) -> None:
    """Install and configure shared LXD/MicroCeph storage (never wipes a healthy backend)."""
    execute("host prepare", json_mode, lambda: commands.host_prepare(osd_size))


@dev.command("create", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--provider", default="manual", show_default=True)
@click.option("--profile", default="single-node", show_default=True)
@click.option("--vcpus", type=int)
@click.option("--memory", type=int, help="MiB")
@click.option("--disk", type=int, help="Root disk, GiB")
@click.option("--sdb-size", help="Guest storage disk, e.g. 50GiB")
@json_option
def create(lab, provider, profile, vcpus, memory, disk, sdb_size, json_mode) -> None:
    """Provision one isolated VM (does not install or bootstrap Sunbeam)."""
    overrides = {"vcpus": vcpus, "memory": memory, "disk": disk, "sdb_size": sdb_size}
    execute("create", json_mode,
            lambda: commands.create(lab, provider=provider, profile=profile, overrides=overrides))


@dev.command("build", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--source", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--include", multiple=True, help="Untracked file to include (repeatable).")
@json_option
def build(lab, source, include, json_mode) -> None:
    """Build the current source snapshot into a registered artifact."""
    execute("build", json_mode, lambda: commands.build(lab, source, include))


@dev.command("deploy", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--artifact", required=True)
@json_option
def deploy(lab, artifact, json_mode) -> None:
    """Install exactly the selected artifact in the lab VM."""
    execute("deploy", json_mode, lambda: commands.deploy(lab, artifact))


@dev.command("test", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--scenario", default="bootstrap-and-launch", show_default=True)
@json_option
def test_cmd(lab, scenario, json_mode) -> None:
    """Run the built-in scenario, then collect diagnostics."""
    execute("test", json_mode, lambda: commands.run_test(lab, scenario))


@dev.command("status", context_settings=CONTEXT_SETTINGS)
@_id_option
@json_option
def status(lab, json_mode) -> None:
    """Show lab state and recorded stage attempts."""
    execute("status", json_mode, lambda: commands.status(lab))


@dev.command("collect", context_settings=CONTEXT_SETTINGS)
@_id_option
@click.option("--run", "run_id", required=True)
@json_option
def collect(lab, run_id, json_mode) -> None:
    """Collect diagnostics again (a new attempt; the original result is untouched)."""
    execute("collect", json_mode, lambda: commands.collect(lab, run_id))


@dev.command("destroy", context_settings=CONTEXT_SETTINGS)
@_id_option
@json_option
def destroy(lab, json_mode) -> None:
    """Remove the lab's resources; results and archives are kept."""
    execute("destroy", json_mode, lambda: commands.destroy(lab))
