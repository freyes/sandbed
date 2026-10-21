"""`sandbed dev ...`: the additive development-lab command group."""

from __future__ import annotations

from collections.abc import Callable

import click

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
