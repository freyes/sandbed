from __future__ import annotations

from .config import Config
from .manifest import render
from .shell import run


def _identity_opts(ctx: Config) -> list[str]:
    # Only when sandbed owns the key. The VMs are new, so their host keys are unknown.
    if not ctx.ssh_identity:
        return []
    return [
        "-i", ctx.ssh_identity, "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=/dev/null",
    ]


def _ssh(ctx: Config, target: str, user: str, script: str) -> None:
    run(["ssh", *_identity_opts(ctx), f"{user}@{target}", script])


def _snap(ctx: Config, ip: str) -> None:
    _ssh(ctx, ip, ctx.vm_user, "sudo snap install openstack")


def deploy_sunbeam(ctx: Config) -> None:
    manifest = render(
        f"manifests/{ctx.provider}.yaml.j2",
        {
            "region": ctx.region,
            "project_network": ctx.project_network,
            "db_topology": ctx.db_topology,
            "deployment_name": ctx.maas_name,
            "maas_url": ctx.maas_url,
            "maas_token": ctx.maas_token,
            "deploy_series": ctx.maas_deploy_series,
        },
    )

    node = ctx.ssh_host

    if ctx.provider == "manual":
        _snap(ctx, node)
        _ssh(ctx, node, ctx.vm_user, "sunbeam prepare-node-script --bootstrap | bash")
        _ssh(
            ctx,
            node,
            ctx.vm_user,
            f"sunbeam cluster bootstrap --role control,compute,storage --manifest {manifest}",
        )
        _ssh(ctx, node, ctx.vm_user, "sunbeam configure --accept-defaults --openrc")
    else:
        _snap(ctx, node)
        _ssh(ctx, node, ctx.vm_user, "sunbeam prepare-node-script --client | bash")
        _ssh(
            ctx,
            node,
            ctx.vm_user,
            f"sunbeam deployment add maas {ctx.maas_name} {ctx.maas_token} {ctx.maas_url}",
        )
        _ssh(ctx, node, ctx.vm_user, f"sunbeam deployment space map {ctx.maas_space}")
        _ssh(ctx, node, ctx.vm_user, "sunbeam deployment validate")
        _ssh(ctx, node, ctx.vm_user, f"sunbeam cluster bootstrap --manifest {manifest}")
        _ssh(ctx, node, ctx.vm_user, f"sunbeam cluster deploy --manifest {manifest}")
        _ssh(ctx, node, ctx.vm_user, "sunbeam configure --accept-defaults --openrc")


def sunbeam_destroy(ctx: Config) -> None:
    _ssh(ctx, ctx.ssh_host, ctx.vm_user, "sunbeam cluster destroy --yes")