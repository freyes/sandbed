from __future__ import annotations

import os
import re
import sys

import click

from . import sshkey, terraform, testflinger
from .config import Config, load_config
from .destroy import destroy
from .dev.cli import dev
from .hooks import run_hook
from .install import install
from .maas import setup_maas
from .preflight import PreflightError, check
from .state import PhaseState
from .sunbeam import deploy_sunbeam

PHASES = ["preflight", "install-tools", "terraform", "maas-setup", "sunbeam"]
# Phases that need the MAAS URL/token/client host, which only exist once `terraform` has run.
MAAS_RESOLVED_PHASES = ("maas-setup", "sunbeam")


def _inside_cwd(path: str) -> bool:
    return os.path.commonpath([os.getcwd(), path]) == os.getcwd()


def _timeout(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is not None and not re.fullmatch(r"\d+[mh]", value):
        raise click.BadParameter("use <N>m or <N>h, e.g. 60m or 2h")
    return value


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
@click.option("--ssh-key-file", envvar="SANDBED_SSH_KEY_FILE")
@click.option("--ssh-host")
@click.option("--maas-name")
@click.option("--maas-space")
@click.option("--maas-channel")
@click.option("--maas-vcpus", type=int)
@click.option("--maas-memory", type=int)
@click.option("--maas-disk", type=int)
@click.option("--maas-admin-password")
@click.option("--maas-deploy-series")
@click.option("--maas-image-sync-timeout", callback=_timeout)
@click.option("--maas-upstream-dns")
@click.option("--maas-parallelism", type=int)
@click.option("--lxd-https-port", type=int)
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
    sshkey.prepare(ctx)

    if ctx.use_testflinger:
        if not ctx.testflinger_queue:
            raise click.UsageError("--use-testflinger requires --testflinger-queue")
        if ctx.testflinger_job and ctx.testflinger_job_file:
            raise click.UsageError(
                "--testflinger-job and --testflinger-job-file are mutually exclusive"
            )
        if not ctx.testflinger_job and not ctx.testflinger_key:
            raise click.UsageError("allocating a node requires at least one --testflinger-key")
        if ctx.ssh_identity and not _inside_cwd(ctx.ssh_identity):
            raise click.UsageError(
                "--ssh-key-file must be inside the working directory with --use-testflinger "
                "(the directory is synced to the node, and the private key goes with it)"
            )
        sys.exit(testflinger.run_(ctx))

    if action == "plan":
        terraform.plan(ctx, provider)
        if provider == "maas":
            click.echo("note: maas-config is not planned; it needs a running MAAS")
        return

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
            if provider == "maas" and phase in MAAS_RESOLVED_PHASES:
                terraform.resolve_maas(ctx)
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


cli.add_command(dev)


def _dispatch(ctx: Config, phase: str) -> None:
    {
        "preflight": lambda: check(ctx),
        "install-tools": lambda: install(ctx),
        "terraform": lambda: terraform.apply(ctx),
        "maas-setup": lambda: setup_maas(ctx) if ctx.provider == "maas" else None,
        "sunbeam": lambda: deploy_sunbeam(ctx),
        "destroy": lambda: destroy(ctx),
    }[phase]()
