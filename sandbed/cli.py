from __future__ import annotations

import sys

import click

from . import terraform, testflinger
from .config import Config, load_config
from .destroy import destroy
from .hooks import run_hook
from .install import install
from .maas import setup_maas
from .preflight import PreflightError, check
from .state import PhaseState
from .sunbeam import deploy_sunbeam

PHASES = ["preflight", "install-tools", "terraform", "maas-setup", "sunbeam"]


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
def cli() -> None:
    """Sandbed: disposable sandbox environments."""


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.argument("provider", type=click.Choice(["manual", "maas"]))
@click.argument("action", type=click.Choice(["up", "plan", "destroy", "sunbeam"]))
@click.option("--mgmt-cidr")
@click.option("--external-cidr")
@click.option("--generic-cidr")
@click.option("--vcpus", type=int)
@click.option("--memory", type=int)
@click.option("--disk", type=int)
@click.option("--microceph-osd-img")
@click.option("--microceph-osd-size", type=int)
@click.option("--microceph-disk-size", default="20G")
@click.option("--obs-count", type=int)
@click.option("--landscape-count", type=int)
@click.option("--shared-dir")
@click.option("--ssh-key", envvar="SUNBEAM_SSH_KEY")
@click.option("--ssh-host")
@click.option("--maas-name")
@click.option("--maas-url")
@click.option("--maas-token")
@click.option("--maas-space")
@click.option("--hooks-dir", default="hooks")
@click.option("--no-hooks", is_flag=True)
@click.option("--cloud-init-templates-dir")
@click.option("--use-testflinger", is_flag=True)
@click.option("--testflinger-series", default="resolute")
@click.option("--testflinger-queue")
@click.option("--testflinger-timeout", default="6h")
@click.option("--testflinger-job")
@click.option("--testflinger-job-file")
@click.option("--testflinger-key", multiple=True)
def sunbeam(provider: str, action: str, **flags) -> None:
    """Deploy Canonical OpenStack (Sunbeam) on LXD virtual machines."""
    ctx = load_config(provider, action, **{k: v for k, v in flags.items() if v is not None})
    state = PhaseState(ctx.state_file)

    if ctx.use_testflinger:
        if not ctx.testflinger_queue:
            raise click.UsageError("--use-testflinger requires --testflinger-queue")
        if ctx.testflinger_job and ctx.testflinger_job_file:
            raise click.UsageError(
                "--testflinger-job and --testflinger-job-file are mutually exclusive"
            )
        if not ctx.testflinger_job and not ctx.testflinger_key:
            raise click.UsageError("allocating a node requires at least one --testflinger-key")
        sys.exit(testflinger.run_(ctx))

    if action == "plan":
        terraform.plan(ctx, provider)
        return

    if action == "up" and provider == "maas" and not (ctx.maas_url and ctx.maas_token):
        raise click.UsageError("maas provider requires --maas-url and --maas-token")

    if action == "up":
        steps = PHASES
    elif action == "sunbeam":
        steps = ["sunbeam"]
    else:
        steps = ["destroy"]

    if not ctx.no_hooks:
        run_hook("pre-run", ctx)

    try:
        for phase in steps:
            if state.is_done(phase):
                click.echo(f"skip {phase} (already done)")
                continue
            if not ctx.no_hooks:
                run_hook(f"{phase}.pre", ctx)
            try:
                _dispatch(ctx, phase)
            except Exception as exc:
                if not ctx.no_hooks:
                    run_hook("on-fail", ctx, phase=phase, feed_stderr=True)
                if isinstance(exc, PreflightError):
                    raise click.ClickException(f"preflight failed: {exc}") from exc
                raise
            if not ctx.no_hooks:
                run_hook(f"{phase}.post", ctx)
            state.mark_done(phase)
            click.echo(f"done {phase}")
    finally:
        if not ctx.no_hooks:
            run_hook("post-run", ctx)


def _dispatch(ctx: Config, phase: str) -> None:
    {
        "preflight": lambda: check(ctx),
        "install-tools": lambda: install(ctx),
        "terraform": lambda: terraform.apply(ctx),
        "maas-setup": lambda: setup_maas(ctx) if ctx.provider == "maas" else None,
        "sunbeam": lambda: deploy_sunbeam(ctx),
        "destroy": lambda: destroy(ctx),
    }[phase]()
